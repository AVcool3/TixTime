"""Domain reference data: franchises, venue archetypes and seat tiers.

None of this comes from the SeatGeek export -- the export's `performerIds`,
`eventScore` and `popularityScore` columns are fully redacted, so there is no
measured popularity signal in the data at all. What follows is an explicit,
auditable *prior*: a hand-assigned demand index per franchise and a seating
model per venue archetype.

It is labelled as a prior everywhere it surfaces. It is used for two things:
seeding the price simulator, and giving the models a franchise-level feature.
It is not a measurement and the UI never presents it as one.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Venue archetypes
# ---------------------------------------------------------------------------

ARENA = "arena"
BALLPARK = "ballpark"
FOOTBALL_STADIUM = "football_stadium"
GENERAL_ADMISSION = "general_admission"

LEAGUE_ARCHETYPE = {
    "nba": ARENA,
    "nhl": ARENA,
    "mlb": BALLPARK,
    "nfl": FOOTBALL_STADIUM,
}

# Typical sellable capacity per archetype. Drives inventory volume in the
# simulator, which in turn drives how fast prices decay.
ARCHETYPE_CAPACITY = {
    ARENA: 18_500,
    BALLPARK: 41_000,
    FOOTBALL_STADIUM: 68_000,
    GENERAL_ADMISSION: 2_000,
}


@dataclass(frozen=True)
class SeatTier:
    """One band of seats within a venue.

    price_multiplier   relative to the event's baseline (lower-bowl-ish) price
    inventory_share    fraction of listed inventory sitting in this tier
    late_decay         how hard this tier's price falls in the final fortnight.
                       Premium seats hold value because their buyers are less
                       price-elastic; nosebleeds are dumped. >1 means it falls
                       harder than the event average.
    scarcity           how quickly the tier sells out; high-scarcity tiers rise
                       into the event instead of falling.
    """

    key: str
    label: str
    price_multiplier: float
    inventory_share: float
    late_decay: float
    scarcity: float


SEAT_TIERS: dict[str, tuple[SeatTier, ...]] = {
    ARENA: (
        SeatTier("courtside", "Courtside / Glass", 6.40, 0.03, 0.55, 0.92),
        SeatTier("lower_bowl", "Lower Bowl", 2.05, 0.24, 0.85, 0.70),
        SeatTier("club", "Club / Suite Level", 2.70, 0.09, 0.75, 0.62),
        SeatTier("upper_bowl", "Upper Bowl", 0.78, 0.38, 1.25, 0.34),
        SeatTier("upper_corner", "Upper Corner / Behind Basket", 0.55, 0.26, 1.40, 0.22),
    ),
    BALLPARK: (
        SeatTier("dugout", "Dugout / Home Plate", 4.20, 0.04, 0.60, 0.88),
        SeatTier("infield_lower", "Infield Lower", 1.85, 0.22, 0.88, 0.66),
        SeatTier("club", "Club Level", 2.30, 0.10, 0.78, 0.58),
        SeatTier("outfield", "Outfield", 0.72, 0.34, 1.30, 0.30),
        SeatTier("upper_deck", "Upper Deck", 0.48, 0.30, 1.45, 0.18),
    ),
    FOOTBALL_STADIUM: (
        SeatTier("field_sideline", "Field Level Sideline", 3.60, 0.06, 0.62, 0.90),
        SeatTier("field_endzone", "Field Level End Zone", 1.70, 0.14, 0.90, 0.68),
        SeatTier("club", "Club / Mezzanine", 2.45, 0.12, 0.74, 0.64),
        SeatTier("upper_sideline", "Upper Sideline", 0.95, 0.34, 1.22, 0.36),
        SeatTier("upper_endzone", "Upper End Zone", 0.62, 0.34, 1.42, 0.20),
    ),
    GENERAL_ADMISSION: (
        SeatTier("ga", "General Admission", 1.00, 1.00, 1.10, 0.40),
    ),
}


def tiers_for(archetype: str, is_ga: bool) -> tuple[SeatTier, ...]:
    """Seat tiers for a venue archetype; GA events collapse to a single tier."""
    if is_ga:
        return SEAT_TIERS[GENERAL_ADMISSION]
    return SEAT_TIERS.get(archetype, SEAT_TIERS[GENERAL_ADMISSION])


# ---------------------------------------------------------------------------
# Franchise demand priors
# ---------------------------------------------------------------------------
# demand_index is on a 0.55 - 1.65 scale, centred near 1.0. It bundles market
# size, travelling-fanbase reputation and building size into one number. It is
# a prior, not a measurement -- see the module docstring.

# Venue slugs whose display name cannot be recovered by de-hyphenating, because
# SeatGeek's slugs drop punctuation ("at-t-stadium", "crypto-com-arena") or
# carry a disambiguating suffix ("delta-center-1").
VENUE_NAME_OVERRIDES = {
    "at-t-stadium": "AT&T Stadium",
    "crypto-com-arena": "Crypto.com Arena",
    "delta-center-1": "Delta Center",
    "m-t-bank-stadium": "M&T Bank Stadium",
    "t-mobile-park": "T-Mobile Park",
    "t-mobile-arena": "T-Mobile Arena",
    "t-mobile-center": "T-Mobile Center",
    "e-g-a-stadium": "EverBank Stadium",
    "xfinity-mobile-arena": "Xfinity Mobile Arena",
    "smoothie-king-center": "Smoothie King Center",
    "spectrum-center-charlotte": "Spectrum Center",
    "great-american-ball-park": "Great American Ball Park",
    "guaranteed-rate-field": "Rate Field",
    "centre-bell": "Centre Bell",
    "canada-life-centre": "Canada Life Centre",
    "sofi-stadium": "SoFi Stadium",
    "ubs-arena": "UBS Arena",
    "kfc-yum-center": "KFC Yum! Center",
    "paycom-center": "Paycom Center",
    "footprint-center": "Footprint Center",
    "mvp-arena": "MVP Arena",
    "pnc-arena": "PNC Arena",
    "pnc-park": "PNC Park",
    "td-garden": "TD Garden",
    "sap-center": "SAP Center",
    "bmo-field": "BMO Field",
    "bmo-stadium": "BMO Stadium",
    "att-center": "Frost Bank Center",
    "the-great-lawn-at-sportsman-s-park": "The Great Lawn at Sportsman's Park",
}


@dataclass(frozen=True)
class Franchise:
    slug: str
    name: str
    league: str
    demand_index: float


_FRANCHISE_ROWS: tuple[tuple[str, str, str, float], ...] = (
    # ---- NFL -------------------------------------------------------------
    ("arizona-cardinals", "Arizona Cardinals", "nfl", 0.86),
    ("atlanta-falcons", "Atlanta Falcons", "nfl", 0.92),
    ("baltimore-ravens", "Baltimore Ravens", "nfl", 1.16),
    ("buffalo-bills", "Buffalo Bills", "nfl", 1.22),
    ("carolina-panthers", "Carolina Panthers", "nfl", 0.88),
    ("chicago-bears", "Chicago Bears", "nfl", 1.20),
    ("cincinnati-bengals", "Cincinnati Bengals", "nfl", 1.02),
    ("cleveland-browns", "Cleveland Browns", "nfl", 0.98),
    ("dallas-cowboys", "Dallas Cowboys", "nfl", 1.58),
    ("denver-broncos", "Denver Broncos", "nfl", 1.14),
    ("detroit-lions", "Detroit Lions", "nfl", 1.18),
    ("green-bay-packers", "Green Bay Packers", "nfl", 1.40),
    ("houston-texans", "Houston Texans", "nfl", 1.04),
    ("indianapolis-colts", "Indianapolis Colts", "nfl", 0.94),
    ("jacksonville-jaguars", "Jacksonville Jaguars", "nfl", 0.78),
    ("kansas-city-chiefs", "Kansas City Chiefs", "nfl", 1.46),
    ("las-vegas-raiders", "Las Vegas Raiders", "nfl", 1.34),
    ("los-angeles-chargers", "Los Angeles Chargers", "nfl", 0.96),
    ("los-angeles-rams", "Los Angeles Rams", "nfl", 1.06),
    ("miami-dolphins", "Miami Dolphins", "nfl", 1.10),
    ("minnesota-vikings", "Minnesota Vikings", "nfl", 1.12),
    ("new-england-patriots", "New England Patriots", "nfl", 1.24),
    ("new-orleans-saints", "New Orleans Saints", "nfl", 1.08),
    ("new-york-giants", "New York Giants", "nfl", 1.20),
    ("new-york-jets", "New York Jets", "nfl", 1.02),
    ("philadelphia-eagles", "Philadelphia Eagles", "nfl", 1.36),
    ("pittsburgh-steelers", "Pittsburgh Steelers", "nfl", 1.32),
    ("san-francisco-49ers", "San Francisco 49ers", "nfl", 1.28),
    ("seattle-seahawks", "Seattle Seahawks", "nfl", 1.22),
    ("tampa-bay-buccaneers", "Tampa Bay Buccaneers", "nfl", 0.94),
    ("tennessee-titans", "Tennessee Titans", "nfl", 0.86),
    ("washington-commanders", "Washington Commanders", "nfl", 1.00),
    # ---- NBA -------------------------------------------------------------
    ("atlanta-hawks", "Atlanta Hawks", "nba", 0.82),
    ("boston-celtics", "Boston Celtics", "nba", 1.42),
    ("brooklyn-nets", "Brooklyn Nets", "nba", 0.96),
    ("charlotte-hornets", "Charlotte Hornets", "nba", 0.72),
    ("chicago-bulls", "Chicago Bulls", "nba", 1.20),
    ("cleveland-cavaliers", "Cleveland Cavaliers", "nba", 0.98),
    ("dallas-mavericks", "Dallas Mavericks", "nba", 1.10),
    ("denver-nuggets", "Denver Nuggets", "nba", 1.08),
    ("detroit-pistons", "Detroit Pistons", "nba", 0.86),
    ("golden-state-warriors", "Golden State Warriors", "nba", 1.52),
    ("houston-rockets", "Houston Rockets", "nba", 0.94),
    ("indiana-pacers", "Indiana Pacers", "nba", 0.88),
    ("los-angeles-clippers", "Los Angeles Clippers", "nba", 1.00),
    ("los-angeles-lakers", "Los Angeles Lakers", "nba", 1.60),
    ("memphis-grizzlies", "Memphis Grizzlies", "nba", 0.78),
    ("miami-heat", "Miami Heat", "nba", 1.24),
    ("milwaukee-bucks", "Milwaukee Bucks", "nba", 1.02),
    ("minnesota-timberwolves", "Minnesota Timberwolves", "nba", 0.90),
    ("new-orleans-pelicans", "New Orleans Pelicans", "nba", 0.76),
    ("new-york-knicks", "New York Knicks", "nba", 1.48),
    ("oklahoma-city-thunder", "Oklahoma City Thunder", "nba", 1.06),
    ("orlando-magic", "Orlando Magic", "nba", 0.84),
    ("philadelphia-76ers", "Philadelphia 76ers", "nba", 1.14),
    ("phoenix-suns", "Phoenix Suns", "nba", 1.04),
    ("portland-trail-blazers", "Portland Trail Blazers", "nba", 0.92),
    ("sacramento-kings", "Sacramento Kings", "nba", 0.88),
    ("san-antonio-spurs", "San Antonio Spurs", "nba", 1.12),
    ("toronto-raptors", "Toronto Raptors", "nba", 1.08),
    ("utah-jazz", "Utah Jazz", "nba", 0.90),
    ("washington-wizards", "Washington Wizards", "nba", 0.74),
    # ---- NHL -------------------------------------------------------------
    ("anaheim-ducks", "Anaheim Ducks", "nhl", 0.72),
    ("boston-bruins", "Boston Bruins", "nhl", 1.28),
    ("buffalo-sabres", "Buffalo Sabres", "nhl", 0.88),
    ("calgary-flames", "Calgary Flames", "nhl", 1.04),
    ("carolina-hurricanes", "Carolina Hurricanes", "nhl", 0.94),
    ("chicago-blackhawks", "Chicago Blackhawks", "nhl", 1.14),
    ("colorado-avalanche", "Colorado Avalanche", "nhl", 1.10),
    ("columbus-blue-jackets", "Columbus Blue Jackets", "nhl", 0.78),
    ("dallas-stars", "Dallas Stars", "nhl", 0.96),
    ("detroit-red-wings", "Detroit Red Wings", "nhl", 1.12),
    ("edmonton-oilers", "Edmonton Oilers", "nhl", 1.26),
    ("florida-panthers", "Florida Panthers", "nhl", 0.98),
    ("los-angeles-kings", "Los Angeles Kings", "nhl", 0.92),
    ("minnesota-wild", "Minnesota Wild", "nhl", 1.06),
    ("montreal-canadiens", "Montreal Canadiens", "nhl", 1.34),
    ("nashville-predators", "Nashville Predators", "nhl", 1.08),
    ("new-jersey-devils", "New Jersey Devils", "nhl", 0.90),
    ("new-york-islanders", "New York Islanders", "nhl", 0.86),
    ("new-york-rangers", "New York Rangers", "nhl", 1.30),
    ("ottawa-senators", "Ottawa Senators", "nhl", 0.84),
    ("philadelphia-flyers", "Philadelphia Flyers", "nhl", 1.00),
    ("pittsburgh-penguins", "Pittsburgh Penguins", "nhl", 1.16),
    ("san-jose-sharks", "San Jose Sharks", "nhl", 0.76),
    ("seattle-kraken", "Seattle Kraken", "nhl", 0.94),
    ("st-louis-blues", "St. Louis Blues", "nhl", 0.98),
    ("tampa-bay-lightning", "Tampa Bay Lightning", "nhl", 1.02),
    ("toronto-maple-leafs", "Toronto Maple Leafs", "nhl", 1.44),
    ("utah-mammoth", "Utah Mammoth", "nhl", 0.80),
    ("vancouver-canucks", "Vancouver Canucks", "nhl", 1.10),
    ("vegas-golden-knights", "Vegas Golden Knights", "nhl", 1.22),
    ("washington-capitals", "Washington Capitals", "nhl", 1.06),
    ("winnipeg-jets", "Winnipeg Jets", "nhl", 0.92),
    # ---- MLB -------------------------------------------------------------
    ("arizona-diamondbacks", "Arizona Diamondbacks", "mlb", 0.82),
    ("athletics", "Athletics", "mlb", 0.62),
    ("atlanta-braves", "Atlanta Braves", "mlb", 1.18),
    ("baltimore-orioles", "Baltimore Orioles", "mlb", 0.90),
    ("boston-red-sox", "Boston Red Sox", "mlb", 1.40),
    ("chicago-cubs", "Chicago Cubs", "mlb", 1.36),
    ("chicago-white-sox", "Chicago White Sox", "mlb", 0.72),
    ("cincinnati-reds", "Cincinnati Reds", "mlb", 0.86),
    ("cleveland-guardians", "Cleveland Guardians", "mlb", 0.88),
    ("colorado-rockies", "Colorado Rockies", "mlb", 0.84),
    ("detroit-tigers", "Detroit Tigers", "mlb", 0.96),
    ("houston-astros", "Houston Astros", "mlb", 1.14),
    ("kansas-city-royals", "Kansas City Royals", "mlb", 0.80),
    ("los-angeles-angels", "Los Angeles Angels", "mlb", 0.86),
    ("los-angeles-dodgers", "Los Angeles Dodgers", "mlb", 1.50),
    ("miami-marlins", "Miami Marlins", "mlb", 0.64),
    ("milwaukee-brewers", "Milwaukee Brewers", "mlb", 0.94),
    ("minnesota-twins", "Minnesota Twins", "mlb", 0.86),
    ("new-york-mets", "New York Mets", "mlb", 1.20),
    ("new-york-yankees", "New York Yankees", "mlb", 1.52),
    ("philadelphia-phillies", "Philadelphia Phillies", "mlb", 1.26),
    ("pittsburgh-pirates", "Pittsburgh Pirates", "mlb", 0.74),
    ("san-diego-padres", "San Diego Padres", "mlb", 1.12),
    ("san-francisco-giants", "San Francisco Giants", "mlb", 1.16),
    ("seattle-mariners", "Seattle Mariners", "mlb", 1.04),
    ("st-louis-cardinals", "St. Louis Cardinals", "mlb", 1.22),
    ("tampa-bay-rays", "Tampa Bay Rays", "mlb", 0.66),
    ("texas-rangers", "Texas Rangers", "mlb", 1.00),
    ("toronto-blue-jays", "Toronto Blue Jays", "mlb", 1.24),
    ("washington-nationals", "Washington Nationals", "mlb", 0.88),
)

FRANCHISES: dict[str, Franchise] = {
    slug: Franchise(slug, name, league, idx) for slug, name, league, idx in _FRANCHISE_ROWS
}

# Name -> slug, so a home/away team parsed out of the event *name* can be
# matched back to a franchise.
FRANCHISE_BY_NAME: dict[str, Franchise] = {f.name.lower(): f for f in FRANCHISES.values()}

DEFAULT_DEMAND_INDEX = 0.85


def franchise_for_slug(slug: str | None) -> Franchise | None:
    """Exact match, then longest-prefix match.

    Team slugs in the export carry suffixes for non-game inventory
    ('dallas-cowboys-tailgates', 'baltimore-ravens-training-camp'). Those still
    belong to the parent franchise, so fall back to a prefix match.
    """
    # NaN arrives here from pandas columns, and NaN is truthy.
    if not isinstance(slug, str) or not slug:
        return None
    direct = FRANCHISES.get(slug)
    if direct is not None:
        return direct
    best: Franchise | None = None
    for candidate_slug, franchise in FRANCHISES.items():
        if slug.startswith(f"{candidate_slug}-"):
            if best is None or len(candidate_slug) > len(best.slug):
                best = franchise
    return best


def franchise_for_name(name: str | None) -> Franchise | None:
    if not isinstance(name, str) or not name:
        return None
    return FRANCHISE_BY_NAME.get(name.strip().lower())


def demand_index_for(slug: str | None, name: str | None = None) -> float:
    franchise = franchise_for_slug(slug) or franchise_for_name(name)
    return franchise.demand_index if franchise else DEFAULT_DEMAND_INDEX


# ---------------------------------------------------------------------------
# League-level price anchors
# ---------------------------------------------------------------------------
# Baseline get-in price for a median matchup in the reference seat tier. These
# anchors set the overall scale of simulated prices; they are chosen to sit in
# the range widely reported for each league's secondary market.

LEAGUE_BASE_PRICE = {
    "nfl": 155.0,
    "nba": 95.0,
    "nhl": 88.0,
    "mlb": 46.0,
}
DEFAULT_BASE_PRICE = 60.0

# Multiplier applied on top of the base price for postseason tiers. Keyed by
# the tier labels produced by catalog.parse.parse_name.
POSTSEASON_MULTIPLIER = {
    "super_bowl": 9.00,
    "world_series": 4.20,
    "stanley_cup_final": 3.80,
    "nba_finals": 4.00,
    "conference_final": 2.60,
    "league_championship": 2.60,
    "divisional": 2.10,
    "playoff_round": 1.85,
    "wild_card": 1.70,
    "play_in": 1.35,
    "in_season_tournament": 1.15,
    "opening_day": 1.45,
    "regular_season": 1.00,
    "exhibition": 0.55,
    "preseason": 0.45,
    "spring_training": 0.40,
}
