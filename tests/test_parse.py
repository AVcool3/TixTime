"""Parser tests, anchored on real rows from the supplied SeatGeek export."""

from __future__ import annotations

import pytest

from tixtime.catalog.parse import (
    parse_name,
    parse_url,
    parse_url_geo_fallback,
    titleize_slug,
)


class TestParseUrl:
    def test_game_url_yields_full_geography(self):
        parsed = parse_url(
            "https://seatgeek.com/golden-state-warriors-tickets/"
            "6-16-2026-san-francisco-california-chase-center/nba/18067840"
        )
        assert parsed.home_team_slug == "golden-state-warriors"
        assert parsed.league == "nba"
        assert parsed.city == "san-francisco"
        assert parsed.region == "california"
        assert parsed.venue_slug == "chase-center"
        assert parsed.has_geo

    def test_multiword_city_and_region_split_correctly(self):
        parsed = parse_url(
            "https://seatgeek.com/new-york-knicks-tickets/"
            "1-5-2026-new-york-new-york-madison-square-garden/nba/1"
        )
        assert parsed.city == "new-york"
        assert parsed.region == "new-york"
        assert parsed.venue_slug == "madison-square-garden"

    def test_canadian_venue_uses_country_not_province(self):
        parsed = parse_url(
            "https://seatgeek.com/toronto-maple-leafs-tickets/"
            "5-31-2026-toronto-canada-scotiabank-arena/nhl/2"
        )
        assert parsed.city == "toronto"
        assert parsed.region == "canada"
        assert parsed.venue_slug == "scotiabank-arena"

    def test_non_game_url_shape_has_no_geography(self):
        # stadium_tours use <slug>/<taxonomy>/<date>/<id> -- no city encoded.
        parsed = parse_url(
            "https://seatgeek.com/at-t-stadium-owner-s-experience-tour-tickets/"
            "stadium-tours/2025-12-02-10-am/17841902"
        )
        assert not parsed.has_geo
        assert parsed.league is None

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "not-a-url",
            "https://seatgeek.com/too/few/segments",
            "https://seatgeek.com/a/b/c/d/e",
        ],
    )
    def test_malformed_urls_return_empty_rather_than_raising(self, url):
        parsed = parse_url(url)
        assert not parsed.has_geo

    def test_geo_fallback_recovers_city_from_learned_vocabulary(self):
        # This row has no region token between city and venue.
        url = (
            "https://seatgeek.com/arizona-cardinals-tickets/"
            "4-23-2026-glendale-the-great-lawn-at-sportsman-s-park/nfl/18108006"
        )
        assert parse_url(url).city is None
        recovered = parse_url_geo_fallback(url, frozenset({"glendale", "phoenix"}))
        assert recovered.city == "glendale"
        assert recovered.venue_slug == "the-great-lawn-at-sportsman-s-park"
        assert recovered.region is None


class TestTitleize:
    @pytest.mark.parametrize(
        "slug,expected",
        [
            ("chase-center", "Chase Center"),
            ("madison-square-garden", "Madison Square Garden"),
            ("ubs-arena", "UBS Arena"),
            ("td-garden", "TD Garden"),
            (None, None),
            (float("nan"), None),  # pandas NaN is truthy -- must not crash
        ],
    )
    def test_titleize(self, slug, expected):
        assert titleize_slug(slug) == expected


class TestParseName:
    def test_simple_matchup(self):
        parsed = parse_name("Chicago Bears at Baltimore Ravens")
        assert parsed.home_team == "Baltimore Ravens"
        assert parsed.away_team == "Chicago Bears"
        assert parsed.postseason_tier == "regular_season"
        assert parsed.is_game and not parsed.is_tbd

    def test_prefix_is_stripped_from_team_name(self):
        parsed = parse_name("Spring Training: San Diego Padres at Cincinnati Reds")
        assert parsed.away_team == "San Diego Padres"
        assert parsed.home_team == "Cincinnati Reds"
        assert parsed.postseason_tier == "spring_training"
        assert parsed.demand_rank == 0

    def test_trailing_qualifier_is_stripped(self):
        parsed = parse_name(
            "San Antonio Spurs at New York Knicks - NBA Finals (Game 6, Home Game 3)"
        )
        assert parsed.home_team == "New York Knicks"
        assert parsed.postseason_tier == "nba_finals"
        assert parsed.game_number == 6

    def test_tbd_opponent_is_flagged_not_stored_as_a_team(self):
        parsed = parse_name("TBD at Golden State Warriors - NBA Finals (Game 6, Home Game 3)")
        assert parsed.is_tbd
        assert parsed.away_team is None
        assert parsed.home_team == "Golden State Warriors"

    def test_neutral_site_vs_keeps_designated_home_first(self):
        parsed = parse_name("NFL International Series: Denver Broncos vs New York Jets")
        assert parsed.home_team == "Denver Broncos"
        assert parsed.away_team == "New York Jets"

    @pytest.mark.parametrize(
        "name",
        [
            "Arizona Cardinals Tailgreeter Tailgate",
            "SUITE: Dallas Cowboys vs New York Giants",
            "2026 Cardinals Draft Party Presented by State Farm",
            "AT&T Stadium Owner's Experience Tour",
        ],
    )
    def test_non_game_inventory_is_excluded_from_games(self, name):
        assert not parse_name(name).is_game

    def test_more_specific_postseason_tier_wins(self):
        # "Stanley Cup Final" must not fall through to the generic
        # "stanley cup" -> playoff_round rule.
        assert parse_name("TBD at Dallas Stars - Stanley Cup Final").postseason_tier == (
            "stanley_cup_final"
        )
        assert parse_name("Stanley Cup Playoffs Round 2").postseason_tier == "playoff_round"

    def test_demand_rank_orders_postseason_above_regular_above_exhibition(self):
        finals = parse_name("TBD at Boston Celtics - NBA Finals")
        regular = parse_name("Miami Heat at Boston Celtics")
        spring = parse_name("Spring Training: Mets at Cardinals")
        assert finals.demand_rank > regular.demand_rank > spring.demand_rank

    @pytest.mark.parametrize("name", ["", None, 123.0])
    def test_degenerate_names_do_not_raise(self, name):
        parsed = parse_name(name)
        assert parsed.home_team is None
