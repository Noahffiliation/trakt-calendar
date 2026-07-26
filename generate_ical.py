"""
Main entry point to generate iCal (.ics) files for Trakt Movies and TV Shows,
and optionally sync directly to Google Calendar via Google Calendar API.
"""

import argparse
from datetime import datetime, timedelta, timezone
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from trakt_api import TraktClient, TraktAPIError
from ical_builder import build_calendar, build_movies_calendar, build_shows_calendar

# Load .env if present
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate separate and combined iCal files for Trakt Movies & TV Shows and sync to Google Calendar."
    )
    parser.add_argument(
        "-c", "--client-id",
        default=os.getenv("TRAKT_CLIENT_ID"),
        help="Trakt API Client ID (or set TRAKT_CLIENT_ID env var)"
    )
    parser.add_argument(
        "-t", "--access-token",
        default=os.getenv("TRAKT_ACCESS_TOKEN"),
        help="Trakt OAuth Access Token for private watchlists (or set TRAKT_ACCESS_TOKEN env var)"
    )
    parser.add_argument(
        "-u", "--username",
        default=os.getenv("TRAKT_USERNAME"),
        help="Trakt Username for public watchlists (or set TRAKT_USERNAME env var)"
    )
    parser.add_argument(
        "-o", "--output",
        default=os.getenv("OUTPUT_FILE", "trakt_watchlist.ics"),
        help="Combined output .ics file path (default: trakt_watchlist.ics)"
    )
    parser.add_argument(
        "-mo", "--movies-output",
        default=os.getenv("MOVIES_OUTPUT_FILE", "trakt_movies.ics"),
        help="Movies output .ics file path (default: trakt_movies.ics)"
    )
    parser.add_argument(
        "-so", "--shows-output",
        default=os.getenv("SHOWS_OUTPUT_FILE", "trakt_shows.ics"),
        help="Shows output .ics file path (default: trakt_shows.ics)"
    )
    parser.add_argument(
        "-d", "--days-back",
        type=int,
        default=30,
        help="Number of days in the past to include (default: 30 days / 1 month ago)"
    )
    parser.add_argument(
        "--no-watched",
        action="store_true",
        help="Disable including shows with watched progress (only include watchlist items)"
    )
    parser.add_argument(
        "-g", "--sync-google",
        action="store_true",
        default=os.getenv("SYNC_GOOGLE", "").lower() in ("true", "1", "yes"),
        help="Directly sync events to Google Calendar using Google Calendar API (requires credentials.json or service_account.json)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose debug logging"
    )
    return parser.parse_args()


def validate_safe_path(file_path_str: str, base_dir: Path = None) -> Path:
    """
    Validates that a file path string resolves safely within the designated base directory
    or working directory to prevent path traversal security vulnerabilities (S8707).
    """
    target_path = Path(file_path_str)

    if base_dir is not None:
        base_dir = base_dir.resolve()
        if not target_path.is_absolute():
            target_path = base_dir / target_path
        resolved_path = target_path.resolve()
        try:
            resolved_path.relative_to(base_dir)
        except ValueError:
            raise ValueError(f"Path traversal security error: '{file_path_str}' escapes allowed base directory '{base_dir}'")
        return resolved_path

    resolved_path = target_path.resolve()
    if not target_path.is_absolute():
        cwd = Path.cwd().resolve()
        try:
            resolved_path.relative_to(cwd)
        except ValueError:
            raise ValueError(f"Path traversal security error: '{file_path_str}' escapes working directory '{cwd}'")
    return resolved_path


def write_ics(calendar, file_path_str: str, base_dir: Path = None):
    path = validate_safe_path(file_path_str, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(calendar.to_ical())
    event_count = len([comp for comp in calendar.subcomponents if comp.name == "VEVENT"])
    logger.info(f"Generated '{path}' containing {event_count} event(s).")



def _init_client(args) -> TraktClient:
    client_id = args.client_id
    access_token = args.access_token
    username = args.username

    if not client_id:
        logger.error("Trakt Client ID is required. Pass --client-id or set TRAKT_CLIENT_ID in environment or .env file.")
        sys.exit(1)

    if not access_token and not username:
        logger.error("Either Trakt --access-token or --username must be specified.")
        sys.exit(1)

    logger.info(f"Initializing Trakt Client (Auth mode: {'OAuth Token' if access_token else f'Public user ({username})'})")
    return TraktClient(client_id=client_id, access_token=access_token, username=username)


def _add_candidate_show(show: dict, hidden_show_ids: set, processed_show_ids: set, candidate_shows: list) -> bool:
    show_id = show.get("ids", {}).get("trakt")
    if show_id and show_id not in hidden_show_ids and show_id not in processed_show_ids:
        candidate_shows.append(show)
        processed_show_ids.add(show_id)
        return True
    return False


def _categorize_items(
    watchlist_items: list,
    hidden_show_ids: set,
    client: TraktClient,
    include_watched: bool
):
    movies = []
    candidate_shows = []
    direct_episodes = []
    processed_show_ids = set()

    for item in watchlist_items:
        item_type = item.get("type")
        if item_type == "movie":
            movies.append(item)
        elif item_type == "show":
            _add_candidate_show(item.get("show", item), hidden_show_ids, processed_show_ids, candidate_shows)
        elif item_type == "episode":
            direct_episodes.append(item)

    if include_watched:
        logger.info("Fetching shows with watched progress...")
        watched_shows_data = client.get_watched_shows()
        added_count = sum(
            1 for item in watched_shows_data
            if _add_candidate_show(item.get("show", item), hidden_show_ids, processed_show_ids, candidate_shows)
        )
        logger.info(f"Added {added_count} show(s) in progress (excluding dropped/hidden shows).")

    logger.info(f"Categorized total: {len(movies)} movie(s), {len(candidate_shows)} show(s) to check, {len(direct_episodes)} direct episode(s).")
    return movies, candidate_shows, direct_episodes


def _fetch_show_episodes(client: TraktClient, candidate_shows: list, direct_episodes: list):
    episodes_to_include = []
    standalone_premieres = []

    for item in direct_episodes:
        episodes_to_include.append({
            "show": item.get("show", {}),
            "episode": item.get("episode", {})
        })

    for show in candidate_shows:
        show_id = show.get("ids", {}).get("trakt")
        show_title = show.get("title", f"Show {show_id}")
        show_status = show.get("status")

        if not show_id:
            continue

        try:
            seasons = client.get_show_seasons_with_episodes(show_id, show_status=show_status)
        except TraktAPIError as e:
            logger.warning(f"Could not fetch episodes for '{show_title}': {e}")
            continue

        found_episodes = 0
        for season in seasons:
            for ep in season.get("episodes", []):
                episodes_to_include.append({"show": show, "episode": ep})
                found_episodes += 1

        if found_episodes == 0:
            standalone_premieres.append({"show": show})

    return episodes_to_include, standalone_premieres


def _sync_to_google(movies_cal, shows_cal):
    logger.info("Initializing Direct Google Calendar API Sync...")
    try:
        from google_sync import get_google_calendar_service, get_or_create_calendar, sync_ical_to_google_calendar
        service = get_google_calendar_service()

        movies_cal_id = get_or_create_calendar(service, "Trakt Movies")
        sync_ical_to_google_calendar(service, movies_cal_id, movies_cal)

        shows_cal_id = get_or_create_calendar(service, "Trakt TV Shows")
        sync_ical_to_google_calendar(service, shows_cal_id, shows_cal)

        logger.info("✅ Direct Google Calendar API sync complete!")
    except Exception:
        logger.exception("❌ Google Calendar API sync error")


def main():
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    client = _init_client(args)

    now_utc = datetime.now(timezone.utc)
    start_cutoff = now_utc - timedelta(days=args.days_back)
    logger.info(f"Filtering items released/airing on or after: {start_cutoff.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    hidden_show_ids = client.get_hidden_show_ids()

    try:
        watchlist_items = client.get_watchlist()
    except TraktAPIError:
        logger.exception("Failed to retrieve watchlist")
        sys.exit(1)

    logger.info(f"Fetched {len(watchlist_items)} total item(s) from Trakt watchlist.")

    movies, candidate_shows, direct_episodes = _categorize_items(
        watchlist_items, hidden_show_ids, client, include_watched=not args.no_watched
    )

    episodes_to_include, standalone_premieres = _fetch_show_episodes(client, candidate_shows, direct_episodes)

    # Build Calendars
    movies_cal = build_movies_calendar(movies=movies, start_cutoff=start_cutoff, calendar_name="Trakt Movies")
    shows_cal = build_shows_calendar(episodes=episodes_to_include, shows_premieres=standalone_premieres, start_cutoff=start_cutoff, calendar_name="Trakt TV Shows")
    combined_cal = build_calendar(movies=movies, episodes=episodes_to_include, shows_premieres=standalone_premieres, start_cutoff=start_cutoff, calendar_name="Trakt Watchlist & Progress")

    # Write .ics Files
    write_ics(movies_cal, args.movies_output)
    write_ics(shows_cal, args.shows_output)
    write_ics(combined_cal, args.output)

    # Optional Direct Google Calendar Sync
    if args.sync_google:
        _sync_to_google(movies_cal, shows_cal)

    logger.info("Done generating all calendars!")


if __name__ == "__main__":
    main()
