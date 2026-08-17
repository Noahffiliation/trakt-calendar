"""
Trakt API Client for fetching watchlist, watched progress, hidden shows, and show episode release data.
Includes automatic rate-limiting retry handling, request pacing, and local caching for show data.
"""

import json
import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

TRAKT_API_URL = "https://api.trakt.tv"
CACHE_FILE = ".show_cache.json"
CACHE_TTL_HOURS = 24  # Cache ended/known show season data for 24 hours


REFRESHED_TOKENS_FILE = ".trakt_refreshed_tokens.json"


class TraktAPIError(Exception):
    """Custom exception for Trakt API errors."""
    pass


def _is_last_page(headers, page: int, item_count: int, limit: int) -> bool:
    total_pages_header = headers.get("X-Pagination-Page-Count")
    if total_pages_header and total_pages_header.isdigit():
        return page >= int(total_pages_header)
    return item_count < limit


def _is_rate_limited(response: requests.Response) -> bool:
    if response.status_code == 429:
        return True
    if response.status_code != 200:
        text_lower = response.text.lower()
        return "rate limited" in text_lower or "cloudflare" in text_lower
    return False


def _get_retry_wait_sec(response: requests.Response, attempt: int) -> int:
    retry_after = response.headers.get("Retry-After")
    if retry_after and retry_after.isdigit():
        return int(retry_after) + 1
    return attempt * 2


def _make_http_request(
    method: str,
    url: str,
    headers: dict,
    params: dict | None,
    session: requests.Session | None = None
) -> requests.Response:
    requester = session if session is not None else requests
    if method.upper() == "GET":
        return requester.get(url, headers=headers, params=params, timeout=30)
    return requester.request(method, url, headers=headers, params=params, timeout=30)


def _handle_page_error(response, endpoint: str, raise_on_error: bool):
    if raise_on_error:
        raise TraktAPIError(f"Trakt API error {response.status_code}: {response.text}")
    logger.warning(f"Could not fetch {endpoint} ({response.status_code}): {response.text}")


class TraktClient:
    def __init__(
        self,
        client_id: str,
        access_token: str | None = None,
        username: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        base_url: str = TRAKT_API_URL,
        cache_file: str = CACHE_FILE,
        refreshed_tokens_file: str = REFRESHED_TOKENS_FILE,
        session: requests.Session | None = None
    ):
        if not client_id:
            raise ValueError("Trakt Client ID is required.")

        self.client_id = client_id
        self.access_token = access_token
        self.username = username
        self.client_secret = client_secret or os.getenv("TRAKT_CLIENT_SECRET")
        self.refresh_token = refresh_token or os.getenv("TRAKT_REFRESH_TOKEN")
        self.base_url = base_url.rstrip("/")
        self.cache_file = cache_file
        self.refreshed_tokens_file = refreshed_tokens_file
        self.session = session or requests.Session()
        self._show_cache = self._load_cache()

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": self.client_id,
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _save_refreshed_tokens(self):
        """Saves refreshed tokens to a temporary JSON file and GitHub Actions output if present."""
        if not self.access_token or not self.refresh_token:
            return

        token_data = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
        }

        try:
            with open(self.refreshed_tokens_file, "w", encoding="utf-8") as f:
                json.dump(token_data, f)
        except Exception as e:
            logger.warning(f"Could not save refreshed tokens to '{self.refreshed_tokens_file}': {e}")

        github_output = os.getenv("GITHUB_OUTPUT")
        if github_output and os.path.exists(github_output):
            try:
                with open(github_output, "a", encoding="utf-8") as f:
                    f.write(f"refreshed_access_token={self.access_token}\n")
                    f.write(f"refreshed_refresh_token={self.refresh_token}\n")
                    f.write("tokens_refreshed=true\n")
            except Exception as e:
                logger.warning(f"Could not write to GITHUB_OUTPUT: {e}")

    def _try_refresh_token(self) -> bool:
        """Attempt to automatically refresh access token if client_secret & refresh_token are present."""
        if not self.client_secret or not self.refresh_token:
            return False

        logger.info("Encountered 401 Unauthorized. Attempting automatic Trakt OAuth token refresh...")
        try:
            requester = self.session if self.session is not None else requests
            response = requester.post(
                f"{self.base_url}/oauth/token",
                json={
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                    "grant_type": "refresh_token"
                },
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            if response.status_code == 200:
                data = response.json()
                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                logger.info("✅ Successfully refreshed Trakt OAuth access token!")
                self._save_refreshed_tokens()
                return True
            else:
                logger.warning(f"Automatic token refresh failed ({response.status_code}): {response.text}")
                return False
        except Exception as e:
            logger.warning(f"Error during automatic token refresh: {e}")
            return False

    def _load_cache(self) -> dict[str, Any]:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load cache file '{self.cache_file}': {e}")
        return {}

    def _save_cache(self):
        try:
            tmp_file = f"{self.cache_file}.tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(self._show_cache, f)
            os.replace(tmp_file, self.cache_file)
        except Exception as e:
            logger.warning(f"Could not save cache file '{self.cache_file}': {e}")

    def _request_with_retry(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        max_retries: int = 5
    ) -> requests.Response:
        """Execute HTTP request with automatic retry logic for 401 auth refresh, rate limits (429) & Cloudflare pacing."""
        refreshed_attempted = False

        for attempt in range(1, max_retries + 1):
            headers = self._get_headers()
            response = _make_http_request(method, url, headers, params, session=self.session)

            if response.status_code == 401 and not refreshed_attempted:
                refreshed_attempted = True
                if self._try_refresh_token():
                    continue

            if _is_rate_limited(response):
                wait_sec = _get_retry_wait_sec(response, attempt)
                logger.warning(f"Rate limited by Trakt API (Status {response.status_code}). Waiting {wait_sec}s before retry {attempt}/{max_retries}...")
                time.sleep(wait_sec)
                continue

            return response

        return response

    def _get_user_endpoint(self, sync_path: str, user_path: str) -> str:
        if self.access_token:
            return f"{self.base_url}/{sync_path}"
        if self.username:
            return f"{self.base_url}/users/{self.username}/{user_path}"
        raise ValueError("Either access_token or username must be provided.")

    def _fetch_paginated_list(self, endpoint: str, raise_on_error: bool = True) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        limit = 100

        while True:
            logger.info(f"Fetching page {page} from {endpoint}")
            response = self._request_with_retry("GET", endpoint, params={"extended": "full", "page": page, "limit": limit})

            if response.status_code != 200:
                _handle_page_error(response, endpoint, raise_on_error)
                break

            data = response.json()
            if not isinstance(data, list):
                break

            items.extend(data)

            if _is_last_page(response.headers, page, len(data), limit):
                break

            page += 1

        return items

    def get_watchlist(self, item_type: str | None = None) -> list[dict[str, Any]]:
        """Fetch all watchlist items for the user with pagination."""
        sync_path = f"sync/watchlist/{item_type}" if item_type else "sync/watchlist"
        user_path = f"watchlist/{item_type}" if item_type else "watchlist"
        endpoint = self._get_user_endpoint(sync_path, user_path)
        return self._fetch_paginated_list(endpoint, raise_on_error=True)

    def get_watched_shows(self) -> list[dict[str, Any]]:
        """Fetch watched shows for the user with pagination."""
        endpoint = self._get_user_endpoint("sync/watched/shows", "watched/shows")
        return self._fetch_paginated_list(endpoint, raise_on_error=False)

    def get_hidden_show_ids(self) -> set[int]:
        """Fetch set of show Trakt IDs that have been dropped or hidden by the user."""
        if not self.access_token:
            logger.warning(
                "Trakt OAuth access token (TRAKT_ACCESS_TOKEN) not provided. "
                "Filtering dropped/hidden shows requires OAuth token authentication. "
                "Skipping hidden shows lookup."
            )
            return set()

        sections = ["progress_watched", "progress_watched_reset", "calendar", "dropped", "recommendations"]
        hidden_ids: set[int] = set()

        for section in sections:
            endpoint = f"{self.base_url}/users/hidden/{section}"
            params = {"type": "show", "limit": 100}
            try:
                response = self._request_with_retry("GET", endpoint, params=params)
                if response.status_code == 200 and isinstance(response.json(), list):
                    for item in response.json():
                        show = item.get("show", {})
                        trakt_id = show.get("ids", {}).get("trakt")
                        if trakt_id:
                            hidden_ids.add(trakt_id)
            except Exception as e:
                logger.debug(f"Could not fetch hidden section '{section}': {e}")

        logger.info(f"Retrieved {len(hidden_ids)} hidden/dropped show ID(s).")
        return hidden_ids

    def get_show_seasons_with_episodes(
        self,
        show_id: str | int,
        show_status: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Fetch all seasons and episode air dates for a show with local caching and request pacing.
        GET /shows/{id}/seasons?extended=episodes,full
        """
        str_id = str(show_id)
        now_ts = time.time()

        # Check local cache
        if str_id in self._show_cache:
            cache_entry = self._show_cache[str_id]
            cached_ts = cache_entry.get("timestamp", 0)

            # If show is ended/canceled and we cached it, or cached within CACHE_TTL_HOURS
            if (show_status in ("ended", "canceled")) or (now_ts - cached_ts < CACHE_TTL_HOURS * 3600):
                return cache_entry.get("seasons", [])

        # Pacing request: 0.1s delay to prevent Cloudflare rate-limiting on large libraries
        time.sleep(0.1)

        endpoint = f"{self.base_url}/shows/{show_id}/seasons"
        params = {"extended": "episodes,full"}

        response = self._request_with_retry("GET", endpoint, params=params)

        if response.status_code == 404:
            logger.warning(f"Show {show_id} seasons not found (404).")
            return []
        elif response.status_code != 200:
            raise TraktAPIError(
                f"Trakt API error fetching show {show_id} seasons: {response.status_code} - {response.text}"
            )

        seasons_data = response.json()

        # Save to local cache
        self._show_cache[str_id] = {
            "timestamp": now_ts,
            "seasons": seasons_data
        }
        self._save_cache()

        return seasons_data
