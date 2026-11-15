"""
Google Calendar API Sync module using Service Account or OAuth Credentials,
with direct Gmail API event notifications.
"""

import base64
import contextlib
import html
import logging
import os.path
import time
import webbrowser
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from icalendar import Calendar

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
]
SERVICE_ACCOUNT_FILE = "service_account.json"
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
DEFAULT_CALENDAR_NAME = "Trakt Calendar"


def _save_token_file(token_path: str, creds: Any) -> None:
    json_data = creds.to_json()
    if not isinstance(json_data, str):
        return
    with contextlib.suppress(OSError), open(token_path, "w", encoding="utf-8") as token_file:
        token_file.write(json_data)


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
            _save_token_file(token_path, creds)
            return creds
        except Exception as e:
            logger.warning(f"Could not refresh token: {e}")

    if not os.path.exists(credentials_path):
        raise FileNotFoundError(
            f"Neither '{SERVICE_ACCOUNT_FILE}' nor '{credentials_path}' was found.\n"
            "Please provide a service_account.json or credentials.json file."
        )

    flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
    try:
        creds = flow.run_local_server(port=0)
    except webbrowser.Error:
        logger.info(
            "No runnable browser found; please open the printed authorization URL in your browser."
        )
        creds = flow.run_local_server(port=0, open_browser=False)
    _save_token_file(token_path, creds)

    return creds


def get_google_calendar_service(
    service_account_path: str = SERVICE_ACCOUNT_FILE,
    credentials_path: str = CREDENTIALS_FILE,
    token_path: str = TOKEN_FILE,
):
    """
    Authenticate and return a Google Calendar API service instance.
    Prefers service_account.json if present; otherwise falls back to OAuth credentials.
    """
    if os.path.exists(service_account_path):
        logger.info(f"Using Service Account authentication from '{service_account_path}'")
        creds = service_account.Credentials.from_service_account_file(
            service_account_path, scopes=SCOPES
        )
        return build("calendar", "v3", credentials=creds)

    creds = _get_oauth_credentials(credentials_path, token_path)
    return build("calendar", "v3", credentials=creds)


def get_gmail_service(
    credentials_path: str = CREDENTIALS_FILE,
    token_path: str = TOKEN_FILE,
) -> Any | None:
    """
    Authenticate via OAuth (credentials.json / token.json) and return a Gmail API service instance.
    Returns None if OAuth credentials are not available.
    """
    try:
        creds = _get_oauth_credentials(credentials_path, token_path)
        return build("gmail", "v1", credentials=creds)
    except Exception as e:
        logger.warning(f"Gmail API service unavailable for email notifications: {e}")
        return None


def disable_calendar_other_notifications(
    calendar_ids: list[str],
    credentials_path: str = CREDENTIALS_FILE,
    token_path: str = TOKEN_FILE,
) -> None:
    """
    Clears Google Calendar's built-in 'Other notifications' (calendar-notification@google.com)
    on the user's CalendarList entries so Google Calendar does not send duplicate RSVP-style emails.
    """
    if not calendar_ids or not (os.path.exists(token_path) or os.path.exists(credentials_path)):
        return
    try:
        creds = _get_oauth_credentials(credentials_path, token_path)
        user_cal_service = build("calendar", "v3", credentials=creds)
        for cal_id in calendar_ids:
            if not cal_id:
                continue
            with contextlib.suppress(Exception):
                user_cal_service.calendarList().patch(
                    calendarId=cal_id,
                    body={"notificationSettings": {"notifications": []}},
                ).execute(num_retries=3)
    except Exception as e:
        logger.debug(f"Could not update user calendarList notificationSettings: {e}")


def _format_event_when(event_data: dict[str, Any]) -> str:
    """Format start/end fields of a Google Calendar event dict into a human-readable string."""
    start = event_data.get("start", {})
    end = event_data.get("end", {})

    start_date = start.get("date")
    if start_date:
        try:
            dt_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
            return f"{dt_obj.strftime('%a, %b %d, %Y')} (All day)"
        except ValueError:
            return f"{start_date} (All day)"

    start_dt_str = start.get("dateTime")
    if start_dt_str:
        try:
            start_dt = datetime.fromisoformat(start_dt_str)
            end_dt_str = end.get("dateTime")
            if end_dt_str:
                end_dt = datetime.fromisoformat(end_dt_str)
                return (
                    f"{start_dt.strftime('%a, %b %d, %Y, %H:%M')} - {end_dt.strftime('%H:%M UTC')}"
                )
            return start_dt.strftime("%a, %b %d, %Y, %H:%M UTC")
        except ValueError:
            return str(start_dt_str)

    return "Unknown date"


def _collect_event_changes(
    previous_event: dict[str, Any] | None,
    summary: str,
    when_str: str,
    description: str,
) -> tuple[list[str], list[str]]:
    """Build plain-text and HTML diff entries showing what changed from previous_event."""
    if not previous_event:
        return [], []

    text_changes: list[str] = []
    html_changes: list[str] = []

    old_summary = str(previous_event.get("summary") or "")
    if old_summary and old_summary != summary:
        text_changes.append(f"- Title: {old_summary} -> {summary}")
        html_changes.append(
            f"<li><strong>Title:</strong> <del style='color:#888;'>{html.escape(old_summary)}</del> "
            f"&rarr; <strong>{html.escape(summary)}</strong></li>"
        )

    old_when = _format_event_when(previous_event)
    if old_when != when_str:
        text_changes.append(f"- When: {old_when} -> {when_str}")
        html_changes.append(
            f"<li><strong>When:</strong> <del style='color:#888;'>{html.escape(old_when)}</del> "
            f"&rarr; <strong>{html.escape(when_str)}</strong></li>"
        )

    old_description = str(previous_event.get("description") or "").strip()
    if old_description != description:
        old_desc_display = old_description or "(empty)"
        new_desc_display = description or "(empty)"
        text_changes.append(
            f"- Description:\n  Before: {old_desc_display}\n  After:  {new_desc_display}"
        )
        old_desc_html = html.escape(old_desc_display).replace("\n", "<br>")
        new_desc_html = html.escape(new_desc_display).replace("\n", "<br>")
        html_changes.append(
            f"<li><strong>Description:</strong><br>"
            f"<del style='color:#888;'>{old_desc_html}</del><br>"
            f"&rarr; {new_desc_html}</li>"
        )

    return text_changes, html_changes


def _build_notification_email_message(
    action: str,
    event_data: dict[str, Any],
    notify_email: str,
    calendar_name: str = DEFAULT_CALENDAR_NAME,
    previous_event: dict[str, Any] | None = None,
) -> dict[str, str]:
    """
    Construct a standard MIME email without text/calendar or EventReservation schema
    so mobile Gmail notifications display standard Delete / Mark as Read actions.
    """
    summary = str(event_data.get("summary") or "Untitled Event")
    description = str(event_data.get("description") or "").strip()
    when_str = _format_event_when(event_data)

    action_label = {
        "created": "New event",
        "updated": "Changed event",
        "deleted": "Canceled event",
    }.get(action.lower(), "Updated event")

    subject = f"{action_label}: {summary} @ {when_str}"

    text_lines = [
        f"{action_label} on {calendar_name}",
        "",
        f"Event: {summary}",
        f"When:  {when_str}",
    ]
    text_changes, html_changes = _collect_event_changes(
        previous_event, summary, when_str, description
    )
    if text_changes:
        text_lines.extend(["", "What Changed:", *text_changes])

    if description:
        text_lines.extend(["", description])

    escaped_desc = html.escape(description).replace("\n", "<br>") if description else ""
    changes_block = (
        f"<p><strong>What Changed:</strong></p><ul>{''.join(html_changes)}</ul>"
        if html_changes
        else ""
    )
    html_body = (
        f"<div style='font-family: sans-serif; line-height: 1.5;'>"
        f"<p><strong>{html.escape(action_label)}</strong> on "
        f"<em>{html.escape(calendar_name)}</em></p>"
        f"<p><strong>Event:</strong> {html.escape(summary)}<br>"
        f"<strong>When:</strong> {html.escape(when_str)}</p>"
        f"{changes_block}"
        f"{f'<p>{escaped_desc}</p>' if escaped_desc else ''}"
        f"</div>"
    )

    msg = EmailMessage()
    msg["To"] = notify_email
    msg["From"] = notify_email
    msg["Subject"] = subject
    msg.set_content("\n".join(text_lines))
    msg.add_alternative(html_body, subtype="html")

    encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return {"raw": encoded}


def send_event_notification_email(
    gmail_service: Any,
    notify_email: str | None,
    action: str,
    event_data: dict[str, Any],
    calendar_name: str = DEFAULT_CALENDAR_NAME,
    previous_event: dict[str, Any] | None = None,
) -> bool:
    """Send an individual plain email notification via the Gmail API."""
    if not gmail_service or not notify_email:
        return False
    try:
        raw_body = _build_notification_email_message(
            action=action,
            event_data=event_data,
            notify_email=notify_email,
            calendar_name=calendar_name,
            previous_event=previous_event,
        )
        gmail_service.users().messages().send(userId="me", body=raw_body).execute(num_retries=5)
        # Gmail API messages.send costs 100 quota units against a 15,000 units/minute/user limit
        time.sleep(0.5)
        return True
    except Exception as e:
        logger.warning(
            f"Could not send '{action}' email notification for '{event_data.get('summary')}': {e}"
        )
        return False


def share_calendar_with_email(service, calendar_id: str, share_email: str | None = None):
    """Share a Google Calendar with a user email address via ACL without sending notification emails."""
    email = (
        share_email or os.getenv("GOOGLE_SHARE_EMAIL") or os.getenv("GOOGLE_CALENDAR_SHARE_EMAIL")
    )
    if not email:
        return
    try:
        # Check if already shared to avoid redundant API calls and emails
        existing_acls = (
            service.acl().list(calendarId=calendar_id).execute(num_retries=3).get("items", [])
        )
        for acl in existing_acls:
            if acl.get("scope", {}).get("value", "").lower() == email.lower():
                return  # Already shared

        rule = {"scope": {"type": "user", "value": email}, "role": "writer"}
        service.acl().insert(calendarId=calendar_id, body=rule, sendNotifications=False).execute(
            num_retries=3
        )
        logger.info(f"Shared Google Calendar ID '{calendar_id}' with '{email}'")
    except HttpError as error:
        if "alreadyExists" not in str(error) and "duplicate" not in str(error).lower():
            logger.warning(f"Could not share calendar with '{email}': {error}")


def get_or_create_calendar(
    service,
    calendar_name: str,
    share_email: str | None = None,
    calendar_id: str | None = None,
) -> str:
    """Retrieve existing Google Calendar ID by explicit ID, name, or create a new dedicated calendar."""
    if calendar_id:
        logger.info(
            f"Using explicitly configured Google Calendar ID '{calendar_id}' for '{calendar_name}'"
        )
        share_calendar_with_email(service, calendar_id, share_email)
        return calendar_id

    # Check env vars for explicit calendar ID override
    env_suffix = calendar_name.upper().replace(" ", "_")
    env_id = os.getenv(f"GOOGLE_CALENDAR_ID_{env_suffix}") or os.getenv(
        f"GOOGLE_{env_suffix}_CALENDAR_ID"
    )
    if env_id:
        logger.info(
            f"Found Google Calendar ID '{env_id}' for '{calendar_name}' from environment variable"
        )
        share_calendar_with_email(service, env_id, share_email)
        return env_id

    page_token = None
    while True:
        calendar_list = service.calendarList().list(pageToken=page_token).execute()
        for calendar_list_entry in calendar_list.get("items", []):
            if calendar_list_entry.get("summary") == calendar_name:
                cal_id = calendar_list_entry["id"]
                logger.info(f"Found existing Google Calendar '{calendar_name}' (ID: {cal_id})")
                share_calendar_with_email(service, cal_id, share_email)
                return cal_id
        page_token = calendar_list.get("nextPageToken")
        if not page_token:
            break

    # Create calendar if not found
    calendar_body = {"summary": calendar_name, "timeZone": "UTC"}
    created_calendar = service.calendars().insert(body=calendar_body).execute()
    cal_id = created_calendar["id"]
    logger.info(f"Created new Google Calendar '{calendar_name}' (ID: {cal_id})")
    share_calendar_with_email(service, cal_id, share_email)
    return cal_id


def _normalize_datetime_str(dt_str: str | None) -> str | None:
    """Normalize ISO-8601 / RFC3339 datetime strings (e.g. 'Z' vs '+00:00') to UTC ISO format."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str).astimezone(UTC).isoformat()
    except ValueError:
        return dt_str


def _event_schedule_changed(existing_event: dict[str, Any], new_body: dict[str, Any]) -> bool:
    """Check if start or end date/dateTime of an event has changed."""
    existing_start = existing_event.get("start", {})
    new_start = new_body.get("start", {})
    if existing_start.get("date") != new_start.get("date") or _normalize_datetime_str(
        existing_start.get("dateTime")
    ) != _normalize_datetime_str(new_start.get("dateTime")):
        return True

    existing_end = existing_event.get("end", {})
    new_end = new_body.get("end", {})
    return existing_end.get("date") != new_end.get("date") or _normalize_datetime_str(
        existing_end.get("dateTime")
    ) != _normalize_datetime_str(new_end.get("dateTime"))


def _strip_leading_status_emoji(summary: str) -> str:
    """Strip leading media/watched emoji prefix so watched-only updates are recognized."""
    for prefix in ("✅ ", "🎬 ", "🍿 ", "📺 "):
        if summary.startswith(prefix):
            return summary[len(prefix) :]
    return summary


def _should_notify_event_update(
    existing_event: dict[str, Any] | None, new_body: dict[str, Any]
) -> bool:
    """
    Return True if an existing event's title (ignoring watched checkmark emoji),
    description, or schedule (start/end date or time) changed, warranting a 'Changed event' email.
    """
    if not existing_event:
        return True
    old_title = _strip_leading_status_emoji(str(existing_event.get("summary") or ""))
    new_title = _strip_leading_status_emoji(str(new_body.get("summary") or ""))
    if old_title != new_title:
        return True
    old_desc = str(existing_event.get("description") or "").strip()
    new_desc = str(new_body.get("description") or "").strip()
    if old_desc != new_desc:
        return True
    return _event_schedule_changed(existing_event, new_body)


def _event_content_changed(existing_event: dict[str, Any], new_body: dict[str, Any]) -> bool:
    """Check if core event content (status, summary, description, start, end) has changed."""
    existing_status = existing_event.get("status") or "confirmed"
    new_status = new_body.get("status") or "confirmed"
    if existing_status != new_status:
        return True
    if existing_event.get("summary") != new_body.get("summary"):
        return True
    if (existing_event.get("description") or "") != (new_body.get("description") or ""):
        return True
    return _event_schedule_changed(existing_event, new_body)


def _event_has_changed(existing_event: dict[str, Any], new_body: dict[str, Any]) -> bool:
    """Check if meaningful event attributes (content or leftover attendees) have changed."""
    if bool(existing_event.get("attendees")) != bool(new_body.get("attendees")):
        return True
    return _event_content_changed(existing_event, new_body)


def _upsert_single_event(
    service: Any,
    calendar_id: str,
    event_body: dict[str, Any],
    existing_event: dict[str, Any] | None = None,
) -> bool:
    """
    Inserts or patches an event in Google Calendar, returning True if updated, False if created.
    Ensures 'status': 'confirmed' so tombstoned/cancelled events are revived, and suppresses
    Google Calendar's built-in email notifications (sendUpdates='none') because notifications
    are sent directly via the Gmail API without RSVP metadata.
    Uses num_retries=5 for automatic exponential backoff on 403/429 rate limit errors.
    """
    body_with_status = dict(event_body)
    body_with_status.setdefault("status", "confirmed")

    if existing_event:
        event_id = existing_event["id"]
        if existing_event.get("attendees"):
            body_with_status.setdefault("attendees", [])

        service.events().patch(
            calendarId=calendar_id,
            eventId=event_id,
            body=body_with_status,
            sendUpdates="none",
        ).execute(num_retries=5)
        return True

    try:
        service.events().insert(
            calendarId=calendar_id,
            body=body_with_status,
            sendUpdates="none",
        ).execute(num_retries=5)
        return False
    except HttpError as error:
        if "duplicate" in str(error).lower() or "already exists" in str(error).lower():
            uid = body_with_status.get("iCalUID")
            logger.debug(
                f"Event with UID '{uid}' already exists in calendar, attempting patch fallback..."
            )
            existing_list = (
                service.events()
                .list(calendarId=calendar_id, iCalUID=uid, showDeleted=True)
                .execute(num_retries=3)
                .get("items", [])
            )
            if existing_list:
                body_with_status.setdefault("attendees", [])
                service.events().patch(
                    calendarId=calendar_id,
                    eventId=existing_list[0]["id"],
                    body=body_with_status,
                    sendUpdates="none",
                ).execute(num_retries=5)
                return True
        raise


def _fetch_all_google_events(
    service, calendar_id: str, show_deleted: bool = True
) -> list[dict[str, Any]]:
    """Fetch all existing events (including deleted/tombstoned ones) from a Google Calendar with pagination."""
    all_events = []
    page_token = None
    while True:
        resp = (
            service.events()
            .list(
                calendarId=calendar_id,
                pageToken=page_token,
                maxResults=2500,
                singleEvents=False,
                showDeleted=show_deleted,
            )
            .execute(num_retries=5)
        )
        all_events.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return all_events


def _delete_removed_events(
    service,
    calendar_id: str,
    current_uids: set,
    existing_events: list[dict[str, Any]],
    gmail_service: Any = None,
    notify_email: str | None = None,
    calendar_name: str = DEFAULT_CALENDAR_NAME,
) -> int:
    """Delete events from Google Calendar that are no longer present in the current sync set."""
    deleted_count = 0
    for g_event in existing_events:
        if g_event.get("status") == "cancelled":
            continue
        i_cal_uid = g_event.get("iCalUID", "")
        if i_cal_uid.startswith("trakt-") and i_cal_uid not in current_uids:
            event_id = g_event.get("id")
            summary = g_event.get("summary", "Unknown Event")
            try:
                service.events().delete(
                    calendarId=calendar_id,
                    eventId=event_id,
                    sendUpdates="none",
                ).execute(num_retries=5)
                logger.info(f"Deleted removed/dropped event '{summary}' from Google Calendar.")
                send_event_notification_email(
                    gmail_service=gmail_service,
                    notify_email=notify_email,
                    action="deleted",
                    event_data=g_event,
                    calendar_name=calendar_name,
                )
                deleted_count += 1
                time.sleep(0.05)
            except HttpError as error:
                logger.warning(f"Could not delete removed event '{summary}': {error}")
    return deleted_count


def _build_google_event_body(event: Any) -> dict[str, Any]:
    """Convert an icalendar VEVENT component into a Google Calendar API event resource body."""
    summary = str(event.get("summary", "Untitled Event"))
    description = str(event.get("description", ""))
    uid = str(event.get("uid", ""))

    dtstart = event.get("dtstart").dt
    dtend = event.get("dtend").dt

    if hasattr(dtstart, "hour"):  # datetime
        return {
            "summary": summary,
            "description": description,
            "iCalUID": uid,
            "start": {"dateTime": dtstart.isoformat()},
            "end": {"dateTime": dtend.isoformat()},
            "status": "confirmed",
        }

    return {
        "summary": summary,
        "description": description,
        "iCalUID": uid,
        "start": {"date": dtstart.isoformat()},
        "end": {"date": dtend.isoformat()},
        "status": "confirmed",
        "reminders": {
            "useDefault": False,
            "overrides": [
                {
                    "method": "popup",
                    "minutes": 900,
                },  # 9:00 AM day before (15h / 900m before 00:00)
                {
                    "method": "popup",
                    "minutes": 0,
                },  # 9:00 AM day of event (0m at start of day)
            ],
        },
    }


def _sync_single_vevent(
    service: Any,
    calendar_id: str,
    event: Any,
    existing_events_by_uid: dict[str | None, dict[str, Any]],
    gmail_service: Any,
    notify_email: str | None,
    calendar_name: str,
) -> tuple[int, int]:
    """Sync one VEVENT into Google Calendar and send notification email; returns (created, updated)."""
    event_body = _build_google_event_body(event)
    summary = event_body["summary"]
    uid = event_body["iCalUID"]

    existing_event = existing_events_by_uid.get(uid)
    if existing_event and not _event_has_changed(existing_event, event_body):
        return 0, 0

    should_notify_update = _should_notify_event_update(existing_event, event_body)
    was_cancelled = bool(existing_event and existing_event.get("status") == "cancelled")

    try:
        is_update = _upsert_single_event(service, calendar_id, event_body, existing_event)
    except HttpError as error:
        logger.warning(f"Error syncing event '{summary}': {error}")
        return 0, 0

    time.sleep(0.05)
    if is_update and not was_cancelled:
        if should_notify_update:
            send_event_notification_email(
                gmail_service=gmail_service,
                notify_email=notify_email,
                action="updated",
                event_data=event_body,
                calendar_name=calendar_name,
                previous_event=existing_event,
            )
        return 0, 1

    send_event_notification_email(
        gmail_service=gmail_service,
        notify_email=notify_email,
        action="created",
        event_data=event_body,
        calendar_name=calendar_name,
    )
    return (0, 1) if is_update else (1, 0)


def sync_ical_to_google_calendar(
    service,
    calendar_id: str,
    ical_obj: Calendar,
    gmail_service: Any = None,
    notify_email: str | None = None,
    calendar_name: str | None = None,
):
    """Sync VEVENT items from an icalendar.Calendar object into a Google Calendar and purge removed events."""
    events = [comp for comp in ical_obj.subcomponents if comp.name == "VEVENT"]
    current_uids = {str(event.get("uid")) for event in events if event.get("uid")}
    resolved_calendar_name = (
        calendar_name or str(ical_obj.get("x-wr-calname") or "") or DEFAULT_CALENDAR_NAME
    )
    resolved_notify_email = (
        notify_email or os.getenv("GOOGLE_SHARE_EMAIL") or os.getenv("GOOGLE_CALENDAR_SHARE_EMAIL")
    )
    logger.info(f"Syncing {len(events)} event(s) to Google Calendar ID '{calendar_id}'...")

    updated_count = 0
    created_count = 0

    # 1. Fetch all existing events from Google Calendar (including deleted tombstones)
    existing_google_events = _fetch_all_google_events(service, calendar_id, show_deleted=True)
    existing_events_by_uid = {
        g_ev.get("iCalUID"): g_ev for g_ev in existing_google_events if g_ev.get("iCalUID")
    }

    # 2. Delete events no longer in Trakt watchlist / progress (dropped or removed shows)
    deleted_count = _delete_removed_events(
        service,
        calendar_id,
        current_uids,
        existing_google_events,
        gmail_service=gmail_service,
        notify_email=resolved_notify_email,
        calendar_name=resolved_calendar_name,
    )

    # 3. Create or update active events
    for event in events:
        created_delta, updated_delta = _sync_single_vevent(
            service=service,
            calendar_id=calendar_id,
            event=event,
            existing_events_by_uid=existing_events_by_uid,
            gmail_service=gmail_service,
            notify_email=resolved_notify_email,
            calendar_name=resolved_calendar_name,
        )
        created_count += created_delta
        updated_count += updated_delta

    logger.info(
        f"Sync complete for '{calendar_id}': {created_count} created, {updated_count} updated, {deleted_count} deleted."
    )
