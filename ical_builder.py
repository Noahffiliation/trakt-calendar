"""
iCalendar (.ics) Builder for Trakt Watchlist Movies and TV Shows.
"""

from datetime import datetime, date, timedelta, timezone
from typing import Any, Dict, List, Optional
import logging
from dateutil import parser
from icalendar import Calendar, Event, Alarm

logger = logging.getLogger(__name__)


def parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse an ISO datetime or date string into a timezone-aware UTC datetime or None."""
    if not dt_str:
        return None
    try:
        dt = parser.isoparse(dt_str)
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt
        elif isinstance(dt, date):
            return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
    except Exception as e:
        logger.warning(f"Could not parse date string '{dt_str}': {e}")
        return None


def parse_date(date_str: Optional[str]) -> Optional[date]:
    """Parse a date string (YYYY-MM-DD) or ISO string into a date object."""
    if not date_str:
        return None
    try:
        dt = parser.isoparse(date_str)
        if isinstance(dt, datetime):
            return dt.date()
        elif isinstance(dt, date):
            return dt
    except Exception as e:
        logger.warning(f"Could not parse date string '{date_str}': {e}")
        return None


def build_movies_calendar(
    movies: List[Dict[str, Any]],
    start_cutoff: Optional[datetime] = None,
    calendar_name: str = "Trakt Movies"
) -> Calendar:
    """Build an iCalendar containing only movie release events."""
    return build_calendar(movies=movies, episodes=[], shows_premieres=[], start_cutoff=start_cutoff, calendar_name=calendar_name)


def build_shows_calendar(
    episodes: List[Dict[str, Any]],
    shows_premieres: Optional[List[Dict[str, Any]]] = None,
    start_cutoff: Optional[datetime] = None,
    calendar_name: str = "Trakt Shows"
) -> Calendar:
    """Build an iCalendar containing only TV show episode events."""
    return build_calendar(movies=[], episodes=episodes, shows_premieres=shows_premieres, start_cutoff=start_cutoff, calendar_name=calendar_name)


DEFAULT_NO_OVERVIEW = "No overview available."


def _create_movie_event(item: Dict[str, Any], start_cutoff: Optional[datetime]) -> Optional[Event]:
    movie = item.get("movie", item)
    released_str = movie.get("released")
    if not released_str:
        return None

    release_dt = parse_datetime(released_str)
    if not release_dt or (start_cutoff and release_dt < start_cutoff):
        return None

    event = Event()
    title = movie.get("title", "Untitled Movie")
    year = movie.get("year")
    summary_title = f"🎬 {title} ({year})" if year else f"🎬 {title}"
    event.add('summary', summary_title)

    release_d = parse_date(released_str) or release_dt.date()
    event.add('dtstart', release_d)
    event.add('dtend', release_d + timedelta(days=1))

    trakt_id = movie.get("ids", {}).get("trakt", "unknown")
    slug = movie.get("ids", {}).get("slug", "")
    overview = movie.get("overview") or DEFAULT_NO_OVERVIEW

    desc_parts = [overview]
    if slug:
        desc_parts.append(f"\nTrakt: https://app.trakt.tv/movies/{slug}")

    event.add('description', "\n".join(desc_parts))
    event.add('uid', f"trakt-movie-{trakt_id}@trakt-calendar")
    event.add('dtstamp', datetime.now(timezone.utc))

    # Add notifications: Day before release at 9:00 AM & Day of release at 9:00 AM
    alarm_day_before = Alarm()
    alarm_day_before.add('action', 'DISPLAY')
    alarm_day_before.add('description', f"Movie releasing tomorrow: {title}")
    alarm_day_before.add('trigger', timedelta(hours=-15))

    alarm_same_day = Alarm()
    alarm_same_day.add('action', 'DISPLAY')
    alarm_same_day.add('description', f"Movie releasing today: {title}")
    alarm_same_day.add('trigger', timedelta(hours=9))

    event.add_component(alarm_day_before)
    event.add_component(alarm_same_day)

    return event


def _create_episode_event(ep_info: Dict[str, Any], start_cutoff: Optional[datetime]) -> Optional[Event]:
    show = ep_info.get("show", {})
    episode = ep_info.get("episode", {})

    first_aired_str = episode.get("first_aired")
    if not first_aired_str:
        return None

    air_dt = parse_datetime(first_aired_str)
    if not air_dt or (start_cutoff and air_dt < start_cutoff):
        return None

    event = Event()
    show_title = show.get("title", "Unknown Show")
    season_num = episode.get("season", 0)
    ep_num = episode.get("number", 0)
    ep_title = episode.get("title") or f"Episode {ep_num}"
    
    summary = f"📺 {show_title} - S{season_num:02d}E{ep_num:02d} - {ep_title}"
    event.add('summary', summary)

    event.add('dtstart', air_dt)
    runtime = episode.get("runtime") or show.get("runtime") or 45
    event.add('dtend', air_dt + timedelta(minutes=runtime))

    trakt_id = show.get("ids", {}).get("trakt", "unknown")
    slug = show.get("ids", {}).get("slug", "")
    ep_overview = episode.get("overview") or show.get("overview") or DEFAULT_NO_OVERVIEW

    desc_parts = [f"Season {season_num}, Episode {ep_num}: {ep_title}", f"\n{ep_overview}"]
    if slug and season_num and ep_num:
        desc_parts.append(f"\nTrakt: https://app.trakt.tv/shows/{slug}/seasons/{season_num}/episodes/{ep_num}")
    elif slug:
        desc_parts.append(f"\nTrakt: https://app.trakt.tv/shows/{slug}")

    event.add('description', "\n".join(desc_parts))
    event.add('uid', f"trakt-ep-{trakt_id}-s{season_num}e{ep_num}@trakt-calendar")
    event.add('dtstamp', datetime.now(timezone.utc))

    return event


def _create_premiere_event(show_item: Dict[str, Any], start_cutoff: Optional[datetime]) -> Optional[Event]:
    show = show_item.get("show", show_item)
    first_aired_str = show.get("first_aired")
    if not first_aired_str:
        return None

    air_dt = parse_datetime(first_aired_str)
    if not air_dt or (start_cutoff and air_dt < start_cutoff):
        return None

    event = Event()
    show_title = show.get("title", "Unknown Show")
    summary = f"📺 {show_title} (Series Premiere)"
    event.add('summary', summary)

    event.add('dtstart', air_dt)
    runtime = show.get("runtime") or 60
    event.add('dtend', air_dt + timedelta(minutes=runtime))

    trakt_id = show.get("ids", {}).get("trakt", "unknown")
    slug = show.get("ids", {}).get("slug", "")
    overview = show.get("overview") or DEFAULT_NO_OVERVIEW

    desc_parts = [overview]
    if slug:
        desc_parts.append(f"\nTrakt: https://app.trakt.tv/shows/{slug}")
    
    event.add('description', "\n".join(desc_parts))
    event.add('uid', f"trakt-show-{trakt_id}-premiere@trakt-calendar")
    event.add('dtstamp', datetime.now(timezone.utc))

    return event


def build_calendar(
    movies: List[Dict[str, Any]],
    episodes: List[Dict[str, Any]],
    shows_premieres: Optional[List[Dict[str, Any]]] = None,
    start_cutoff: Optional[datetime] = None,
    calendar_name: str = "Trakt Watchlist"
) -> Calendar:
    """
    Build an icalendar.Calendar object populated with VEVENT entries.
    """
    cal = Calendar()
    cal.add('prodid', '-//Trakt Watchlist Calendar Generator//NONSGML v1.0//EN')
    cal.add('version', '2.0')
    cal.add('x-wr-calname', calendar_name)
    cal.add('x-wr-timezone', 'UTC')
    cal.add('calscale', 'GREGORIAN')

    for item in movies:
        ev = _create_movie_event(item, start_cutoff)
        if ev:
            cal.add_component(ev)

    for ep_info in episodes:
        ev = _create_episode_event(ep_info, start_cutoff)
        if ev:
            cal.add_component(ev)

    if shows_premieres:
        for show_item in shows_premieres:
            ev = _create_premiere_event(show_item, start_cutoff)
            if ev:
                cal.add_component(ev)

    return cal
