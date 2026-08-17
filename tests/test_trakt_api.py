"""
Unit tests for TraktAPI Client.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from trakt_api import TraktAPIError, TraktClient


def test_trakt_client_init_requires_client_id():
    with pytest.raises(ValueError, match="Client ID is required"):
        TraktClient(client_id="")


def test_trakt_client_headers():
    client_pub = TraktClient(client_id="test_id")
    headers = client_pub._get_headers()
    assert headers["trakt-api-key"] == "test_id"
    assert "Authorization" not in headers

    client_auth = TraktClient(client_id="test_id", access_token="test_token")
    headers_auth = client_auth._get_headers()
    assert headers_auth["Authorization"] == "Bearer test_token"


@patch.object(requests.Session, "get")
def test_get_watchlist_public_pagination(mock_get):
    client = TraktClient(client_id="test_id", username="johndoe")

    mock_resp_1 = MagicMock()
    mock_resp_1.status_code = 200
    mock_resp_1.json.return_value = [{"type": "movie", "movie": {"title": "Movie 1"}}]
    mock_resp_1.headers = {"X-Pagination-Page-Count": "2"}

    mock_resp_2 = MagicMock()
    mock_resp_2.status_code = 200
    mock_resp_2.json.return_value = [{"type": "show", "show": {"title": "Show 1"}}]
    mock_resp_2.headers = {"X-Pagination-Page-Count": "2"}

    mock_get.side_effect = [mock_resp_1, mock_resp_2]

    items = client.get_watchlist()
    assert len(items) == 2
    assert items[0]["movie"]["title"] == "Movie 1"
    assert items[1]["show"]["title"] == "Show 1"
    assert mock_get.call_count == 2


@patch.object(requests.Session, "get")
def test_get_watched_shows(mock_get):
    client = TraktClient(client_id="test_id", username="johndoe")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {"plays": 10, "show": {"title": "Watched Show", "ids": {"trakt": 999}}}
    ]
    mock_get.return_value = mock_resp

    watched = client.get_watched_shows()
    assert len(watched) == 1
    assert watched[0]["show"]["ids"]["trakt"] == 999


@patch.object(requests.Session, "get")
def test_get_hidden_show_ids(mock_get):
    client = TraktClient(client_id="test_id", access_token="test_token")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [{"show": {"ids": {"trakt": 555}}}]
    mock_get.return_value = mock_resp

    hidden_ids = client.get_hidden_show_ids()
    assert 555 in hidden_ids


@patch.object(requests.Session, "get")
def test_get_show_seasons_with_episodes(mock_get):
    client = TraktClient(client_id="test_id", username="johndoe")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {
            "number": 1,
            "episodes": [
                {
                    "season": 1,
                    "number": 1,
                    "title": "Pilot",
                    "first_aired": "2026-07-20T00:00:00.000Z",
                }
            ],
        }
    ]
    mock_get.return_value = mock_resp

    seasons = client.get_show_seasons_with_episodes(show_id=12345)
    assert len(seasons) == 1
    assert seasons[0]["episodes"][0]["title"] == "Pilot"


@patch.object(requests.Session, "post")
@patch.object(requests.Session, "get")
def test_auto_refresh_on_401(mock_get, mock_post):
    client = TraktClient(
        client_id="cid", access_token="old_acc", client_secret="csecret", refresh_token="old_ref"
    )

    mock_401 = MagicMock()
    mock_401.status_code = 401

    mock_200 = MagicMock()
    mock_200.status_code = 200
    mock_200.json.return_value = [{"type": "movie"}]

    mock_get.side_effect = [mock_401, mock_200]

    mock_token_resp = MagicMock()
    mock_token_resp.status_code = 200
    mock_token_resp.json.return_value = {"access_token": "new_acc", "refresh_token": "new_ref"}
    mock_post.return_value = mock_token_resp

    items = client.get_watchlist()
    assert len(items) == 1
    assert client.access_token == "new_acc"
    assert client.refresh_token == "new_ref"


def test_corrupted_cache_fallback(tmp_path):
    corrupted_file = tmp_path / "corrupted_cache.json"
    corrupted_file.write_text("{invalid json content", encoding="utf-8")

    client = TraktClient(client_id="test_id", cache_file=str(corrupted_file))
    assert client._show_cache == {}


def test_atomic_save_cache(tmp_path):
    cache_file = tmp_path / "test_cache.json"
    client = TraktClient(client_id="test_id", cache_file=str(cache_file))
    client._show_cache = {"123": {"timestamp": 123456}}
    client._save_cache()

    assert cache_file.exists()
    assert "123" in cache_file.read_text(encoding="utf-8")


def test_save_refreshed_tokens_to_file(tmp_path):
    token_file = tmp_path / "refreshed.json"
    client = TraktClient(
        client_id="test_id",
        access_token="new_access_123",
        refresh_token="new_refresh_456",
        refreshed_tokens_file=str(token_file),
    )
    client._save_refreshed_tokens()

    assert token_file.exists()
    content = token_file.read_text(encoding="utf-8")
    assert "new_access_123" in content
    assert "new_refresh_456" in content


def test_save_refreshed_tokens_github_output(tmp_path):
    token_file = tmp_path / "refreshed.json"
    github_output_file = tmp_path / "github_output.txt"
    github_output_file.write_text("", encoding="utf-8")

    client = TraktClient(
        client_id="test_id",
        access_token="tok_abc",
        refresh_token="ref_xyz",
        refreshed_tokens_file=str(token_file),
    )

    with patch.dict("os.environ", {"GITHUB_OUTPUT": str(github_output_file)}):
        client._save_refreshed_tokens()

    assert github_output_file.exists()
    output_text = github_output_file.read_text(encoding="utf-8")
    assert "refreshed_access_token=tok_abc" in output_text
    assert "refreshed_refresh_token=ref_xyz" in output_text
    assert "tokens_refreshed=true" in output_text


def test_save_refreshed_tokens_noop_if_empty(tmp_path):
    token_file = tmp_path / "refreshed.json"
    client = TraktClient(
        client_id="test_id",
        access_token=None,
        refresh_token=None,
        refreshed_tokens_file=str(token_file),
    )
    client._save_refreshed_tokens()
    assert not token_file.exists()


def test_is_last_page_helper():
    from trakt_api import _is_last_page

    assert (
        _is_last_page({"X-Pagination-Page-Count": "3"}, page=3, item_count=100, limit=100) is True
    )
    assert (
        _is_last_page({"X-Pagination-Page-Count": "3"}, page=2, item_count=100, limit=100) is False
    )
    assert _is_last_page({}, page=1, item_count=50, limit=100) is True
    assert _is_last_page({}, page=1, item_count=100, limit=100) is False


def test_is_rate_limited_helper():
    from trakt_api import _is_rate_limited

    resp_429 = MagicMock(status_code=429)
    assert _is_rate_limited(resp_429) is True

    resp_cf = MagicMock(status_code=503, text="Error from Cloudflare")
    assert _is_rate_limited(resp_cf) is True

    resp_rl = MagicMock(status_code=500, text="You have been rate limited")
    assert _is_rate_limited(resp_rl) is True

    resp_200 = MagicMock(status_code=200, text="OK")
    assert _is_rate_limited(resp_200) is False

    resp_404 = MagicMock(status_code=404, text="Not Found")
    assert _is_rate_limited(resp_404) is False


def test_get_retry_wait_sec():
    from trakt_api import _get_retry_wait_sec

    resp_retry_after = MagicMock(headers={"Retry-After": "4"})
    assert _get_retry_wait_sec(resp_retry_after, attempt=1) == 5

    resp_no_header = MagicMock(headers={})
    assert _get_retry_wait_sec(resp_no_header, attempt=3) == 6


def test_make_http_request_non_get():
    from trakt_api import _make_http_request

    mock_session = MagicMock()
    _make_http_request(
        "POST", "https://api.trakt.tv/test", headers={}, params=None, session=mock_session
    )
    mock_session.request.assert_called_once_with(
        "POST", "https://api.trakt.tv/test", headers={}, params=None, timeout=30
    )

    # Without session fallback
    with patch("requests.request") as mock_req:
        _make_http_request(
            "DELETE", "https://api.trakt.tv/del", headers={}, params=None, session=None
        )
        mock_req.assert_called_once()


def test_handle_page_error():
    from trakt_api import _handle_page_error

    resp = MagicMock(status_code=500, text="Internal Error")
    with pytest.raises(TraktAPIError):
        _handle_page_error(resp, "https://api.trakt.tv/ep", raise_on_error=True)

    # Should not raise when raise_on_error is False
    _handle_page_error(resp, "https://api.trakt.tv/ep", raise_on_error=False)


def test_save_refreshed_tokens_exceptions(tmp_path):
    client = TraktClient(
        client_id="test_id",
        access_token="tok_1",
        refresh_token="ref_1",
        refreshed_tokens_file=str(tmp_path / "inaccessible" / "file.json"),
    )
    with (
        patch("builtins.open", side_effect=PermissionError("Denied")),
        patch.dict("os.environ", {"GITHUB_OUTPUT": str(tmp_path / "gh_out.txt")}),
        patch("os.path.exists", return_value=True),
    ):
        # Should catch exceptions and log warnings without crashing
        client._save_refreshed_tokens()


def test_try_refresh_token_edge_cases():
    # Missing credentials
    with patch.dict("os.environ", {}, clear=True):
        client_no_secret = TraktClient(client_id="cid", client_secret=None, refresh_token=None)
        assert client_no_secret._try_refresh_token() is False

    # 400 Bad Request response
    client = TraktClient(client_id="cid", client_secret="csec", refresh_token="rtok")
    mock_fail_resp = MagicMock(status_code=400, text="Invalid Grant")
    with patch.object(client.session, "post", return_value=mock_fail_resp):
        assert client._try_refresh_token() is False

    # Exception during request
    with patch.object(
        client.session, "post", side_effect=requests.RequestException("Connection error")
    ):
        assert client._try_refresh_token() is False


def test_save_cache_exception(tmp_path):
    client = TraktClient(client_id="test_id", cache_file=str(tmp_path / "cache.json"))
    with patch("builtins.open", side_effect=OSError("Disk write error")):
        client._save_cache()


def test_request_with_retry_rate_limit():
    client = TraktClient(client_id="test_id")
    mock_429 = MagicMock(status_code=429, headers={"Retry-After": "1"})
    mock_200 = MagicMock(status_code=200)

    with (
        patch.object(client.session, "get", side_effect=[mock_429, mock_200]),
        patch("time.sleep") as mock_sleep,
    ):
        resp = client._request_with_retry("GET", "https://api.trakt.tv/test", max_retries=2)
        assert resp.status_code == 200
        mock_sleep.assert_called_once_with(2)


def test_get_user_endpoint_no_auth():
    client = TraktClient(client_id="test_id", access_token=None, username=None)
    with pytest.raises(ValueError, match="Either access_token or username must be provided"):
        client._get_user_endpoint("sync/watchlist", "watchlist")


def test_fetch_paginated_list_non_list_response():
    client = TraktClient(client_id="test_id", username="johndoe")
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"error": "Not a list"}
    mock_resp.headers = {}

    with patch.object(client.session, "get", return_value=mock_resp):
        items = client._fetch_paginated_list("https://api.trakt.tv/test")
        assert items == []


def test_get_hidden_show_ids_edge_cases():
    # No access token
    client_no_token = TraktClient(client_id="test_id", access_token=None)
    assert client_no_token.get_hidden_show_ids() == set()

    # Exception during section request
    client_auth = TraktClient(client_id="test_id", access_token="atok")
    with patch.object(client_auth.session, "get", side_effect=Exception("Network failure")):
        hidden = client_auth.get_hidden_show_ids()
        assert hidden == set()


def test_get_show_seasons_with_episodes_caching_and_errors(tmp_path):
    cache_file = tmp_path / "seasons_cache.json"
    client = TraktClient(client_id="test_id", username="johndoe", cache_file=str(cache_file))

    # Pre-populate cache
    import time

    client._show_cache = {
        "100": {"timestamp": time.time(), "seasons": [{"number": 1, "episodes": []}]},
        "200": {
            "timestamp": time.time() - (25 * 3600),  # Expired for active show
            "seasons": [{"number": 1, "episodes": []}],
        },
    }

    # 1. Cache hit within TTL
    seasons_100 = client.get_show_seasons_with_episodes(100, show_status="returning series")
    assert len(seasons_100) == 1

    # 2. Cache hit for ended show despite expired timestamp
    seasons_200_ended = client.get_show_seasons_with_episodes(200, show_status="ended")
    assert len(seasons_200_ended) == 1

    # 3. 404 Not Found response
    mock_404 = MagicMock(status_code=404, text="Not Found")
    with patch.object(client.session, "get", return_value=mock_404), patch("time.sleep"):
        seasons_404 = client.get_show_seasons_with_episodes(999, show_status="returning series")
        assert seasons_404 == []


def test_request_with_retry_exhausted_retries():
    client = TraktClient(client_id="test_id")
    mock_429 = MagicMock(status_code=429, headers={})
    with patch.object(client.session, "get", return_value=mock_429), patch("time.sleep"):
        resp = client._request_with_retry("GET", "https://api.trakt.tv/exhaust", max_retries=2)
        assert resp.status_code == 429


def test_fetch_paginated_list_error_no_raise():
    client = TraktClient(client_id="test_id", username="johndoe")
    mock_500 = MagicMock(status_code=500, text="Server error")
    with patch.object(client.session, "get", return_value=mock_500):
        items = client._fetch_paginated_list("https://api.trakt.tv/test", raise_on_error=False)
        assert items == []


def test_get_show_seasons_with_episodes_error():
    client = TraktClient(client_id="test_id", username="johndoe")
    mock_500 = MagicMock(status_code=500, text="Server Error")
    with (
        patch.object(client.session, "get", return_value=mock_500),
        patch("time.sleep"),
        pytest.raises(TraktAPIError),
    ):
        client.get_show_seasons_with_episodes(888, show_status="returning series")


def test_get_show_seasons_with_episodes_live_fetch(tmp_path):
    cache_file = tmp_path / "new_cache.json"
    client = TraktClient(client_id="test_id", username="johndoe", cache_file=str(cache_file))

    mock_200 = MagicMock(status_code=200)
    mock_200.json.return_value = [{"number": 1, "episodes": [{"title": "Fresh Ep"}]}]

    with patch.object(client.session, "get", return_value=mock_200), patch("time.sleep"):
        seasons = client.get_show_seasons_with_episodes(12345, show_status="returning series")
        assert len(seasons) == 1
        assert seasons[0]["episodes"][0]["title"] == "Fresh Ep"
        assert "12345" in client._show_cache
