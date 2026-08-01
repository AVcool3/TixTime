"""Synthetic secondary-market price history.

WHY THIS EXISTS
---------------
The supplied SeatGeek export has every price column redacted (0 of 7,118 rows
carry lowestPrice/medianPrice/listingCount) and holds exactly one row per
event, so it contains no price time series at all. The environment TixTime was
built in cannot reach any ticket API to fetch one. Without a price history
there is nothing to learn a buy-timing model from.

So this module generates one. Every row it writes is tagged
`source='synthetic_v1'`, the API returns that tag, and the UI displays it. No
part of the system presents these numbers as observed market prices.

WHAT IS AND IS NOT LEGITIMATE
-----------------------------
A model trained on this data learns the generative process encoded below. That
makes the *machinery* verifiable end to end -- feature engineering, leakage
control, the backtest harness, the serving path -- and the reported accuracy
is a genuine measure of whether that machinery works. It is NOT evidence that
the model would predict real ticket prices well, and the accuracy page says so
in those words.

THE PRICE PROCESS
-----------------
Resale prices do not move monotonically toward the event. The shape widely
described in secondary-market reporting, and the one modelled here, is:

  1. An early plateau just after listings open, anchored near the primary
     on-sale price, when few sellers are competing.
  2. A decline as speculative and season-ticket-holder inventory piles up,
     bottoming out somewhere in the middle of the window.
  3. A terminal phase whose direction depends on demand:
       - hot events tighten and prices climb steeply into event day;
       - soft events see sellers dump inventory they can no longer hold, and
         prices collapse in the final week.

The event's latent `demand_score` decides which terminal branch dominates, so
the optimal purchase date genuinely varies across the catalogue -- which is the
entire point. If every event bottomed on the same day the prediction task
would be trivial and the backtest meaningless.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
import pandas as pd

from tixtime.catalog.reference import (
    DEFAULT_BASE_PRICE,
    LEAGUE_BASE_PRICE,
    POSTSEASON_MULTIPLIER,
    SeatTier,
    tiers_for_event,
)
from tixtime.config import SIMULATION, SYNTHETIC_SOURCE, WARMUP_DAYS
from tixtime.pricing.regimes import DEFAULT_REGIME, Regime


def _event_rng(event_id: int, seed: int) -> np.random.Generator:
    """Deterministic per-event generator.

    Hashing the pair means an event's price path is identical across runs,
    machines and partial rebuilds, and independent of how many events were
    simulated before it.
    """
    digest = hashlib.blake2b(f"{seed}:{event_id}".encode(), digest_size=8).digest()
    return np.random.default_rng(int.from_bytes(digest, "big"))


# ---------------------------------------------------------------------------
# Latent demand
# ---------------------------------------------------------------------------

# Weekend games draw better; a Tuesday in February does not.
_DOW_DEMAND = {0: -0.06, 1: -0.07, 2: -0.05, 3: -0.02, 4: 0.06, 5: 0.09, 6: 0.03}


def _demand_score(event: pd.Series, rng: np.random.Generator) -> float:
    """Latent 0-1 demand for an event. Drives the terminal price branch."""
    home = float(event["home_demand_index"])
    away = float(event["away_demand_index"])
    # The home fanbase fills the building; a marquee visitor adds a premium but
    # matters less than who is hosting.
    base = 0.70 * home + 0.30 * away

    tier_boost = {
        0: -0.30, 1: -0.22, 2: 0.0, 3: 0.12, 4: 0.26, 5: 0.38, 6: 0.52,
    }.get(int(event["demand_rank"]), 0.0)

    event_date = pd.Timestamp(event["event_date"])
    dow = _DOW_DEMAND.get(event_date.dayofweek, 0.0)

    # Team quality varies year to year in ways the catalogue cannot see; this
    # is the residual, seeded so it stays fixed for a given event.
    residual = rng.normal(0.0, 0.11)

    raw = (base - 1.0) + tier_boost + dow + residual
    # Squash to (0, 1). The 1.9 gain keeps typical events off the asymptotes so
    # the terminal branch is genuinely mixed across the catalogue.
    return float(1.0 / (1.0 + np.exp(-1.9 * raw)))


def _base_price(event: pd.Series, demand: float) -> float:
    """Reference-tier price level for the event, before any time dynamics."""
    league = event["league"] if isinstance(event["league"], str) else None
    anchor = LEAGUE_BASE_PRICE.get(league, DEFAULT_BASE_PRICE)
    postseason = POSTSEASON_MULTIPLIER.get(event["postseason_tier"], 1.0)
    # Demand moves the level by roughly +/-45% around the league anchor.
    demand_mult = 0.62 + 0.85 * demand
    return float(anchor * postseason * demand_mult)


# ---------------------------------------------------------------------------
# The time path
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PathParams:
    """Shape of one event's log-price path.

    The pre-trough phase is a single *monotone* decline rather than an early
    premium plus a Gaussian basin. That earlier formulation left a long flat
    plateau between the premium decaying away and the basin taking hold, so
    the argmin was chosen by noise anywhere along it -- optimal buy days
    scattered out past 100 days and the 1-3 week bucket sat empty. A monotone
    decline puts the minimum where the parameters say it should be.
    """

    open_premium: float    # log-points above the trough level at listing open
    decline_shape: float   # >1 front-loads the fall, <1 back-loads it
    trough_center: float   # days-until-event the decline bottoms out at
    terminal_gain: float   # >0 hot: climbs into the event; <0 soft: dumps
    tau_terminal: float


def _path_params(
    demand: float, rng: np.random.Generator, horizon: int, regime: Regime = DEFAULT_REGIME
) -> PathParams:
    # Hot events keep a thin early premium and then run up; soft events carry a
    # fat early premium (optimistic sellers) and then capitulate.
    # Total fall from listing open to the trough, in log points. 0.29 log is a
    # ~25% fall, which is the order of magnitude resale markets actually move
    # over a listing window. Hot events give up less of their opening level.
    # A soft event gives up this premium AND then dumps, so the two terms
    # compound; sizing this term as if it were the whole fall produced 60%+
    # open-to-event declines.
    open_premium = float(np.clip(
        regime.open_premium_base + regime.open_premium_demand * demand + rng.normal(0, 0.04),
        0.06, 0.60))
    # Curvature of the decline. Must stay BELOW 1: with an exponent above 1 the
    # curve approaches the trough with near-zero slope, recreating the flat
    # plateau this shape was meant to remove -- on a 240-day horizon the curve
    # rose only ~0.008 log points between d=40 and d=60, so noise chose the
    # argmin and the median optimal day drifted out past 85. Below 1 the
    # approach is steep and the trough is sharply defined.
    decline_shape = float(np.clip(
        rng.normal(regime.decline_shape_mean, regime.decline_shape_sd), 0.45, 2.2))

    # Where the decline bottoms out. Hot events bottom late in the window and
    # then run up; soft events bottom early here, but their terminal dump
    # usually drags the true minimum to event day anyway. The wide spread is
    # what populates the 1-3 week bucket.
    trough_center = float(np.clip(
        regime.trough_base + regime.trough_demand * demand + rng.normal(0, regime.trough_sd),
        2, 0.7 * horizon))

    # The terminal branch is what makes buy-timing non-trivial, so it has to be
    # decisive: hot events run up hard into the event, soft events capitulate.
    # Centred at demand 0.52 so the catalogue splits roughly evenly.
    # Centred at 0.62, not 0.50. Sweeting (2012, JPE) finds secondary MLB
    # prices DECLINE as game day approaches, accelerating in the final days,
    # because a ticket is worth zero at first pitch and sellers know it. A
    # late run-up is a real but MINORITY regime -- it needs genuinely high
    # demand and supply exhaustion. Centring on 0.50 made half the catalogue
    # run up, which inverts the documented default.
    terminal_gain = float((demand - regime.terminal_centre) * regime.terminal_scale + rng.normal(0, 0.08))
    # How early the terminal move starts. A large tau here means the run-up is
    # already underway two months out, which pulled hot events' optimal buy day
    # back to ~88 days; the run-up should bite in the final few weeks.
    tau_terminal = (
        float(rng.uniform(regime.tau_hot_low, regime.tau_hot_high))
        if terminal_gain >= 0
        else float(rng.uniform(regime.tau_soft_low, regime.tau_soft_high))
    )

    return PathParams(
        open_premium=open_premium,
        decline_shape=decline_shape,
        trough_center=trough_center,
        terminal_gain=terminal_gain,
        tau_terminal=tau_terminal,
    )


def _log_shape(days: np.ndarray, horizon: int, params: PathParams, tier: SeatTier) -> np.ndarray:
    """Log-price offset for each days-until-event value.

    `days` counts DOWN to the event: days[0] == horizon, days[-1] == 0.
    """
    # 1. Monotone decline from the opening premium down to the trough. Zero at
    #    and below trough_center, so the pre-trough phase never flattens out
    #    into a plateau where noise would choose the minimum.
    span = max(horizon - params.trough_center, 1.0)
    progress = np.clip((days - params.trough_center) / span, 0.0, 1.0)
    decline = params.open_premium * progress**params.decline_shape

    # 2. terminal phase. A tier's own scarcity and decay reshape it: premium
    #    seats hold or climb, nosebleeds get dumped hardest.
    closeness = np.exp(-days / params.tau_terminal)

    # The terminal regime is resolved PER TIER, not once per event. Scarce
    # premium seats can firm up into an event whose upper deck is being dumped,
    # which is what real soft-event order books look like. Deciding the sign
    # once per event made every tier bottom on the same day whenever the event
    # declined into game day, collapsing the per-seat recommendation to noise.
    gain = params.terminal_gain + 0.22 * (tier.scarcity - 0.40)
    tier_gain = np.where(
        gain >= 0,
        gain * (0.70 + 0.60 * tier.scarcity),  # scarce tiers run up harder
        gain * tier.late_decay,                # cheap tiers fall harder
    )
    terminal = tier_gain * closeness

    return decline + terminal


def _ar1_noise(n: int, rng: np.random.Generator, sigma: float = 0.008, phi: float = 0.86) -> np.ndarray:
    """Correlated day-to-day noise.

    Independent noise would let a model denoise by averaging and would make the
    running minimum unrealistically jagged. Real listing prices wander.

    Kept small on purpose. Over a 200-day path the running minimum of the noise
    is itself a meaningful negative number, so noise that rivals the
    deterministic shape stops the trough from landing where the parameters say
    and inflates the apparent open-to-trough fall.
    """
    innovations = rng.normal(0.0, sigma, size=n)
    out = np.empty(n)
    out[0] = innovations[0] / np.sqrt(1 - phi**2)
    for i in range(1, n):
        out[i] = phi * out[i - 1] + innovations[i]
    return out


def _shocks(
    days: np.ndarray, rng: np.random.Generator, rate: float = 0.4, magnitude_sd: float = 0.03
) -> np.ndarray:
    """Occasional persistent level shifts: an injury, a hot streak, weather.

    Kept small relative to the deterministic shape. Oversized shocks bias the
    argmin toward the start of the window, because a positive shift raises
    every day after it and leaves the pre-shock days looking cheapest.
    """
    out = np.zeros(len(days))
    for _ in range(rng.poisson(rate)):
        at = rng.integers(0, len(days))
        magnitude = rng.normal(0.0, magnitude_sd)
        # A shock shifts the level from that day onward rather than for one day.
        out[at:] += magnitude
    return out


def simulate_event(
    event: pd.Series, seed: int = SIMULATION.seed, regime: Regime = DEFAULT_REGIME
) -> pd.DataFrame:
    """Generate the full price history for one event, across every seat tier."""
    horizon = int(event["horizon_days"])
    rng = _event_rng(int(event["event_id"]), seed)

    demand = _demand_score(event, rng)
    base = _base_price(event, demand)
    params = _path_params(demand, rng, horizon, regime)

    # Simulate a burn-in period BEFORE the exposed window so that 7/14/30-day
    # rolling features are fully defined on the very first exposed day. Without
    # it the earliest as-of rows carry partial windows -- exactly the
    # long-horizon regime the product exists to serve -- and the model either
    # never trains on them or learns a spurious cliff where the windows fill.
    # Burn-in rows are flagged and never reach the API or the chart.
    first_day = horizon + WARMUP_DAYS
    days = np.arange(first_day, -1, -1)
    is_burn_in = days > horizon
    as_of = pd.Timestamp(event["event_date"]) - pd.to_timedelta(days, unit="D")

    # Single tier unless the catalogue says seats are actually selectable.
    single_tier = not bool(event.get("has_seat_tiers", not event["is_ga"]))
    tiers = tiers_for_event(event.get("league"), event.get("archetype"), single_tier)

    # Shared across tiers: the whole event moves together, so a shock hits
    # every section at once. Tier-specific noise is added on top.
    common_noise = (_ar1_noise(len(days), rng, regime.ar1_sigma, regime.ar1_phi)
                    + _shocks(days, rng, regime.shock_rate, regime.shock_sd))

    frames = []
    for tier in tiers:
        # The shape is defined on the exposed window; burn-in days sit at or
        # before listing open, so they clamp to the opening level.
        shape = _log_shape(np.minimum(days, horizon), horizon, params, tier)
        tier_noise = _ar1_noise(len(days), rng, sigma=regime.tier_sigma, phi=regime.ar1_phi)
        log_price = np.log(base * tier.price_multiplier) + shape + common_noise + tier_noise
        median_price = np.exp(log_price)

        # The get-in (cheapest) listing sits below the tier median, and the gap
        # widens when inventory is deep -- more listings means more competition
        # at the bottom of the stack.
        #
        # The inventory term is deliberately small. At 0.22 the discount swung
        # 22 percentage points across the window and peaked where inventory
        # peaked (~d=95), which manufactured a false trough there: the get-in
        # argmin sat 35 days away from the actual price trough while the median
        # tracked it within a day. The gap must modulate the curve, not define
        # it.
        inventory = _inventory(np.minimum(days, horizon), horizon, demand, tier, rng)
        depth = inventory / max(inventory.max(), 1.0)
        get_in = median_price * (
            1.0 - (regime.get_in_base_discount + regime.get_in_depth_discount * depth))

        frames.append(
            pd.DataFrame(
                {
                    "event_id": int(event["event_id"]),
                    "tier_key": tier.key,
                    "as_of_date": as_of.date,
                    "days_until_event": days,
                    "get_in_price": np.round(get_in, 2),
                    "median_price": np.round(median_price, 2),
                    "listing_count": inventory.astype(int),
                    "ticket_count": (inventory * rng.uniform(1.6, 2.4)).astype(int),
                    "is_burn_in": is_burn_in,
                    "source": regime.key,
                }
            )
        )

    return pd.concat(frames, ignore_index=True)


def _inventory(
    days: np.ndarray, horizon: int, demand: float, tier: SeatTier, rng: np.random.Generator
) -> np.ndarray:
    """Listing count over time: builds after on-sale, peaks mid, then sells off."""
    capacity = 1400 * tier.inventory_share * (0.7 + 0.6 * (1.0 - demand))
    elapsed = horizon - days
    build = 1.0 - np.exp(-elapsed / max(horizon * 0.18, 6.0))
    # Sell-through accelerates near the event, and faster for scarce tiers.
    sell = np.exp(-((1.0 - days / max(horizon, 1)) ** 3) * (2.2 + 3.4 * tier.scarcity))
    noise = rng.normal(1.0, 0.05, size=len(days))
    return np.clip(capacity * build * sell * noise, 1, None)
