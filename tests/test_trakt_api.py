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
