"""
Helper script to authenticate with Trakt via Device Code or PIN flow
and retrieve an OAuth Access Token for private watchlists.
"""

import os
import sys

import requests
from dotenv import load_dotenv

from trakt_api import update_env_file

load_dotenv()

TRAKT_API_URL = "https://api.trakt.tv"
CONTENT_TYPE_JSON = "application/json"
JSON_HEADERS = {"Content-Type": CONTENT_TYPE_JSON}


def refresh_oauth_token(client_id: str, client_secret: str, refresh_token: str) -> dict | None:
    """Exchanges a refresh token for a new access token and refresh token via Trakt API."""
    response = requests.post(
        f"{TRAKT_API_URL}/oauth/token",
        json={
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
            "grant_type": "refresh_token",
        },
        headers=JSON_HEADERS,
        timeout=30,
    )
    if response.status_code == 200:
        return response.json()
    else:
        print(f"❌ Failed to refresh token: {response.status_code} - {response.text}")
        return None


def _get_credentials() -> tuple[str, str]:
    client_id = os.getenv("TRAKT_CLIENT_ID") or input("Enter Trakt Client ID: ").strip()
    client_secret = os.getenv("TRAKT_CLIENT_SECRET") or input("Enter Trakt Client Secret: ").strip()
    if not client_id or not client_secret:
        print("Error: Both Client ID and Client Secret are required for OAuth flow.")
        sys.exit(1)
    return client_id, client_secret


def _save_tokens_to_env(
    access_token: str, refresh_token: str, create_if_missing: bool = False
) -> None:
    target_env = os.getenv("ENV_FILE") or (
        ".env" if os.path.exists(".env") and not os.getenv("PYTEST_CURRENT_TEST") else None
    )
    if not (target_env and access_token and refresh_token):
        return

    try:
        update_env_file(
            target_env,
            {
                "TRAKT_ACCESS_TOKEN": str(access_token),
                "TRAKT_REFRESH_TOKEN": str(refresh_token),
            },
            create_if_missing=create_if_missing,
        )
        print(f"✅ Automatically updated {target_env} with new access and refresh tokens.")
    except Exception as e:
        print(f"⚠️  Could not automatically update {target_env}: {e}")


def _handle_token_refresh(client_id: str, client_secret: str) -> None:
    refresh_tok = os.getenv("TRAKT_REFRESH_TOKEN") or input("Enter Trakt Refresh Token: ").strip()
    if not refresh_tok:
        print("Error: Refresh Token is required for token refresh.")
        sys.exit(1)

    print("Requesting token refresh...")
    token_data = refresh_oauth_token(client_id, client_secret, refresh_tok)
    if not token_data:
        sys.exit(1)

    print("\n✅ Token refresh successful!")
    print("\nUpdate your .env file or environment variables with these values.")
    _save_tokens_to_env(
        token_data.get("access_token", ""),
        token_data.get("refresh_token", ""),
        create_if_missing=False,
    )


def _initiate_device_code(client_id: str) -> dict:
    print("\n--- Trakt Device Code Authorization ---")
    response = requests.post(
        f"{TRAKT_API_URL}/oauth/device/code",
        json={"client_id": client_id},
        headers=JSON_HEADERS,
        timeout=30,
    )
    if response.status_code != 200:
        print(f"Error initiating device authentication: {response.status_code} - {response.text}")
        sys.exit(1)
    return response.json()


def _prompt_user_authorization(data: dict) -> None:
    user_code = data["user_code"]
    verification_url = data["verification_url"]
    expires_in = data["expires_in"]

    print(f"\n1. Open your browser and navigate to: {verification_url}")
    print(f"2. Enter the following code: {user_code}")
    print(f"(This code will expire in {expires_in} seconds)\n")

    input("Press ENTER after you have authorized the app in your browser...")


def _exchange_device_token(client_id: str, client_secret: str, device_code: str) -> None:
    print("Requesting access token...")
    token_resp = requests.post(
        f"{TRAKT_API_URL}/oauth/device/token",
        json={"code": device_code, "client_id": client_id, "client_secret": client_secret},
        headers=JSON_HEADERS,
        timeout=30,
    )

    if token_resp.status_code != 200:
        print(f"❌ Failed to obtain access token: {token_resp.status_code} - {token_resp.text}")
        return

    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")

    print("\n✅ Authorization successful!")
    print(f"TRAKT_ACCESS_TOKEN={access_token}")
    print(f"TRAKT_REFRESH_TOKEN={refresh_token}")
    print("\nAdd TRAKT_ACCESS_TOKEN to your .env file or environment variables.")
    _save_tokens_to_env(str(access_token), str(refresh_token), create_if_missing=True)


def main(args=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Authenticate with Trakt or refresh OAuth access token."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Attempt to refresh access token using TRAKT_REFRESH_TOKEN",
    )
    cli_args = parser.parse_args(args)

    client_id, client_secret = _get_credentials()

    if cli_args.refresh:
        _handle_token_refresh(client_id, client_secret)
        return

    code_data = _initiate_device_code(client_id)
    _prompt_user_authorization(code_data)
    _exchange_device_token(client_id, client_secret, code_data["device_code"])


if __name__ == "__main__":
    main()
