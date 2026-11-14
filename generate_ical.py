"""
Main entry point to generate iCal (.ics) files for Trakt Movies and TV Shows,
and optionally sync directly to Google Calendar via Google Calendar API.
"""

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ical_builder import (
    build_calendar,
    build_movies_calendar,
    build_shows_calendar,
    build_streaming_calendar,
    parse_datetime,
)
from trakt_api import TraktAPIError, TraktClient

# Load .env if present
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate separate and combined iCal files for Trakt Movies & TV Shows and sync to Google Calendar."
    )
    parser.add_argument(
        "-c",
        "--client-id",
        default=os.getenv("TRAKT_CLIENT_ID"),
        help="Trakt API Client ID (or set TRAKT_CLIENT_ID env var)",
    )
    parser.add_argument(
        "-t",
        "--access-token",
        default=os.getenv("TRAKT_ACCESS_TOKEN"),
        help="Trakt OAuth Access Token for private watchlists (or set TRAKT_ACCESS_TOKEN env var)",
    )
    parser.add_argument(
        "-u",
        "--username",
        default=os.getenv("TRAKT_USERNAME"),
        help="Trakt Username for public watchlists (or set TRAKT_USERNAME env var)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=os.getenv("OUTPUT_FILE", "trakt_watchlist.ics"),
        help="Combined output .ics file path (default: trakt_watchlist.ics)",
    )
    parser.add_argument(
        "-mo",
        "--movies-output",
        default=os.getenv("MOVIES_OUTPUT_FILE", "trakt_movies.ics"),
        help="Movies output .ics file path (default: trakt_movies.ics)",
    )
    parser.add_argument(
        "-so",
        "--shows-output",
        default=os.getenv("SHOWS_OUTPUT_FILE", "trakt_shows.ics"),
        help="Shows output .ics file path (default: trakt_shows.ics)",
    )
    parser.add_argument(
        "-sto",
        "--streaming-output",
        default=os.getenv("STREAMING_OUTPUT_FILE", "trakt_streaming.ics"),
        help="Streaming output .ics file path (default: trakt_streaming.ics)",
    )
    parser.add_argument(
        "-d",
        "--days-back",
        type=int,
        default=30,
        help="Number of days in the past to include (default: 30 days / 1 month ago)",
    )
    parser.add_argument(
        "--no-watched",
        action="store_true",
        help="Disable including shows with watched progress (only include watchlist items)",
    )
    parser.add_argument(
        "-g",
        "--sync-google",
        action="store_true",
        default=os.getenv("SYNC_GOOGLE", "").lower() in ("true", "1", "yes"),
        help="Directly sync events to Google Calendar using Google Calendar API (requires credentials.json or service_account.json)",
    )
    parser.add_argument(
        "--movies-calendar-id",
        default=os.getenv("GOOGLE_CALENDAR_ID_MOVIES") or os.getenv("GOOGLE_MOVIES_CALENDAR_ID"),
        help="Google Calendar ID for Trakt Movies (optional, overrides default calendar search/create)",
    )
    parser.add_argument(
        "--shows-calendar-id",
        default=os.getenv("GOOGLE_CALENDAR_ID_SHOWS") or os.getenv("GOOGLE_SHOWS_CALENDAR_ID"),
        help="Google Calendar ID for Trakt TV Shows (optional, overrides default calendar search/create)",
    )
    parser.add_argument(
        "--streaming-calendar-id",
        default=os.getenv("GOOGLE_CALENDAR_ID_STREAMING")
        or os.getenv("GOOGLE_STREAMING_CALENDAR_ID"),
        help="Google Calendar ID for Trakt Streaming (optional, overrides default calendar search/create)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose debug logging")
    return parser.parse_args()


def validate_safe_path(file_path_str: str, base_dir: Path | None = None) -> Path:
    """Validates that a file path string resolves safely within the designated base directory
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
            raise ValueError(
                f"Path traversal security error: '{file_path_str}' escapes allowed base directory '{base_dir}'"
            )
        return resolved_path

    resolved_path = target_path.resolve()
    if not target_path.is_absolute():
        cwd = Path.cwd().resolve()
        try:
            resolved_path.relative_to(cwd)
        except ValueError:
            raise ValueError(
                f"Path traversal security error: '{file_path_str}' escapes working directory '{cwd}'"
            )
    return resolved_path


def write_ics(calendar, file_path_str: str, base_dir: Path | None = None):
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
        logger.error(
            "Trakt Client ID is required. Pass --client-id or set TRAKT_CLIENT_ID in environment or .env file."
        )
        sys.exit(1)

    if not access_token and not username:
        logger.error("Either Trakt --access-token or --username must be specified.")
        sys.exit(1)

    logger.info(
        f"Initializing Trakt Client (Auth mode: {'OAuth Token' if access_token else f'Public user ({username})'})"
    )
    return TraktClient(client_id=client_id, access_token=access_token, username=username)


def _add_candidate_show(
    show: dict, hidden_show_ids: set, processed_show_ids: set, candidate_shows: list
) -> bool:
    show_id = show.get("ids", {}).get("trakt")
    if show_id and show_id not in hidden_show_ids and show_id not in processed_show_ids:
        candidate_shows.append(show)
        processed_show_ids.add(show_id)
        return True
    return False


def _is_episode_watched_after_reset(ep: dict[str, Any], reset_dt: datetime | None) -> bool:
    if ep.get("number") is None or ep.get("plays", 1) <= 0:
        return False
    if reset_dt is None:
        return True
    ep_watched_dt = parse_datetime(ep.get("last_watched_at"))
    return ep_watched_dt is None or ep_watched_dt >= reset_dt


def _extract_watched_episodes_for_show(watched_item: dict[str, Any]) -> set[tuple[int, int]]:
    """Extract a set of watched (season_number, episode_number) tuples for a show from Trakt watched data."""
    watched_eps: set[tuple[int, int]] = set()
    reset_dt = parse_datetime(watched_item.get("reset_at"))
    for season in watched_item.get("seasons", []):
        season_num = season.get("number")
        if season_num is None:
            continue
        for ep in season.get("episodes", []):
            if _is_episode_watched_after_reset(ep, reset_dt):
                watched_eps.add((season_num, ep["number"]))
    return watched_eps


def _apply_watched_shows(
    watched_shows_data: list[dict[str, Any]],
    hidden_show_ids: set[int],
    processed_show_ids: set[int],
    candidate_shows: list[dict[str, Any]],
    direct_episodes: list[dict[str, Any]],
) -> int:
    watched_by_show: dict[int, set[tuple[int, int]]] = {}
    added_count = 0

    for item in watched_shows_data:
        show = item.get("show", item)
        show_id = show.get("ids", {}).get("trakt")
        if show_id:
            watched_by_show.setdefault(show_id, set()).update(
                _extract_watched_episodes_for_show(item)
            )
        if _add_candidate_show(show, hidden_show_ids, processed_show_ids, candidate_shows):
            added_count += 1

    for show in candidate_shows:
        show_id = show.get("ids", {}).get("trakt")
        if show_id in watched_by_show:
            show["watched_episodes"] = watched_by_show[show_id]

    for ep_item in direct_episodes:
        show_id = ep_item.get("show", {}).get("ids", {}).get("trakt")
        ep = ep_item.get("episode", {})
        if (
            show_id in watched_by_show
            and (ep.get("season"), ep.get("number")) in watched_by_show[show_id]
        ):
            ep_item["watched"] = True

    return added_count


def _merge_watched_movies(
    movies: list[dict[str, Any]], watched_movies_data: list[dict[str, Any]]
) -> int:
    movies_by_id: dict[int, dict[str, Any]] = {}
    for item in movies:
        movie_id = item.get("movie", item).get("ids", {}).get("trakt")
        if movie_id:
            movies_by_id[movie_id] = item

    added_count = 0
    for item in watched_movies_data:
        if item.get("plays", 1) <= 0:
            continue
        movie = item.get("movie", item)
        movie_id = movie.get("ids", {}).get("trakt")
        if not movie_id:
            continue
        if movie_id in movies_by_id:
            existing = movies_by_id[movie_id]
            existing["watched"] = True
            existing.get("movie", existing)["watched"] = True
        else:
            watched_movie = {**movie, "watched": True}
            entry = {"type": "movie", "movie": watched_movie, "watched": True}
            movies.append(entry)
            movies_by_id[movie_id] = entry
            added_count += 1
    return added_count


def _categorize_items(
    watchlist_items: list, hidden_show_ids: set, client: TraktClient, include_watched: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    movies: list[dict[str, Any]] = []
    candidate_shows: list[dict[str, Any]] = []
    direct_episodes: list[dict[str, Any]] = []
    processed_show_ids: set[int] = set()

    for item in watchlist_items:
        item_type = item.get("type")
        if item_type == "movie":
            movies.append(item)
        elif item_type == "show":
            _add_candidate_show(
                item.get("show", item), hidden_show_ids, processed_show_ids, candidate_shows
            )
        elif item_type == "episode":
            direct_episodes.append(item)

    if include_watched:
        logger.info("Fetching shows with watched progress...")
        watched_shows_data = client.get_watched_shows()
        added_count = _apply_watched_shows(
            watched_shows_data,
            hidden_show_ids,
            processed_show_ids,
            candidate_shows,
            direct_episodes,
        )
        logger.info(f"Added {added_count} show(s) in progress (excluding dropped/hidden shows).")

        logger.info("Fetching watched movies...")
        watched_movies_data = client.get_watched_movies()
        added_movies = _merge_watched_movies(movies, watched_movies_data)
        logger.info(f"Added {added_movies} watched movie(s).")

    logger.info(
        f"Categorized total: {len(movies)} movie(s), {len(candidate_shows)} show(s) to check, {len(direct_episodes)} direct episode(s)."
    )
    return movies, candidate_shows, direct_episodes


def _is_episode_watched(
    ep: dict[str, Any], watched_eps: set[tuple[int, int]], item_watched: bool = False
) -> bool:
    return bool(
        item_watched or ep.get("watched") or (ep.get("season"), ep.get("number")) in watched_eps
    )


def _fetch_show_episodes(
    client: TraktClient, candidate_shows: list, direct_episodes: list
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    episodes_to_include: list[dict[str, Any]] = []
    standalone_premieres: list[dict[str, Any]] = []

    for item in direct_episodes:
        show = item.get("show", {})
        ep = item.get("episode", {})
        watched_eps = show.get("watched_episodes", set())
        episodes_to_include.append(
            {
                "show": show,
                "episode": ep,
                "watched": _is_episode_watched(ep, watched_eps, bool(item.get("watched"))),
            }
        )

    for show in candidate_shows:
        show_id = show.get("ids", {}).get("trakt")
        show_title = show.get("title", f"Show {show_id}")
        show_status = show.get("status")
        watched_eps = show.get("watched_episodes", set())

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
                episodes_to_include.append(
                    {
                        "show": show,
                        "episode": ep,
                        "watched": _is_episode_watched(ep, watched_eps),
                    }
                )
                found_episodes += 1

        if found_episodes == 0:
            standalone_premieres.append(
                {"show": show, "watched": bool(show.get("watched") or watched_eps)}
            )

    return episodes_to_include, standalone_premieres


def _is_recent_or_upcoming_movie(movie: dict[str, Any], start_cutoff: datetime | None) -> bool:
    """Return True if the movie is unreleased or released recently enough to have an upcoming/recent streaming release."""
    if start_cutoff is None:
        return True

    min_theatrical_dt = start_cutoff - timedelta(days=365)
    released_str = movie.get("released")
    if released_str:
        release_dt = parse_datetime(released_str)
        if release_dt and release_dt < min_theatrical_dt:
            return False
    else:
        year = movie.get("year")
        if isinstance(year, int) and year < min_theatrical_dt.year:
            return False

    return True


HOME_RELEASE_TYPES = ("digital", "streaming", "physical", "tv")


def _fetch_movie_streaming_releases(
    client: TraktClient,
    movies: list[dict[str, Any]],
    start_cutoff: datetime | None = None,
) -> list[dict[str, Any]]:
    streaming_movies: list[dict[str, Any]] = []

    for item in movies:
        movie = item.get("movie", item)
        if not _is_recent_or_upcoming_movie(movie, start_cutoff):
            continue

        movie_id = movie.get("ids", {}).get("trakt")
        movie_title = movie.get("title", f"Movie {movie_id}")

        if not movie_id:
            continue

        try:
            releases = client.get_movie_releases(movie_id)
        except TraktAPIError as e:
            logger.warning(f"Could not fetch streaming releases for '{movie_title}': {e}")
            continue

        movie["releases"] = releases

        home_releases = [
            r
            for r in releases
            if r.get("release_type") in HOME_RELEASE_TYPES and r.get("release_date")
        ]
        if not home_releases:
            continue

        home_releases.sort(key=lambda r: (str(r.get("release_date")), 0 if r.get("note") else 1))
        chosen = home_releases[0]
        streaming_movies.append(
            {
                "movie": movie,
                "released": chosen["release_date"],
                "note": chosen.get("note"),
                "watched": bool(item.get("watched") or movie.get("watched")),
            }
        )

    logger.info(f"Found {len(streaming_movies)} movie(s) with streaming/digital release dates.")
    return streaming_movies


def _sync_to_google(
    movies_cal,
    shows_cal,
    streaming_cal=None,
    movies_cal_id_override: str | None = None,
    shows_cal_id_override: str | None = None,
    streaming_cal_id_override: str | None = None,
):
    logger.info("Initializing Direct Google Calendar API Sync...")
    try:
        from google_sync import (
            get_google_calendar_service,
            get_or_create_calendar,
            sync_ical_to_google_calendar,
        )

        service = get_google_calendar_service()

        movies_cal_id = get_or_create_calendar(
            service, "Trakt Movies", calendar_id=movies_cal_id_override
        )
        sync_ical_to_google_calendar(service, movies_cal_id, movies_cal)

        shows_cal_id = get_or_create_calendar(
            service, "Trakt TV Shows", calendar_id=shows_cal_id_override
        )
        sync_ical_to_google_calendar(service, shows_cal_id, shows_cal)

        if streaming_cal is not None:
            streaming_cal_id = get_or_create_calendar(
                service, "Trakt Streaming", calendar_id=streaming_cal_id_override
            )
            sync_ical_to_google_calendar(service, streaming_cal_id, streaming_cal)

        logger.info("✅ Direct Google Calendar API sync complete!")
    except Exception:
        logger.exception("❌ Google Calendar API sync error")


def main():
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    client = _init_client(args)

    now_utc = datetime.now(UTC)
    start_cutoff = now_utc - timedelta(days=args.days_back)
    logger.info(
        f"Filtering items released/airing on or after: {start_cutoff.strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )

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

    episodes_to_include, standalone_premieres = _fetch_show_episodes(
        client, candidate_shows, direct_episodes
    )

    streaming_movies = _fetch_movie_streaming_releases(client, movies, start_cutoff=start_cutoff)

    # Build Calendars
    movies_cal = build_movies_calendar(
        movies=movies, start_cutoff=start_cutoff, calendar_name="Trakt Movies"
    )
    shows_cal = build_shows_calendar(
        episodes=episodes_to_include,
        shows_premieres=standalone_premieres,
        start_cutoff=start_cutoff,
        calendar_name="Trakt TV Shows",
    )
    streaming_cal = build_streaming_calendar(
        streaming_movies=streaming_movies,
        start_cutoff=start_cutoff,
        calendar_name="Trakt Streaming",
    )
    combined_cal = build_calendar(
        movies=movies,
        episodes=episodes_to_include,
        shows_premieres=standalone_premieres,
        streaming_movies=streaming_movies,
        start_cutoff=start_cutoff,
        calendar_name="Trakt Watchlist & Progress",
    )

    # Write .ics Files
    write_ics(movies_cal, args.movies_output)
    write_ics(shows_cal, args.shows_output)
    write_ics(streaming_cal, args.streaming_output)
    write_ics(combined_cal, args.output)

    # Optional Direct Google Calendar Sync
    if args.sync_google:
        _sync_to_google(
            movies_cal,
            shows_cal,
            streaming_cal,
            movies_cal_id_override=args.movies_calendar_id,
            shows_cal_id_override=args.shows_calendar_id,
            streaming_cal_id_override=args.streaming_calendar_id,
        )

    logger.info("Done generating all calendars!")


if __name__ == "__main__":
    main()
