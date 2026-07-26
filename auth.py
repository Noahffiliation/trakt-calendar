"""
Helper script to authenticate with Trakt via Device Code or PIN flow
and retrieve an OAuth Access Token for private watchlists.
"""

import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

TRAKT_API_URL = "https://api.trakt.tv"


def main():
    client_id = os.getenv("TRAKT_CLIENT_ID") or input("Enter Trakt Client ID: ").strip()
    client_secret = os.getenv("TRAKT_CLIENT_SECRET") or input("Enter Trakt Client Secret: ").strip()

    if not client_id or not client_secret:
        print("Error: Both Client ID and Client Secret are required for OAuth flow.")
        sys.exit(1)

    print("\n--- Trakt Device Code Authorization ---")
    response = requests.post(
        f"{TRAKT_API_URL}/oauth/device/code",
        json={"client_id": client_id},
        headers={"Content-Type": "application/json"}
    )

    if response.status_code != 200:
        print(f"Error initiating device authentication: {response.status_code} - {response.text}")
        sys.exit(1)

    data = response.json()
    device_code = data["device_code"]
    user_code = data["user_code"]
    verification_url = data["verification_url"]
    expires_in = data["expires_in"]

    print(f"\n1. Open your browser and navigate to: {verification_url}")
    print(f"2. Enter the following code: {user_code}")
    print(f"(This code will expire in {expires_in} seconds)\n")

    input("Press ENTER after you have authorized the app in your browser...")

    print("Requesting access token...")
    token_resp = requests.post(
        f"{TRAKT_API_URL}/oauth/device/token",
        json={
            "code": device_code,
            "client_id": client_id,
            "client_secret": client_secret
        },
        headers={"Content-Type": "application/json"}
    )

    if token_resp.status_code == 200:
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")

        print("\n✅ Authorization successful!")
        print(f"TRAKT_ACCESS_TOKEN={access_token}")
        print(f"TRAKT_REFRESH_TOKEN={refresh_token}")
        print("\nAdd TRAKT_ACCESS_TOKEN to your .env file or environment variables.")
    else:
        print(f"❌ Failed to obtain access token: {token_resp.status_code} - {token_resp.text}")


if __name__ == "__main__":
    main()
