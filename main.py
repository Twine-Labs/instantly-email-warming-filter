"""
This script will remove all warming emails from your inbox.
"""

import argparse
import base64
import json
import os
import re
import time
from typing import Any, Dict, List
from tqdm.auto import tqdm
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "openid",
]

## CHANGE THIS TO YOUR OWN TAG
TWINE_TAG = "YCEWFAF"



FILE_PATH = "credentials.json"

API_QUOTA_LIMIT_PER_SECOND = 250


def service_factory():
    """
    Creates and returns a Gmail service object using the provided credentials.

    Returns:
        A Gmail service object.
    """
    credentials = Credentials.from_authorized_user_file(FILE_PATH, SCOPES)
    return build("gmail", "v1", credentials=credentials)


def get_ids_to_update(messages: List[Dict[str, str]]) -> List[str]:
    """
    Returns a list of message IDs that should be removed based on the result of the `check_if_message_is_warming` function.

    Args:
        messages: A list of dictionaries representing messages, where each dictionary contains an "id" key.

    Returns:
        A list of message IDs that should be removed.
    """

    all_ids = check_messages([message["id"] for message in messages])
    ids_to_remove = [message_id for message_id in all_ids if message_id is not None]
    return ids_to_remove


def update_labels(message_ids: List[str], warming_label_id: str):
    """
    Updates the labels of the specified messages. Removes the "INBOX" label and adds
    the "Warming" label.

    Args:
        message_ids (List[str]): A list of message IDs.

    Returns:
        None
    """
    service = service_factory()
    for i in range(0, len(message_ids), 1000):
        # pylint: disable=maybe-no-member
        service.users().messages().batchModify(
            userId="me",
            body={
                "ids": message_ids[i : i + 1000],
                "removeLabelIds": ["INBOX"],
                "addLabelIds": [warming_label_id],
            },
        ).execute()


def add_warming_label_if_not_present() -> str:
    """
    Adds a 'Warming' label to the user's email account if it is not already present.

    This function checks if the 'Warming' label exists in the user's account. If it does not exist,
    it creates the label with the specified visibility settings.

    Returns:
        str: The ID of the 'Warming' label.
    """
    service = service_factory()
    # pylint: disable=maybe-no-member
    result = service.users().labels().list(userId="me").execute()  # 1 quota unit
    labels = result.get("labels", [])
    warming_label = next(
        (label for label in labels if label["name"] == "Warming"), None
    )
    if not warming_label:
        print("Creating Warming label...")
        # pylint: disable=maybe-no-member
        res = (
            service.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": "Warming",
                    "labelListVisibility": "labelHide",
                    "messageListVisibility": "show",
                },
            )
            .execute()
        )
        print("Created Warming label...")
        label_id = res["id"]
        return label_id
    print("Warming label already exists...")
    return warming_label["id"]


def process_historical_messages(warming_label_id: str):
    """
    Process historical messages by retrieving all messages for the last 90 days,
    removing unwanted messages, and updating labels.

    Returns:
        None
    """
    # Get all messages for the last 90 days
    service = service_factory()
    after_date = time.strftime(
        "%Y/%m/%d", time.localtime(time.time() - 120 * 24 * 60 * 60)
    )
    with tqdm() as pbar:
        # pylint: disable=maybe-no-member
        result = (
            service.users()
            .messages()
            .list(
                maxResults=500,
                userId="me",
                q=f"after:{after_date}",
            )
            .execute()
        )  # 5 quota units
        messages = result.get("messages", [])
        ids_to_update = get_ids_to_update(messages)
        update_labels(ids_to_update, warming_label_id)
        pbar.update(len(messages))
        while "nextPageToken" in result:
            # pylint: disable=maybe-no-member
            result = (
                service.users()
                .messages()
                .list(
                    maxResults=500,
                    userId="me",
                    q=f"after:{after_date}",
                    pageToken=result["nextPageToken"],
                )
                .execute()
            )  # 5 quota units
            messages = result.get("messages", [])
            ids_to_update = get_ids_to_update(messages)
            update_labels(ids_to_update, warming_label_id)
            pbar.update(len(messages))
        print("Finished processing historical messages...")


def check_body_for_warming(parts: List[Dict[str, str]], tag: str) -> bool:
    """
    Checks if the body of a message contains a warming tag.

    Args:
        parts (List[Dict[str, str]]): A list of dictionaries representing parts of a message.
        tag (str): The warming tag to check for.

    Returns:
        bool: True if the message contains a warming tag, False otherwise.
    """
    for part in parts:
        if part["mimeType"] in ["text/plain", "text/html"]:
            data = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8")
            if tag in data:
                return True
    return False


def check_messages(message_ids: List[str]) -> List[str]:
    """
    Checks if the specified messages are warming messages.

    Args:
        message_ids (List[str]): A list of message IDs.

    Returns:
        List[str]: A list of message IDs that should be removed.
    """
    service = service_factory()
    # pylint: disable=maybe-no-member
    batch = service.new_batch_http_request()
    max_batch_len = 30
    max_requests_per_second = 1.3
    results = []
    start_time = time.time()
    for i, message_id in enumerate(message_ids):
        batch.add(
            # pylint: disable=maybe-no-member
            service.users().messages().get(userId="me", id=message_id),
            callback=lambda request_id, response, exception: check_if_message_is_warming(
                response, results, exception
            ),
        )
        if i % max_batch_len == 0:
            batch.execute()
            # pylint: disable=maybe-no-member
            batch = service.new_batch_http_request()
            end_time = time.time()
            if end_time - start_time < 1 / max_requests_per_second:
                time.sleep(1 / max_requests_per_second - (end_time - start_time))
            start_time = time.time()
    if len(message_ids) % max_batch_len != 0:
        batch.execute()
    return results


def check_if_message_is_warming(
    message: Dict[str, Any], results: List[str], exception: Any
) -> None:
    """
    Checks if a message is a warming message based on its subject header.

    Args:
        message_id (str): The ID of the message to check.
        results (List[str]): A list of message IDs that should be removed.

    Returns:
        None
    """
    if exception is not None:
        print("Warning: ", exception)
        return
    message_id = message["id"]
    headers = message["payload"]["headers"]
    for header in headers:
        if header["name"] == "Subject":
            if TWINE_TAG in (header["value"]):
                results.append(message_id)
                return
            return


def google_login():
    """
    Authenticates the user with Google using OAuth 2.0 and returns the credentials.

    Returns:
        google.oauth2.credentials.Credentials: The authenticated Google credentials.
    """
    flow = InstalledAppFlow.from_client_secrets_file(
        "client_secret.json",
        scopes=SCOPES,
    )
    google_credentials = flow.run_local_server()
    return google_credentials


def main():
    """
    Main function that performs email warming filter.

    This function checks for the existence of 'client_secret.json' file and 'credentials.json' file.
    If 'client_secret.json' file is not found, it prompts the user to download it from Google.
    If 'credentials.json' file is not found, it logs in and creates the file using Google
        credentials.
        Once credentials are created, it is stored and historical messages are processed.
    If 'credentials.json' file is found, it skips the login
        process.
    It continuously retrieves emails from the user's inbox, clears warming emails, and sleeps for an hour.
    """

    parser = argparse.ArgumentParser()
    parser.add_argument("-f", "--force_historical", default=False, action="store_true")
    args = parser.parse_args()

    client_secret_file = os.path.exists("client_secret.json")
    if not client_secret_file:
        print("No client_secret.json file found, please download it from google")
        return
    has_credentials = os.path.exists(FILE_PATH)
    label_id = None
    if not has_credentials:
        print("No credentials.json file, logging in...")
        google_credentials = google_login()
        with open(FILE_PATH, "w", encoding="utf-8") as f:
            json_credentials = google_credentials.to_json()

            json.dump(json.loads(json_credentials), f)
        label_id = add_warming_label_if_not_present()
        process_historical_messages(label_id)
    elif args.force_historical:
        print("Found credentials.json file, processing historical messages...")
        label_id = add_warming_label_if_not_present()
        process_historical_messages(label_id)
    else:
        print("Found credentials.json file, skipping login...")
        label_id = add_warming_label_if_not_present()
    service = service_factory()
    while True:
        start_time = time.time()
        # pylint: disable=maybe-no-member
        result = (
            service.users().messages().list(userId="me", labelIds=["INBOX"]).execute()
        )  # 5 quota units

        messages = result.get("messages", [])

        ids_to_remove = get_ids_to_update(messages)
        update_labels(ids_to_remove, label_id)
        end_time = time.time()
        print(
            f"cleared {len(ids_to_remove)} warming emails in {end_time - start_time:.2f} seconds"
        )
        time.sleep(3600 - (end_time - start_time))


if __name__ == "__main__":
    main()
