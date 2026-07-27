"""
Unit tests for google_sync.py Google Calendar integration.
"""

from unittest.mock import MagicMock, patch, mock_open
import pytest
from datetime import datetime, date, timezone, timedelta
from icalendar import Calendar, Event
from googleapiclient.errors import HttpError

import google_sync


def test_get_oauth_credentials_existing_valid():
    mock_creds = MagicMock()
    mock_creds.valid = True

    with patch("os.path.exists", return_value=True), \
         patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=mock_creds):
        creds = google_sync._get_oauth_credentials("credentials.json", "token.json")
        assert creds == mock_creds


def test_get_oauth_credentials_refresh_and_flow():
    # Test expired token refresh failure and flow execution
    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.expired = True
    mock_creds.refresh_token = "rt"
    mock_creds.refresh.side_effect = Exception("Refresh error")

    mock_flow = MagicMock()
    mock_new_creds = MagicMock()
    mock_new_creds.to_json.return_value = '{"token": "xyz"}'
    mock_flow.run_local_server.return_value = mock_new_creds

    with patch("os.path.exists", side_effect=lambda p: p in ("token.json", "credentials.json")), \
         patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=mock_creds), \
         patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file", return_value=mock_flow), \
         patch("builtins.open", mock_open()):
        creds = google_sync._get_oauth_credentials("credentials.json", "token.json")
        assert creds == mock_new_creds


def test_get_oauth_credentials_missing_file():
    with patch("os.path.exists", return_value=False), \
         pytest.raises(FileNotFoundError):
        google_sync._get_oauth_credentials("credentials.json", "token.json")


def test_get_google_calendar_service_service_account():
    mock_creds = MagicMock()
    mock_build = MagicMock()

    with patch("os.path.exists", side_effect=lambda p: p == "service_account.json"), \
         patch("google.oauth2.service_account.Credentials.from_service_account_file", return_value=mock_creds), \
         patch("google_sync.build", return_value=mock_build) as mock_b:
        service = google_sync.get_google_calendar_service("service_account.json")
        assert service == mock_build
        mock_b.assert_called_once_with('calendar', 'v3', credentials=mock_creds)


def test_get_google_calendar_service_oauth_fallback():
    mock_creds = MagicMock()
    mock_build = MagicMock()

    with patch("os.path.exists", return_value=False), \
         patch("google_sync._get_oauth_credentials", return_value=mock_creds), \
         patch("google_sync.build", return_value=mock_build):
        service = google_sync.get_google_calendar_service("service_account.json", "credentials.json")
        assert service == mock_build


def test_share_calendar_with_email():
    mock_service = MagicMock()
    
    # Test when already shared
    mock_service.acl().list().execute().get.return_value = [
        {"scope": {"value": "test@example.com"}}
    ]
    google_sync.share_calendar_with_email(mock_service, "cal_id", "test@example.com")
    mock_service.acl().insert.assert_not_called()

    # Test when not shared
    mock_service.acl().list().execute().get.return_value = []
    google_sync.share_calendar_with_email(mock_service, "cal_id", "new@example.com")
    mock_service.acl().insert.assert_called_once()


def test_get_or_create_calendar_existing():
    mock_service = MagicMock()
    mock_service.calendarList().list().execute.return_value = {
        "items": [{"summary": "Trakt Movies", "id": "cal_123"}],
        "nextPageToken": None
    }

    cal_id = google_sync.get_or_create_calendar(mock_service, "Trakt Movies")
    assert cal_id == "cal_123"


def test_get_or_create_calendar_new():
    mock_service = MagicMock()
    mock_service.calendarList().list().execute.return_value = {
        "items": [],
        "nextPageToken": None
    }
    mock_service.calendars().insert().execute.return_value = {"id": "new_cal_456"}

    cal_id = google_sync.get_or_create_calendar(mock_service, "Trakt TV Shows")
    assert cal_id == "new_cal_456"


def test_sync_single_event_update_and_insert():
    mock_service = MagicMock()

    # Test update existing event
    mock_service.events().list().execute().get.return_value = [{"id": "ev_1"}]
    is_update = google_sync._sync_single_event(mock_service, "cal_id", {}, "uid_1")
    assert is_update is True
    mock_service.events().update.assert_called_once()

    # Test insert new event
    mock_service.events().list().execute().get.return_value = []
    is_update_2 = google_sync._sync_single_event(mock_service, "cal_id", {}, "uid_2")
    assert is_update_2 is False
    mock_service.events().insert.assert_called_once()


def test_fetch_all_google_events():
    mock_service = MagicMock()
    mock_service.events().list().execute.side_effect = [
        {"items": [{"id": "1"}], "nextPageToken": "token2"},
        {"items": [{"id": "2"}], "nextPageToken": None}
    ]

    events = google_sync._fetch_all_google_events(mock_service, "cal_id")
    assert len(events) == 2


def test_delete_removed_events():
    mock_service = MagicMock()
    existing_events = [
        {"id": "ev1", "iCalUID": "trakt-movie-1", "summary": "Movie 1"},
        {"id": "ev2", "iCalUID": "trakt-movie-2", "summary": "Movie 2"},
        {"id": "ev3", "iCalUID": "other-uid-3", "summary": "Other"}
    ]

    current_uids = {"trakt-movie-1"}
    with patch("time.sleep"):
        deleted = google_sync._delete_removed_events(mock_service, "cal_id", current_uids, existing_events)
    assert deleted == 1
    mock_service.events().delete.assert_called_once_with(calendarId="cal_id", eventId="ev2", sendUpdates="none")


def test_sync_ical_to_google_calendar():
    cal = Calendar()
    ev1 = Event()
    ev1.add("summary", "Timed Event")
    ev1.add("description", "Desc")
    ev1.add("uid", "trakt-ep-1")
    ev1.add("dtstart", datetime.now(timezone.utc))
    ev1.add("dtend", datetime.now(timezone.utc) + timedelta(hours=1))

    ev2 = Event()
    ev2.add("summary", "All Day Event")
    ev2.add("description", "Movie Desc")
    ev2.add("uid", "trakt-movie-2")
    ev2.add("dtstart", date(2026, 7, 26))
    ev2.add("dtend", date(2026, 7, 27))

    cal.add_component(ev1)
    cal.add_component(ev2)

    mock_service = MagicMock()
    with patch("google_sync._fetch_all_google_events", return_value=[]), \
         patch("google_sync._delete_removed_events", return_value=0), \
         patch("google_sync._sync_single_event", side_effect=[False, True]), \
         patch("time.sleep"):
        google_sync.sync_ical_to_google_calendar(mock_service, "cal_id", cal)
