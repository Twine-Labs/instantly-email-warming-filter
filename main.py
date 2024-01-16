import re
import json
import os
import time
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from multiprocessing.pool import ThreadPool


SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "openid",
]

INSTANTLY_PATTERN = r".* \| [A-Z0-9]{7} [A-Z0-9]{7}$"
regex = re.compile(INSTANTLY_PATTERN)


file_path = "credentials.json"


def process_message(message_id: str):
    credentials = Credentials.from_authorized_user_file(file_path, SCOPES)

    service = build("gmail", "v1", credentials=credentials)

    # pylint: disable=maybe-no-member
    message = service.users().messages().get(userId="me", id=message_id).execute()

    headers = message["payload"]["headers"]
    for header in headers:
        if header["name"] == "Subject":
            if regex.match(header["value"]):
                return message_id
            else:
                return None


def google_login():
    flow = InstalledAppFlow.from_client_secrets_file(
        "client_secret.json",
        scopes=SCOPES,
    )
    print(flow)
    google_credentials = flow.run_local_server()
    print(google_credentials)
    return google_credentials


def main():
    client_secret_file = os.path.exists("client_secret.json")
    if not client_secret_file:
        print("No client_secret.json file found, please download it from google")
        return
    has_credentials = os.path.exists(file_path)
    if not has_credentials:
        print("No credentials.json file, logging in...")
        google_credentials = google_login()
        with open(file_path, "w", encoding="utf-8") as f:
            json_credentials = google_credentials.to_json()

            json.dump(json.loads(json_credentials), f)
    else:
        print("Found credentials.json file, skipping login...")
    google_credentials = Credentials.from_authorized_user_file(file_path, scopes=SCOPES)
    service = build(
        "gmail",
        "v1",
        credentials=google_credentials,
    )

    while True:
        start_time = time.time()
        # pylint: disable=maybe-no-member
        result = (
            service.users().messages().list(userId="me", labelIds=["INBOX"]).execute()
        )

        messages = result.get("messages", [])

        all_ids = []
        with ThreadPool(10) as pool:
            all_ids = pool.map(process_message, [message["id"] for message in messages])
        ids_to_remove = [message_id for message_id in all_ids if message_id is not None]
        # limit 1000 ids at a time
        for i in range(0, len(ids_to_remove), 1000):
            service.users().messages().batchModify(
                userId="me",
                body={
                    "ids": ids_to_remove[i : i + 1000],
                    "removeLabelIds": ["INBOX"],
                },
            ).execute()
        end_time = time.time()
        print(
            f"cleared {len(ids_to_remove)} warming emails in {end_time - start_time:.2f} seconds"
        )
        time.sleep(3600 - (end_time - start_time))


if __name__ == "__main__":
    main()
