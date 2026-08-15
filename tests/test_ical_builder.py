"""
Unit tests for iCalendar builder and date filtering logic.
"""

from datetime import datetime, timedelta, timezone
from icalendar import Calendar
from ical_builder import build_calendar, build_movies_calendar, build_shows_calendar, parse_datetime, parse_date


def test_parse_datetime_and_date():
    dt = parse_datetime("2026-07-25T12:00:00.000Z")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 7
    assert dt.day == 25
    assert dt.tzinfo == timezone.utc

    d = parse_date("2026-08-15")
    assert d is not None
    assert d.year == 2026
    assert d.month == 8
    assert d.day == 15


def test_build_calendar_date_filtering():
    now_utc = datetime.now(timezone.utc)
    one_month_ago = now_utc - timedelta(days=30)
    two_months_ago = now_utc - timedelta(days=60)
    one_month_future = now_utc + timedelta(days=30)
    one_year_future = now_utc + timedelta(days=365)

    movies = [
        {
            "movie": {
                "title": "Old Movie",
                "year": 2026,
                "released": two_months_ago.strftime("%Y-%m-%d"),
                "ids": {"trakt": 1}
            }
        },
        {
            "movie": {
                "title": "Recent Movie",
                "year": 2026,
                "released": (now_utc - timedelta(days=15)).strftime("%Y-%m-%d"),
                "ids": {"trakt": 2}
            }
        },
        {
            "movie": {
                "title": "Future Movie",
                "year": 2026,
                "released": one_month_future.strftime("%Y-%m-%d"),
                "ids": {"trakt": 3}
            }
        },
        {
            "movie": {
                "title": "Far Future Movie",
                "year": 2027,
                "released": one_year_future.strftime("%Y-%m-%d"),
                "ids": {"trakt": 4}
            }
        }
    ]

    episodes = [
        {
            "show": {"title": "Test Show", "ids": {"trakt": 100}},
            "episode": {
                "season": 1,
                "number": 1,
                "title": "Past Ep",
                "first_aired": two_months_ago.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "ids": {"trakt": 101}
            }
        },
        {
            "show": {"title": "Test Show", "ids": {"trakt": 100}},
            "episode": {
                "season": 1,
                "number": 2,
                "title": "Recent Ep",
                "first_aired": (now_utc - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "ids": {"trakt": 102}
            }
        },
        {
            "show": {"title": "Test Show", "ids": {"trakt": 100}},
            "episode": {
                "season": 1,
                "number": 3,
                "title": "Future Ep",
                "first_aired": one_month_future.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "ids": {"trakt": 103}
            }
        }
    ]

    cal = build_calendar(
        movies=movies,
        episodes=episodes,
        start_cutoff=one_month_ago,
        calendar_name="Test Trakt Calendar"
    )

    events = [comp for comp in cal.subcomponents if comp.name == "VEVENT"]
    assert len(events) == 5

    # Separate Movies Calendar test
    movies_cal = build_movies_calendar(movies=movies, start_cutoff=one_month_ago)
    movie_events = [comp for comp in movies_cal.subcomponents if comp.name == "VEVENT"]
    assert len(movie_events) == 3

    # Separate Shows Calendar test
    shows_cal = build_shows_calendar(episodes=episodes, start_cutoff=one_month_ago)
    show_events = [comp for comp in shows_cal.subcomponents if comp.name == "VEVENT"]
    assert len(show_events) == 2


def test_ical_valid_ics_formatting():
    movies = [
        {
            "movie": {
                "title": "Inception",
                "year": 2010,
                "released": "2010-07-16",
                "overview": "A thief who steals corporate secrets through dream-sharing technology.",
                "rating": 8.8,
                "genres": ["Action", "Sci-Fi"],
                "ids": {"trakt": 166, "slug": "inception-2010", "imdb": "tt1375666"}
            }
        }
    ]

    cal = build_calendar(movies=movies, episodes=[], start_cutoff=None)
    ics_data = cal.to_ical()

    parsed_cal = Calendar.from_ical(ics_data)
    assert parsed_cal["x-wr-calname"] == "Trakt Watchlist"
    parsed_events = [c for c in parsed_cal.subcomponents if c.name == "VEVENT"]
    assert len(parsed_events) == 1
    assert "Inception" in str(parsed_events[0]["summary"])
    assert "trakt-movie-166@trakt-calendar" in str(parsed_events[0]["uid"])


def test_movie_all_day_events():
    from datetime import date
    movies = [
        {
            "movie": {
                "title": "ISO Release Movie",
                "year": 2026,
                "released": "2026-07-25T18:00:00.000Z",
                "ids": {"trakt": 999}
            }
        }
    ]

    cal = build_movies_calendar(movies=movies)
    parsed_events = [c for c in cal.subcomponents if c.name == "VEVENT"]
    assert len(parsed_events) == 1
    
    dtstart = parsed_events[0].get("dtstart").dt
    dtend = parsed_events[0].get("dtend").dt

    assert type(dtstart) is date
    assert type(dtend) is date
    assert dtstart == date(2026, 7, 25)
    assert dtend == date(2026, 7, 26)

    # Check notification alarms
    alarms = [c for c in parsed_events[0].subcomponents if c.name == "VALARM"]
    assert len(alarms) == 2
    triggers = [a.get("trigger").dt for a in alarms]
    assert timedelta(hours=-15) in triggers
    assert timedelta(hours=9) in triggers


def test_parse_datetime_and_date_edge_cases():
    assert parse_datetime(None) is None
    assert parse_datetime("") is None
    assert parse_datetime("not-a-date") is None

    # Test date string in parse_datetime
    dt_from_date = parse_datetime("2026-08-15")
    assert dt_from_date == datetime(2026, 8, 15, tzinfo=timezone.utc)

    # Test naive datetime string
    dt_naive = parse_datetime("2026-08-15T10:00:00")
    assert dt_naive.tzinfo == timezone.utc

    assert parse_date(None) is None
    assert parse_date("") is None
    assert parse_date("not-a-date") is None
    assert parse_date("2026-08-15T12:00:00.000Z") is not None


def test_create_movie_event_missing_or_filtered():
    from ical_builder import _create_movie_event
    now_utc = datetime.now(timezone.utc)

    # Missing released
    assert _create_movie_event({"movie": {"title": "No Release"}}, start_cutoff=None) is None

    # Released before cutoff
    assert _create_movie_event(
        {"movie": {"title": "Old", "released": "2020-01-01"}},
        start_cutoff=now_utc
    ) is None

    # Movie without year or slug
    ev = _create_movie_event({"title": "Basic Movie", "released": "2026-09-01"}, start_cutoff=None)
    assert ev is not None
    assert "🎬 Basic Movie" in str(ev.get("summary"))


def test_create_episode_event_edge_cases():
    from ical_builder import _create_episode_event
    now_utc = datetime.now(timezone.utc)

    # Missing first_aired
    assert _create_episode_event({"show": {}, "episode": {}}, start_cutoff=None) is None

    # Aired before cutoff
    assert _create_episode_event(
        {"show": {}, "episode": {"first_aired": "2020-01-01T00:00:00.000Z"}},
        start_cutoff=now_utc
    ) is None

    # Episode with slug but no season/number
    ev = _create_episode_event({
        "show": {"title": "Show Slug", "ids": {"slug": "show-slug"}},
        "episode": {"first_aired": "2026-09-01T00:00:00.000Z"}
    }, start_cutoff=None)
    assert ev is not None
    assert "https://app.trakt.tv/shows/show-slug" in str(ev.get("description"))


def test_create_premiere_event_and_calendar_integration():
    from ical_builder import _create_premiere_event
    now_utc = datetime.now(timezone.utc)

    # Missing first_aired
    assert _create_premiere_event({"show": {"title": "No Air"}}, start_cutoff=None) is None

    # Aired before cutoff
    assert _create_premiere_event(
        {"show": {"title": "Old Show", "first_aired": "2020-01-01T00:00:00.000Z"}},
        start_cutoff=now_utc
    ) is None

    # Valid premiere with slug and overview
    prem = {
        "show": {
            "title": "New Series",
            "first_aired": "2026-09-01T00:00:00.000Z",
            "runtime": 50,
            "overview": "A brand new series overview.",
            "ids": {"trakt": 777, "slug": "new-series"}
        }
    }
    ev = _create_premiere_event(prem, start_cutoff=None)
    assert ev is not None
    assert "📺 New Series (Series Premiere)" in str(ev.get("summary"))
    assert "https://app.trakt.tv/shows/new-series" in str(ev.get("description"))

    # Premiere without slug or overview
    prem_bare = {
        "show": {
            "title": "Bare Show",
            "first_aired": "2026-09-01T00:00:00.000Z"
        }
    }
    ev_bare = _create_premiere_event(prem_bare, start_cutoff=None)
    assert ev_bare is not None

def test_parse_datetime_and_date_mocked_date_instance():
    from unittest.mock import patch
    from datetime import date
    with patch("ical_builder.parser.isoparse", return_value=date(2026, 5, 1)):
        dt = parse_datetime("2026-05-01")
        assert dt == datetime(2026, 5, 1, tzinfo=timezone.utc)

        d = parse_date("2026-05-01")
        assert d == date(2026, 5, 1)


def test_create_episode_event_with_season_and_episode_slug():
    from ical_builder import _create_episode_event
    ev = _create_episode_event({
        "show": {"title": "Hit Show", "ids": {"trakt": 123, "slug": "hit-show"}},
        "episode": {
            "title": "Season Finale",
            "season": 2,
            "number": 10,
            "runtime": "60",
            "first_aired": "2026-09-01T20:00:00.000Z"
        }
    }, start_cutoff=None)
def test_safe_runtime_exceptions():
    from ical_builder import _safe_runtime
    assert _safe_runtime("not-a-number", default=45) == 45
    assert _safe_runtime([], default=45) == 45
    assert _safe_runtime(0, default=45) == 45


def test_build_calendar_with_premieres():
    prem = {
        "show": {
            "title": "Combined Premiere",
            "first_aired": "2026-09-01T00:00:00.000Z"
        }
    }
    cal = build_calendar(movies=[], episodes=[], shows_premieres=[prem], start_cutoff=None)
    events = [c for c in cal.subcomponents if c.name == "VEVENT"]
    assert len(events) == 1





