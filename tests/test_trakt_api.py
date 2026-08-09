"""
Unit tests for TraktAPI Client.
"""

from unittest.mock import patch, MagicMock
import pytest
from trakt_api import TraktClient, TraktAPIError


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


@patch("requests.get")
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


@patch("requests.get")
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


@patch("requests.get")
def test_get_hidden_show_ids(mock_get):
    client = TraktClient(client_id="test_id", access_token="test_token")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {"show": {"ids": {"trakt": 555}}}
    ]
    mock_get.return_value = mock_resp

    hidden_ids = client.get_hidden_show_ids()
    assert 555 in hidden_ids


@patch("requests.get")
def test_get_show_seasons_with_episodes(mock_get):
    client = TraktClient(client_id="test_id", username="johndoe")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {
            "number": 1,
            "episodes": [
                {"season": 1, "number": 1, "title": "Pilot", "first_aired": "2026-07-20T00:00:00.000Z"}
            ]
        }
    ]
    mock_get.return_value = mock_resp

    seasons = client.get_show_seasons_with_episodes(show_id=12345)
    assert len(seasons) == 1
    assert seasons[0]["episodes"][0]["title"] == "Pilot"


@patch("requests.post")
@patch("requests.get")
def test_auto_refresh_on_401(mock_get, mock_post):
    client = TraktClient(
        client_id="cid",
        access_token="old_acc",
        client_secret="csecret",
        refresh_token="old_ref"
    )

    mock_401 = MagicMock()
    mock_401.status_code = 401

    mock_200 = MagicMock()
    mock_200.status_code = 200
    mock_200.json.return_value = [{"type": "movie"}]

    mock_get.side_effect = [mock_401, mock_200]

    mock_token_resp = MagicMock()
    mock_token_resp.status_code = 200
    mock_token_resp.json.return_value = {
        "access_token": "new_acc",
        "refresh_token": "new_ref"
    }
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
        refreshed_tokens_file=str(token_file)
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
        refreshed_tokens_file=str(token_file)
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
        refreshed_tokens_file=str(token_file)
    )
    client._save_refreshed_tokens()
    assert not token_file.exists()


