"""
Google Calendar API Sync module using Service Account or OAuth Credentials.
"""

import logging
import os.path
import time
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from icalendar import Calendar

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar']
SERVICE_ACCOUNT_FILE = 'service_account.json'
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'


def _get_oauth_credentials(credentials_path: str, token_path: str):
    """Load or refresh OAuth client credentials."""
    creds = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception as e:
            logger.warning(f"Could not load {token_path}: {e}")

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            return creds
        except Exception as e:
            logger.warning(f"Could not refresh token: {e}")

    if not os.path.exists(credentials_path):
        raise FileNotFoundError(
            f"Neither '{SERVICE_ACCOUNT_FILE}' nor '{credentials_path}' was found.\n"
            "Please provide a service_account.json or credentials.json file."
        )

    flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(token_path, 'w') as token_file:
        token_file.write(creds.to_json())

    return creds


def get_google_calendar_service(
    service_account_path: str = SERVICE_ACCOUNT_FILE,
    credentials_path: str = CREDENTIALS_FILE,
    token_path: str = TOKEN_FILE
):
    """
    Authenticate and return a Google Calendar API service instance.
    Prefers service_account.json if present; otherwise falls back to OAuth credentials.
    """
    if os.path.exists(service_account_path):
        logger.info(f"Using Service Account authentication from '{service_account_path}'")
        creds = service_account.Credentials.from_service_account_file(
            service_account_path,
            scopes=SCOPES
        )
        return build('calendar', 'v3', credentials=creds)

    creds = _get_oauth_credentials(credentials_path, token_path)
    return build('calendar', 'v3', credentials=creds)


def share_calendar_with_email(service, calendar_id: str, share_email: str | None = None):
    """Share a Google Calendar with a user email address via ACL without sending notification emails."""
    email = share_email or os.getenv("GOOGLE_SHARE_EMAIL") or os.getenv("GOOGLE_CALENDAR_SHARE_EMAIL")
    if not email:
        return
    try:
        # Check if already shared to avoid redundant API calls and emails
        existing_acls = service.acl().list(calendarId=calendar_id).execute(num_retries=3).get('items', [])
        for acl in existing_acls:
            if acl.get('scope', {}).get('value', '').lower() == email.lower():
                return  # Already shared

        rule = {
            'scope': {'type': 'user', 'value': email},
            'role': 'writer'
        }
        service.acl().insert(calendarId=calendar_id, body=rule, sendNotifications=False).execute(num_retries=3)
        logger.info(f"Shared Google Calendar ID '{calendar_id}' with '{email}'")
    except HttpError as error:
        if "alreadyExists" not in str(error) and "duplicate" not in str(error).lower():
            logger.warning(f"Could not share calendar with '{email}': {error}")


def get_or_create_calendar(service, calendar_name: str, share_email: str | None = None) -> str:
    """Retrieve existing Google Calendar ID by name or create a new dedicated calendar."""
    page_token = None
    while True:
        calendar_list = service.calendarList().list(pageToken=page_token).execute()
        for calendar_list_entry in calendar_list.get('items', []):
            if calendar_list_entry.get('summary') == calendar_name:
                cal_id = calendar_list_entry['id']
                logger.info(f"Found existing Google Calendar '{calendar_name}' (ID: {cal_id})")
                share_calendar_with_email(service, cal_id, share_email)
                return cal_id
        page_token = calendar_list.get('nextPageToken')
        if not page_token:
            break

    # Create calendar if not found
    calendar_body = {
        'summary': calendar_name,
        'timeZone': 'UTC'
    }
    created_calendar = service.calendars().insert(body=calendar_body).execute()
    cal_id = created_calendar['id']
    logger.info(f"Created new Google Calendar '{calendar_name}' (ID: {cal_id})")
    share_calendar_with_email(service, cal_id, share_email)
    return cal_id


def _event_has_changed(existing_event: dict[str, Any], new_body: dict[str, Any]) -> bool:
    """Check if meaningful event attributes (summary, description, start, end) have changed."""
    if existing_event.get('summary') != new_body.get('summary'):
        return True
    if (existing_event.get('description') or '') != (new_body.get('description') or ''):
        return True

    existing_start = existing_event.get('start', {})
    new_start = new_body.get('start', {})
    if existing_start.get('date') != new_start.get('date') or existing_start.get('dateTime') != new_start.get('dateTime'):
        return True

    existing_end = existing_event.get('end', {})
    new_end = new_body.get('end', {})
    return existing_end.get('date') != new_end.get('date') or existing_end.get('dateTime') != new_end.get('dateTime')


def _upsert_single_event(
    service: Any,
    calendar_id: str,
    event_body: dict[str, Any],
    existing_event: dict[str, Any] | None = None
) -> bool:
    """
    Inserts or patches an event in Google Calendar, returning True if updated, False if created.
    Uses num_retries=5 for automatic exponential backoff on 403/429 rate limit errors.
    """
    if existing_event:
        event_id = existing_event['id']
        service.events().patch(
            calendarId=calendar_id,
            eventId=event_id,
            body=event_body
        ).execute(num_retries=5)
        return True

    try:
        service.events().insert(
            calendarId=calendar_id,
            body=event_body
        ).execute(num_retries=5)
        return False
    except HttpError as error:
        if "duplicate" in str(error).lower() or "already exists" in str(error).lower():
            uid = event_body.get('iCalUID')
            logger.debug(f"Event with UID '{uid}' already exists in calendar, attempting patch fallback...")
            existing_list = service.events().list(
                calendarId=calendar_id,
                iCalUID=uid,
                showDeleted=True
            ).execute(num_retries=3).get('items', [])
            if existing_list:
                service.events().patch(
                    calendarId=calendar_id,
                    eventId=existing_list[0]['id'],
                    body=event_body
                ).execute(num_retries=5)
                return True
        raise


def _fetch_all_google_events(service, calendar_id: str) -> list[dict[str, Any]]:
    """Fetch all existing events from a Google Calendar with pagination."""
    all_events = []
    page_token = None
    while True:
        resp = service.events().list(
            calendarId=calendar_id,
            pageToken=page_token,
            maxResults=2500,
            singleEvents=False
        ).execute(num_retries=5)
        all_events.extend(resp.get('items', []))
        page_token = resp.get('nextPageToken')
        if not page_token:
            break
    return all_events


def _delete_removed_events(service, calendar_id: str, current_uids: set, existing_events: list[dict[str, Any]]) -> int:
    """Delete events from Google Calendar that are no longer present in the current sync set."""
    deleted_count = 0
    for g_event in existing_events:
        i_cal_uid = g_event.get('iCalUID', '')
        if i_cal_uid.startswith('trakt-') and i_cal_uid not in current_uids:
            event_id = g_event.get('id')
            summary = g_event.get('summary', 'Unknown Event')
            try:
                service.events().delete(
                    calendarId=calendar_id,
                    eventId=event_id,
                    sendUpdates='none'
                ).execute(num_retries=5)
                logger.info(f"Deleted removed/dropped event '{summary}' from Google Calendar.")
                deleted_count += 1
                time.sleep(0.05)
            except HttpError as error:
                logger.warning(f"Could not delete removed event '{summary}': {error}")
    return deleted_count


def sync_ical_to_google_calendar(
    service,
    calendar_id: str,
    ical_obj: Calendar
):
    """Sync VEVENT items from an icalendar.Calendar object into a Google Calendar and purge removed events."""
    events = [comp for comp in ical_obj.subcomponents if comp.name == 'VEVENT']
    current_uids = {str(event.get('uid')) for event in events if event.get('uid')}
    logger.info(f"Syncing {len(events)} event(s) to Google Calendar ID '{calendar_id}'...")

    updated_count = 0
    created_count = 0

    # 1. Fetch all existing events from Google Calendar to check for deletions & fast upsert lookup
    existing_google_events = _fetch_all_google_events(service, calendar_id)
    existing_events_by_uid = {
        g_ev.get('iCalUID'): g_ev
        for g_ev in existing_google_events
        if g_ev.get('iCalUID')
    }

    # 2. Delete events no longer in Trakt watchlist / progress (dropped or removed shows)
    deleted_count = _delete_removed_events(service, calendar_id, current_uids, existing_google_events)

    # 3. Create or update active events
    for event in events:
        summary = str(event.get('summary', 'Untitled Event'))
        description = str(event.get('description', ''))
        uid = str(event.get('uid', ''))

        dtstart = event.get('dtstart').dt
        dtend = event.get('dtend').dt

        # Format start/end for Google Calendar API
        if hasattr(dtstart, 'hour'):  # datetime
            start_dict = {'dateTime': dtstart.isoformat()}
            end_dict = {'dateTime': dtend.isoformat()}
            event_body = {
                'summary': summary,
                'description': description,
                'iCalUID': uid,
                'start': start_dict,
                'end': end_dict
            }
        else:  # date (all-day event, e.g. movies)
            start_dict = {'date': dtstart.isoformat()}
            end_dict = {'date': dtend.isoformat()}
            event_body = {
                'summary': summary,
                'description': description,
                'iCalUID': uid,
                'start': start_dict,
                'end': end_dict,
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'popup', 'minutes': 900},   # 9:00 AM day before (15h / 900m before 00:00)
                        {'method': 'popup', 'minutes': -540}   # 9:00 AM day of event (9h after 00:00)
                    ]
                }
            }

        existing_event = existing_events_by_uid.get(uid)
        if existing_event and not _event_has_changed(existing_event, event_body):
            # Event is completely up-to-date, skip redundant API call
            continue

        try:
            is_update = _upsert_single_event(service, calendar_id, event_body, existing_event)
            if is_update:
                updated_count += 1
            else:
                created_count += 1
            # Request pacing to keep rate under 500 requests per 100 seconds
            time.sleep(0.05)
        except HttpError as error:
            logger.warning(f"Error syncing event '{summary}': {error}")

    logger.info(f"Sync complete for '{calendar_id}': {created_count} created, {updated_count} updated, {deleted_count} deleted.")
