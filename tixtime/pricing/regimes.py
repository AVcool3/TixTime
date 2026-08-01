"""Alternative market regimes, for testing whether the model generalises.

WHY THIS EXISTS
---------------
The sharpest objection to any result measured on simulated data: the model was
trained on data produced by rules the same author wrote, so a good score may
just mean it successfully inverted its own generator. Reporting accuracy
without answering that is close to circular.

The answer is a REGIME HOLDOUT. This module defines markets whose structural
rules differ from the one the model trained on -- not merely a different random
seed, which would change nothing that matters, but different curve shape,
different timing of the terminal move, different noise, and in one case a
reversed relationship between demand and when prices bottom.

A model that memorised `synthetic_v1`'s parameters collapses on these. A model
that learned "read the recent trajectory and the inventory, then judge whether
the cheapest price is still ahead" should degrade but survive. Which of those
happens is an empirical question, and `tixtime.ml.regime_test` answers it.

The regimes are deliberately not tuned to make the model look good.
`v2_sharp` changes the curve's shape and triples the noise; `v3_inverted`
reverses the demand-to-timing relationship outright. A model that leans hard on
that relationship is expected to do badly on the latter. That is the point.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Regime:
    """Structural knobs of a market. `v1` reproduces the trained-on market."""

    key: str
    description: str

    # Pre-trough decline
    open_premium_base: float
    open_premium_demand: float     # coefficient on demand
    decline_shape_mean: float
    decline_shape_sd: float

    # Where the trough sits, as a function of demand
    trough_base: float
    trough_demand: float           # negative reverses the demand relationship
    trough_sd: float

    # Terminal move
    terminal_scale: float
    terminal_centre: float         # demand level at which the branch flips
    tau_hot_low: float
    tau_hot_high: float
    tau_soft_low: float
    tau_soft_high: float

    # Noise
    ar1_sigma: float
    ar1_phi: float
    tier_sigma: float
    shock_rate: float
    shock_sd: float

    # Microstructure
    get_in_base_discount: float
    get_in_depth_discount: float


V1 = Regime(
    key="synthetic_v1",
    description="The market the model was trained on.",
    open_premium_base=0.30, open_premium_demand=-0.12,
    decline_shape_mean=0.95, decline_shape_sd=0.16,
    trough_base=12.0, trough_demand=38.0, trough_sd=12.0,
    terminal_scale=1.45, terminal_centre=0.58,
    tau_hot_low=7.0, tau_hot_high=13.0, tau_soft_low=3.5, tau_soft_high=10.0,
    ar1_sigma=0.008, ar1_phi=0.86, tier_sigma=0.005,
    shock_rate=0.4, shock_sd=0.03,
    get_in_base_discount=0.11, get_in_depth_discount=0.06,
)

# Same qualitative story, materially different quantities: a steeper, more
# front-loaded decline, troughs much closer to the event, a sharper terminal
# move that starts later, and roughly triple the noise. This is the "same kind
# of market, different city" test.
V2_SHARP = Regime(
    key="synthetic_v2_sharp",
    description="Steeper decline, later and sharper terminal move, ~3x the noise.",
    open_premium_base=0.44, open_premium_demand=-0.20,
    decline_shape_mean=1.55, decline_shape_sd=0.22,
    trough_base=5.0, trough_demand=18.0, trough_sd=7.0,
    terminal_scale=2.30, terminal_centre=0.46,
    tau_hot_low=3.5, tau_hot_high=7.0, tau_soft_low=2.0, tau_soft_high=5.0,
    ar1_sigma=0.024, ar1_phi=0.72, tier_sigma=0.014,
    shock_rate=1.1, shock_sd=0.09,
    get_in_base_discount=0.05, get_in_depth_discount=0.17,
)

# The adversarial one. Demand's relationship to WHEN prices bottom is reversed
# on BOTH mechanisms that control it, which is what it takes: moving only the
# trough position left the correlation positive (0.43 vs v1's 0.63), because
# the terminal run-up still pushed hot events' true minimum earlier and
# dominated. `terminal_scale` is therefore NEGATIVE here -- hot events dump and
# soft events run up, the opposite of what the model was taught. Anything
# relying on the learned demand-to-timing mapping should be actively harmed,
# not merely degraded.
V3_INVERTED = Regime(
    key="synthetic_v3_inverted",
    description="Demand-to-timing relationship reversed; hot events bottom late.",
    open_premium_base=0.26, open_premium_demand=+0.10,
    decline_shape_mean=0.80, decline_shape_sd=0.18,
    trough_base=52.0, trough_demand=-34.0, trough_sd=11.0,
    terminal_scale=-1.25, terminal_centre=0.44,
    tau_hot_low=9.0, tau_hot_high=18.0, tau_soft_low=4.0, tau_soft_high=12.0,
    ar1_sigma=0.014, ar1_phi=0.80, tier_sigma=0.009,
    shock_rate=0.7, shock_sd=0.06,
    get_in_base_discount=0.14, get_in_depth_discount=0.03,
)

REGIMES: dict[str, Regime] = {r.key: r for r in (V1, V2_SHARP, V3_INVERTED)}
DEFAULT_REGIME = V1
