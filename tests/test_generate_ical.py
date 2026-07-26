"""
Unit tests for generate_ical.py script execution and categorization logic.
"""

from unittest.mock import MagicMock, patch
import pytest
from datetime import datetime, timezone
import generate_ical
from trakt_api import TraktAPIError


def test_write_ics(tmp_path):
    mock_cal = MagicMock()
    mock_cal.to_ical.return_value = b"BEGIN:VCALENDAR\nEND:VCALENDAR"
    comp = MagicMock()
    comp.name = "VEVENT"
    mock_cal.subcomponents = [comp]

    out_file = tmp_path / "sub" / "test.ics"
    generate_ical.write_ics(mock_cal, str(out_file))

    assert out_file.exists()
    assert out_file.read_bytes() == b"BEGIN:VCALENDAR\nEND:VCALENDAR"


def test_validate_safe_path_traversal(tmp_path):
    # Test valid path within base_dir
    valid_path = generate_ical.validate_safe_path("sub/file.ics", base_dir=tmp_path)
    assert valid_path == (tmp_path / "sub" / "file.ics").resolve()

    # Test path traversal rejection
    with pytest.raises(ValueError) as exc_info:
        generate_ical.validate_safe_path("../../../etc/passwd", base_dir=tmp_path)
    assert "Path traversal security error" in str(exc_info.value)

    # Test relative to cwd traversal rejection
    with pytest.raises(ValueError) as exc_info2:
        generate_ical.validate_safe_path("../../outside.ics")
    assert "Path traversal security error" in str(exc_info2.value)



def test_init_client_missing_id():
    args = MagicMock()
    args.client_id = None
    with pytest.raises(SystemExit) as exc_info:
        generate_ical._init_client(args)
    assert exc_info.value.code == 1


def test_init_client_missing_tokens():
    args = MagicMock()
    args.client_id = "cid"
    args.access_token = None
    args.username = None
    with pytest.raises(SystemExit) as exc_info:
        generate_ical._init_client(args)
    assert exc_info.value.code == 1


def test_init_client_success():
    args = MagicMock()
    args.client_id = "cid"
    args.access_token = "atok"
    args.username = "user"
    client = generate_ical._init_client(args)
    assert client.client_id == "cid"


def test_categorize_items():
    watchlist_items = [
        {"type": "movie", "movie": {"title": "Movie 1"}},
        {"type": "show", "show": {"title": "Show 1", "ids": {"trakt": 10}}},
        {"type": "episode", "show": {"title": "Show 2"}, "episode": {"season": 1, "number": 1}}
    ]

    mock_client = MagicMock()
    mock_client.get_watched_shows.return_value = [
        {"show": {"title": "Show 3", "ids": {"trakt": 20}}}
    ]

    hidden = {99}
    movies, shows, episodes = generate_ical._categorize_items(
        watchlist_items, hidden, mock_client, include_watched=True
    )

    assert len(movies) == 1
    assert len(shows) == 2
    assert len(episodes) == 1


def test_fetch_show_episodes():
    candidate_shows = [
        {"title": "Show A", "ids": {"trakt": 1}, "status": "returning series"},
        {"title": "Show B", "ids": {"trakt": 2}, "status": "ended"}
    ]
    direct_episodes = [
        {"show": {"title": "Direct Show"}, "episode": {"season": 1, "number": 1}}
    ]

    mock_client = MagicMock()
    mock_client.get_show_seasons_with_episodes.side_effect = [
        [{"episodes": [{"season": 1, "number": 1}]}],
        []
    ]

    episodes, premieres = generate_ical._fetch_show_episodes(mock_client, candidate_shows, direct_episodes)
    assert len(episodes) == 2
    assert len(premieres) == 1



def test_sync_to_google():
    mock_service = MagicMock()
    mock_movies_cal = MagicMock()
    mock_shows_cal = MagicMock()

    with patch("google_sync.get_google_calendar_service", return_value=mock_service), \
         patch("google_sync.get_or_create_calendar", side_effect=["m_id", "s_id"]), \
         patch("google_sync.sync_ical_to_google_calendar") as mock_sync:
        generate_ical._sync_to_google(mock_movies_cal, mock_shows_cal)
        assert mock_sync.call_count == 2


def test_main(tmp_path):
    mock_args = MagicMock()
    mock_args.verbose = False
    mock_args.days_back = 30
    mock_args.no_watched = False
    mock_args.sync_google = False
    mock_args.movies_output = str(tmp_path / "movies.ics")
    mock_args.shows_output = str(tmp_path / "shows.ics")
    mock_args.output = str(tmp_path / "combined.ics")

    mock_client = MagicMock()
    mock_client.get_hidden_show_ids.return_value = set()
    mock_client.get_watchlist.return_value = [
        {"type": "movie", "movie": {"title": "Inception", "ids": {"trakt": 1}}}
    ]
    mock_client.get_watched_shows.return_value = []

    with patch("generate_ical.parse_args", return_value=mock_args), \
         patch("generate_ical._init_client", return_value=mock_client):
        generate_ical.main()

    assert (tmp_path / "movies.ics").exists()
    assert (tmp_path / "shows.ics").exists()
    assert (tmp_path / "combined.ics").exists()
