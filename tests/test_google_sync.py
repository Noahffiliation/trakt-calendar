"""
Unit tests for google_sync.py Google Calendar integration.
"""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, mock_open, patch

import pytest
from googleapiclient.errors import HttpError
from icalendar import Calendar, Event

import google_sync


def test_get_oauth_credentials_existing_valid():
    mock_creds = MagicMock()
    mock_creds.valid = True

    with (
        patch("os.path.exists", return_value=True),
        patch(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        ),
    ):
        creds = google_sync._get_oauth_credentials("credentials.json", "token.json")
        assert creds == mock_creds


def test_get_oauth_credentials_refresh_success():
    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.expired = True
    mock_creds.refresh_token = "rt"
    mock_creds.refresh.return_value = None

    with (
        patch("os.path.exists", return_value=True),
        patch(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        ),
    ):
        creds = google_sync._get_oauth_credentials("credentials.json", "token.json")
        assert creds == mock_creds
        mock_creds.refresh.assert_called_once()


def test_get_oauth_credentials_refresh_fail_and_flow():
    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.expired = True
    mock_creds.refresh_token = "rt"
    mock_creds.refresh.side_effect = Exception("Refresh error")

    mock_flow = MagicMock()
    mock_new_creds = MagicMock()
    mock_new_creds.to_json.return_value = '{"token": "xyz"}'
    mock_flow.run_local_server.return_value = mock_new_creds

    with (
        patch("os.path.exists", side_effect=lambda p: p in ("token.json", "credentials.json")),
        patch(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        ),
        patch(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
            return_value=mock_flow,
        ),
        patch("builtins.open", mock_open()),
    ):
        creds = google_sync._get_oauth_credentials("credentials.json", "token.json")
        assert creds == mock_new_creds


def test_get_oauth_credentials_missing_file():
    with patch("os.path.exists", return_value=False), pytest.raises(FileNotFoundError):
        google_sync._get_oauth_credentials("credentials.json", "token.json")


def test_get_google_calendar_service_service_account():
    mock_creds = MagicMock()
    mock_build = MagicMock()

    with (
        patch("os.path.exists", side_effect=lambda p: p == "service_account.json"),
        patch(
            "google.oauth2.service_account.Credentials.from_service_account_file",
            return_value=mock_creds,
        ),
        patch("google_sync.build", return_value=mock_build) as mock_b,
    ):
        service = google_sync.get_google_calendar_service("service_account.json")
        assert service == mock_build
        mock_b.assert_called_once_with("calendar", "v3", credentials=mock_creds)


def test_get_google_calendar_service_oauth_fallback():
    mock_creds = MagicMock()
    mock_build = MagicMock()

    with (
        patch("os.path.exists", return_value=False),
        patch("google_sync._get_oauth_credentials", return_value=mock_creds),
        patch("google_sync.build", return_value=mock_build),
    ):
        service = google_sync.get_google_calendar_service(
            "service_account.json", "credentials.json"
        )
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
        "nextPageToken": None,
    }

    cal_id = google_sync.get_or_create_calendar(mock_service, "Trakt Movies")
    assert cal_id == "cal_123"


def test_get_or_create_calendar_new():
    mock_service = MagicMock()
    mock_service.calendarList().list().execute.return_value = {"items": [], "nextPageToken": None}
    mock_service.calendars().insert().execute.return_value = {"id": "new_cal_456"}

    cal_id = google_sync.get_or_create_calendar(mock_service, "Trakt TV Shows")
    assert cal_id == "new_cal_456"


def test_get_or_create_calendar_explicit_arg():
    mock_service = MagicMock()
    cal_id = google_sync.get_or_create_calendar(
        mock_service, "Trakt Movies", calendar_id="custom_movies_cal_id"
    )
    assert cal_id == "custom_movies_cal_id"
    mock_service.calendarList().list.assert_not_called()


def test_get_or_create_calendar_env_override():
    mock_service = MagicMock()
    with patch.dict("os.environ", {"GOOGLE_CALENDAR_ID_TRAKT_MOVIES": "env_movie_cal_id"}):
        cal_id = google_sync.get_or_create_calendar(mock_service, "Trakt Movies")
        assert cal_id == "env_movie_cal_id"
        mock_service.calendarList().list.assert_not_called()


def test_event_has_changed_helper():
    base_existing = {
        "status": "confirmed",
        "summary": "Movie A",
        "description": "Desc A",
        "start": {"date": "2026-08-01"},
        "end": {"date": "2026-08-02"},
    }

    # Identical
    assert (
        google_sync._event_has_changed(
            base_existing,
            {
                "status": "confirmed",
                "summary": "Movie A",
                "description": "Desc A",
                "start": {"date": "2026-08-01"},
                "end": {"date": "2026-08-02"},
            },
        )
        is False
    )

    # Status changed from cancelled to confirmed
    assert (
        google_sync._event_has_changed(
            {"status": "cancelled", "summary": "Movie A", "description": "Desc A"},
            {"status": "confirmed", "summary": "Movie A", "description": "Desc A"},
        )
        is True
    )

    # Summary changed
    assert (
        google_sync._event_has_changed(
            base_existing,
            {
                "summary": "Movie B",
                "description": "Desc A",
                "start": {"date": "2026-08-01"},
                "end": {"date": "2026-08-02"},
            },
        )
        is True
    )

    # Description changed
    assert (
        google_sync._event_has_changed(
            base_existing,
            {
                "summary": "Movie A",
                "description": "New Desc",
                "start": {"date": "2026-08-01"},
                "end": {"date": "2026-08-02"},
            },
        )
        is True
    )

    # Start changed
    assert (
        google_sync._event_has_changed(
            base_existing,
            {
                "summary": "Movie A",
                "description": "Desc A",
                "start": {"date": "2026-08-05"},
                "end": {"date": "2026-08-02"},
            },
        )
        is True
    )

    # End changed
    assert (
        google_sync._event_has_changed(
            base_existing,
            {
                "summary": "Movie A",
                "description": "Desc A",
                "start": {"date": "2026-08-01"},
                "end": {"date": "2026-08-06"},
            },
        )
        is True
    )


def test_upsert_single_event_update_and_insert():
    mock_service = MagicMock()

    # Test update existing event via patch
    is_update = google_sync._upsert_single_event(
        mock_service, "cal_id", {"summary": "New Summary"}, existing_event={"id": "ev_1"}
    )
    assert is_update is True
    mock_service.events().patch.assert_called_with(
        calendarId="cal_id",
        eventId="ev_1",
        body={"summary": "New Summary", "status": "confirmed"},
    )

    # Test insert new event
    is_update_2 = google_sync._upsert_single_event(
        mock_service, "cal_id", {"summary": "Brand New"}, existing_event=None
    )
    assert is_update_2 is False
    mock_service.events().insert.assert_called_with(
        calendarId="cal_id", body={"summary": "Brand New", "status": "confirmed"}
    )


def test_upsert_single_event_409_duplicate_fallback():
    mock_service = MagicMock()
    resp = MagicMock(status=409, reason="Conflict")
    err_409 = HttpError(resp, b"The requested identifier already exists.")
    mock_service.events().insert().execute.side_effect = err_409

    # Mock list returning the existing item
    mock_service.events().list().execute().get.return_value = [{"id": "recovered_id"}]

    is_update = google_sync._upsert_single_event(
        mock_service,
        "cal_id",
        {"summary": "Conflict Show", "iCalUID": "trakt-123"},
        existing_event=None,
    )
    assert is_update is True
    mock_service.events().patch.assert_called_with(
        calendarId="cal_id",
        eventId="recovered_id",
        body={"summary": "Conflict Show", "iCalUID": "trakt-123", "status": "confirmed"},
    )


def test_upsert_single_event_non_409_error():
    mock_service = MagicMock()
    resp = MagicMock(status=500, reason="Server Error")
    err_500 = HttpError(resp, b"Internal Error")
    mock_service.events().insert().execute.side_effect = err_500

    with pytest.raises(HttpError):
        google_sync._upsert_single_event(
            mock_service, "cal_id", {"summary": "Error Show"}, existing_event=None
        )


def test_fetch_all_google_events():
    mock_service = MagicMock()
    mock_service.events().list().execute.side_effect = [
        {"items": [{"id": "1"}], "nextPageToken": "token2"},
        {"items": [{"id": "2"}], "nextPageToken": None},
    ]

    events = google_sync._fetch_all_google_events(mock_service, "cal_id")
    assert len(events) == 2
    mock_service.events().list.assert_called_with(
        calendarId="cal_id",
        pageToken="token2",
        maxResults=2500,
        singleEvents=False,
        showDeleted=True,
    )


def test_delete_removed_events():
    mock_service = MagicMock()
    existing_events = [
        {"id": "ev1", "iCalUID": "trakt-movie-1", "summary": "Movie 1", "status": "confirmed"},
        {"id": "ev2", "iCalUID": "trakt-movie-2", "summary": "Movie 2", "status": "confirmed"},
        {"id": "ev3", "iCalUID": "other-uid-3", "summary": "Other", "status": "confirmed"},
        {"id": "ev4", "iCalUID": "trakt-movie-4", "summary": "Cancelled", "status": "cancelled"},
    ]

    current_uids = {"trakt-movie-1"}
    with patch("time.sleep"):
        deleted = google_sync._delete_removed_events(
            mock_service, "cal_id", current_uids, existing_events
        )
    # Only ev2 should be deleted; ev4 is already cancelled and ignored
    assert deleted == 1
    mock_service.events().delete.assert_called_once_with(
        calendarId="cal_id", eventId="ev2", sendUpdates="none"
    )


def test_get_oauth_credentials_corrupt_user_file():
    mock_flow = MagicMock()
    mock_new_creds = MagicMock()
    mock_new_creds.to_json.return_value = '{"token": "xyz"}'
    mock_flow.run_local_server.return_value = mock_new_creds

    with (
        patch("os.path.exists", side_effect=lambda p: p in ("token.json", "credentials.json")),
        patch(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            side_effect=Exception("Corrupt file"),
        ),
        patch(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
            return_value=mock_flow,
        ),
        patch("builtins.open", mock_open()),
    ):
        creds = google_sync._get_oauth_credentials("credentials.json", "token.json")
        assert creds == mock_new_creds


def test_share_calendar_with_email_none():
    mock_service = MagicMock()
    with patch.dict("os.environ", {}, clear=True):
        google_sync.share_calendar_with_email(mock_service, "cal_id", share_email=None)
        mock_service.acl.assert_not_called()


def test_share_calendar_with_email_httperror():
    mock_service = MagicMock()
    mock_service.acl().list().execute().get.return_value = []

    resp = MagicMock(status=400, reason="Bad Request")
    http_err = HttpError(resp, b"ACL error")
    mock_service.acl().insert().execute.side_effect = http_err

    google_sync.share_calendar_with_email(mock_service, "cal_id", "error@example.com")


def test_delete_removed_events_httperror():
    mock_service = MagicMock()
    existing_events = [{"id": "ev1", "iCalUID": "trakt-movie-1", "summary": "Movie 1"}]
    resp = MagicMock(status=400, reason="Error")
    mock_service.events().delete().execute.side_effect = HttpError(resp, b"Delete failed")

    with patch("time.sleep"):
        deleted = google_sync._delete_removed_events(
            mock_service, "cal_id", current_uids=set(), existing_events=existing_events
        )
    assert deleted == 0


def test_sync_ical_to_google_calendar_success():
    cal = Calendar()
    ev1 = Event()
    ev1.add("summary", "Timed Event")
    ev1.add("description", "Desc")
    ev1.add("uid", "trakt-ep-1")
    ev1.add("dtstart", datetime.now(UTC))
    ev1.add("dtend", datetime.now(UTC) + timedelta(hours=1))

    ev2 = Event()
    ev2.add("summary", "All Day Event")
    ev2.add("description", "Movie Desc")
    ev2.add("uid", "trakt-movie-2")
    ev2.add("dtstart", date(2026, 7, 26))
    ev2.add("dtend", date(2026, 7, 27))

    cal.add_component(ev1)
    cal.add_component(ev2)

    mock_service = MagicMock()
    # One existing changed event, one new event
    existing_events = [
        {
            "id": "existing_ev_1",
            "iCalUID": "trakt-ep-1",
            "summary": "Old Summary",  # Changed!
            "description": "Desc",
            "start": {"dateTime": ev1.get("dtstart").dt.isoformat()},
            "end": {"dateTime": ev1.get("dtend").dt.isoformat()},
        }
    ]
    with (
        patch("google_sync._fetch_all_google_events", return_value=existing_events),
        patch("google_sync._delete_removed_events", return_value=0),
        patch("google_sync._upsert_single_event", side_effect=[True, False]) as mock_upsert,
        patch("time.sleep"),
    ):
        google_sync.sync_ical_to_google_calendar(mock_service, "cal_id", cal)
        # ev1 is updated (changed), ev2 is inserted
        assert mock_upsert.call_count == 2


def test_sync_ical_to_google_calendar_skip_unchanged():
    cal = Calendar()
    ev1 = Event()
    ev1.add("summary", "Timed Event")
    ev1.add("description", "Desc")
    ev1.add("uid", "trakt-ep-1")
    ev1.add("dtstart", datetime.now(UTC))
    ev1.add("dtend", datetime.now(UTC) + timedelta(hours=1))
    cal.add_component(ev1)

    mock_service = MagicMock()
    existing_events = [
        {
            "id": "existing_ev_1",
            "iCalUID": "trakt-ep-1",
            "summary": "Timed Event",
            "description": "Desc",
            "start": {"dateTime": ev1.get("dtstart").dt.isoformat()},
            "end": {"dateTime": ev1.get("dtend").dt.isoformat()},
        }
    ]
    with (
        patch("google_sync._fetch_all_google_events", return_value=existing_events),
        patch("google_sync._delete_removed_events", return_value=0),
        patch("google_sync._upsert_single_event") as mock_upsert,
        patch("time.sleep"),
    ):
        google_sync.sync_ical_to_google_calendar(mock_service, "cal_id", cal)
        # Unchanged event was skipped completely
        mock_upsert.assert_not_called()


def test_sync_ical_to_google_calendar_httperror():
    cal = Calendar()
    ev1 = Event()
    ev1.add("summary", "Timed Event")
    ev1.add("uid", "trakt-ep-1")
    ev1.add("dtstart", datetime.now(UTC))
    ev1.add("dtend", datetime.now(UTC) + timedelta(hours=1))
    cal.add_component(ev1)

    mock_service = MagicMock()
    resp = MagicMock(status=500, reason="Server Error")
    http_err = HttpError(resp, b"Sync error")

    with (
        patch("google_sync._fetch_all_google_events", return_value=[]),
        patch("google_sync._delete_removed_events", return_value=0),
        patch("google_sync._upsert_single_event", side_effect=http_err),
        patch("time.sleep"),
    ):
        google_sync.sync_ical_to_google_calendar(mock_service, "cal_id", cal)
