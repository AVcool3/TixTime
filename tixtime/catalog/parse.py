"""Parsers that recover structure the SeatGeek export drops on the floor.

The supplied CSV has `performerIds`, `eventScore` and `popularityScore` fully
redacted, so the only route to venue geography and team identity is the `url`
column and the free-text `name`. Both were profiled against all 7,118 rows
before these parsers were written; the coverage numbers in the docstrings are
measured, not aspirational.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------
# Every url in the export has exactly four path segments. They come in two
# shapes:
#
#   game shape   (5,799 rows)
#     <home-team>-tickets / <M-D-YYYY-city-region-venue> / <league> / <id>
#   non-game shape (1,319 rows -- stadium_tours + a few one-off baseball events)
#     <event-slug>-tickets / <taxonomy> / <YYYY-MM-DD-h-ampm> / <id>
#
# Only the game shape carries geography. The three venueIds used by non-game
# rows all also appear in game rows, so venue geography backfills to 170/170
# venues by venueId.

_GAME_LEAGUES = frozenset({"nba", "nfl", "mlb", "nhl"})

# Region tokens that appear between city and venue in the middle segment.
# US states plus the country names SeatGeek substitutes for non-US events
# (Canadian venues use "canada", not the province).
_REGIONS = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "district-of-columbia", "florida", "georgia",
    "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new-hampshire", "new-jersey", "new-mexico", "new-york", "north-carolina",
    "north-dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode-island", "south-carolina", "south-dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington", "west-virginia", "wisconsin",
    "wyoming",
    # non-US
    "canada", "mexico", "uk", "germany", "japan", "australia", "ireland",
)
# Longest-first so "new-york" wins over a hypothetical "york".
_REGIONS_BY_LEN = tuple(sorted(_REGIONS, key=len, reverse=True))

_DATE_PREFIX = re.compile(r"^\d{1,2}-\d{1,2}-\d{4}-(?P<rest>.+)$")

_REGION_DISPLAY = {
    "district-of-columbia": "DC",
    "uk": "UK",
    "usa": "USA",
}


@dataclass(frozen=True)
class ParsedUrl:
    """Structure recovered from a SeatGeek event url."""

    home_team_slug: str | None
    league: str | None
    city: str | None
    region: str | None
    venue_slug: str | None

    @property
    def has_geo(self) -> bool:
        return bool(self.city and self.region and self.venue_slug)


def parse_url(url: str) -> ParsedUrl:
    """Split a SeatGeek event url into its parts.

    Returns a ParsedUrl with None fields rather than raising, so a single
    unexpected url shape can never abort ingestion of the whole export.
    """
    if not isinstance(url, str) or not url:
        return ParsedUrl(None, None, None, None, None)

    path = url.split("seatgeek.com/", 1)[-1].strip("/")
    parts = path.split("/")
    if len(parts) != 4:
        return ParsedUrl(None, None, None, None, None)

    slug_seg, mid_seg, third_seg, _event_id = parts
    team_slug = slug_seg[: -len("-tickets")] if slug_seg.endswith("-tickets") else slug_seg

    if third_seg not in _GAME_LEAGUES:
        # Non-game shape: no geography encoded, and the "team" slug is really
        # the event name. Backfilled later from venueId.
        return ParsedUrl(None, None, None, None, None)

    match = _DATE_PREFIX.match(mid_seg)
    if not match:
        return ParsedUrl(team_slug, third_seg, None, None, None)

    rest = match.group("rest")
    for region in _REGIONS_BY_LEN:
        idx = rest.find(f"-{region}-")
        if idx > 0:
            return ParsedUrl(
                home_team_slug=team_slug,
                league=third_seg,
                city=rest[:idx],
                region=region,
                venue_slug=rest[idx + len(region) + 2:],
            )

    # Seven rows in the export are neutral-site or special events whose middle
    # segment has no region token at all (e.g. a university stadium practice).
    # Keep the league and team; leave geography for the venueId backfill.
    return ParsedUrl(team_slug, third_seg, None, None, None)


def parse_url_geo_fallback(url: str, known_cities: frozenset[str]) -> ParsedUrl:
    """Second-pass geography for urls whose middle segment has no region token.

    Exactly two venues in the export hit this path -- a draft party on a lawn
    and a stadium practice at a university -- because SeatGeek omits the state
    for those. Both cities (glendale, college-park) do appear in other rows, so
    rather than hard-coding them we match against the city vocabulary learned
    from the successfully parsed urls. Region is left None; the caller records
    the geography as low confidence.
    """
    base = parse_url(url)
    if base.has_geo or base.league is None:
        return base

    path = url.split("seatgeek.com/", 1)[-1].strip("/")
    parts = path.split("/")
    if len(parts) != 4:
        return base
    match = _DATE_PREFIX.match(parts[1])
    if not match:
        return base

    rest = match.group("rest")
    for city in sorted(known_cities, key=len, reverse=True):
        if rest.startswith(f"{city}-"):
            return ParsedUrl(
                home_team_slug=base.home_team_slug,
                league=base.league,
                city=city,
                region=None,
                venue_slug=rest[len(city) + 1:],
            )
    return base


def titleize_slug(slug: str | None) -> str | None:
    """'chase-center' -> 'Chase Center'. Handles the known abbreviations."""
    # NaN arrives here from pandas columns, and NaN is truthy.
    if not isinstance(slug, str) or not slug:
        return None
    if slug in _REGION_DISPLAY:
        return _REGION_DISPLAY[slug]
    words = []
    for word in slug.split("-"):
        if not word:
            continue
        if word.lower() in {"at", "of", "the", "and"} and words:
            words.append(word.lower())
        elif word.lower() in {"usa", "ubs", "sap", "kfc", "att", "td", "bmo", "pnc", "mvp"}:
            words.append(word.upper())
        else:
            words.append(word.capitalize())
    return " ".join(words)


# ---------------------------------------------------------------------------
# Event name parsing
# ---------------------------------------------------------------------------
# 5,719 of 7,118 names use "<Away> at <Home>". Names also carry a postseason
# prefix or suffix that is the single strongest demand signal available in the
# export: "World Series", "Stanley Cup Finals", "ALCS", "NFC Divisional",
# "Spring Training", "Preseason", ...

# Ordered most specific first: a Game 7 Stanley Cup Final should not match as
# a generic "conference" tier.
_POSTSEASON_PATTERNS: tuple[tuple[str, str, int], ...] = (
    # (regex, tier label, demand rank 0-6 where 6 is the hottest ticket)
    (r"super bowl", "super_bowl", 6),
    (r"world series", "world_series", 6),
    (r"stanley cup final", "stanley_cup_final", 6),
    (r"nba finals", "nba_finals", 6),
    (r"(afc|nfc) championship", "conference_final", 5),
    (r"(eastern|western) conference final", "conference_final", 5),
    (r"conference final", "conference_final", 5),
    (r"\b(alcs|nlcs)\b", "league_championship", 5),
    (r"(afc|nfc) divisional", "divisional", 4),
    (r"\b(alds|nlds)\b", "divisional", 4),
    (r"(afc|nfc) wild ?card", "wild_card", 4),
    (r"wild ?card", "wild_card", 4),
    (r"(eastern|western) conference (first round|semifinal)", "playoff_round", 4),
    (r"stanley cup", "playoff_round", 4),
    (r"play-?in", "play_in", 3),
    (r"nba cup", "in_season_tournament", 3),
    (r"opening day", "opening_day", 3),
    (r"spring breakout", "exhibition", 1),
    (r"spring training", "spring_training", 0),
    (r"preseason", "preseason", 0),
)
_COMPILED_POSTSEASON = tuple(
    (re.compile(pat, re.IGNORECASE), label, rank) for pat, label, rank in _POSTSEASON_PATTERNS
)

# Non-game inventory sold under an event name: tours, tailgates, suites,
# parties. These are not games and must not be modelled as such.
_NON_GAME_PATTERNS = re.compile(
    r"tailgate|tailgreeter|\bsuite\b|\brsvp\b|watch party|draft party|"
    r"stadium (tour|practice)|owner'?s experience|pregame party|vip",
    re.IGNORECASE,
)

_AT_SPLIT = re.compile(r"\s+at\s+", re.IGNORECASE)
_VS_SPLIT = re.compile(r"\s+vs\.?\s+", re.IGNORECASE)
# Trailing qualifiers: " - NBA Finals (Game 6, Home Game 3)", " (Game 5, ...)"
_TRAILING_QUALIFIER = re.compile(r"\s*[-(].*$")


@dataclass(frozen=True)
class ParsedName:
    away_team: str | None
    home_team: str | None
    postseason_tier: str
    demand_rank: int
    is_game: bool
    is_tbd: bool
    game_number: int | None


def parse_name(name: str) -> ParsedName:
    """Recover opponent structure and postseason tier from an event name."""
    if not isinstance(name, str) or not name.strip():
        return ParsedName(None, None, "regular_season", 2, False, False, None)

    tier, rank = "regular_season", 2
    for pattern, label, demand in _COMPILED_POSTSEASON:
        if pattern.search(name):
            tier, rank = label, demand
            break

    is_game = not bool(_NON_GAME_PATTERNS.search(name))

    game_number = None
    game_match = re.search(r"\bGame (\d+)", name, re.IGNORECASE)
    if game_match:
        game_number = int(game_match.group(1))

    # Strip a leading "ALCS: " / "Spring Training: " style prefix before
    # splitting on " at " so the prefix never lands in the team name.
    body = name.split(": ", 1)[1] if ": " in name[:40] else name

    away = home = None
    if _AT_SPLIT.search(body):
        left, right = _AT_SPLIT.split(body, maxsplit=1)
        away, home = left.strip(), _TRAILING_QUALIFIER.sub("", right).strip()
    elif _VS_SPLIT.search(body):
        # Neutral-site games ("Broncos vs Jets") -- the url's team slug is the
        # designated home side, so keep the order as written.
        left, right = _VS_SPLIT.split(body, maxsplit=1)
        home, away = left.strip(), _TRAILING_QUALIFIER.sub("", right).strip()

    is_tbd = bool(away and away.upper() == "TBD")
    if is_tbd:
        away = None

    return ParsedName(
        away_team=away or None,
        home_team=home or None,
        postseason_tier=tier,
        demand_rank=rank,
        is_game=is_game,
        is_tbd=is_tbd,
        game_number=game_number,
    )
