"""Layer 1 / Layer 2 alert rules (Section 5).

Pure functions over the observation series + optional external history, so they
are fully unit-testable offline.

- **Layer 1 (threshold):** current price <= the user's target.
- **Layer 2 (relative value):** percentile framing where history exists
  (Amazon via Keepa) — "near all-time low"; elsewhere an *honest cold-start*
  that builds its own baseline from the observations recorded since tracking
  began (no fabricated baselines).

Dedup is by *improvement*: a layer re-fires only when the price drops strictly
below what that layer last alerted at, so a flat sub-target price does not
re-notify every interval. ``last_alert`` carries that state per layer.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from ..models import Alert, AlertLayer, PriceObservation, TrackedProduct
from ..workers.price_context import PriceContext


@dataclass(frozen=True)
class RuleParams:
    # Layer 2 (history available): alert when current sits at/below this
    # percentile of known history (near the all-time low).
    near_low_percentile: float = 15.0
    # Layer 2 (cold start): minimum observations before relative-value fires.
    cold_start_min_observations: int = 5
    # Layer 2 (cold start): alert when current is this fraction below the
    # trailing median of observed prices (e.g. 0.05 == 5% under median).
    cold_start_drop_fraction: float = 0.05
    # Re-arm: once the price rises this fraction above a layer's last alerted
    # price (Layer 1: above the target itself), that layer's dedup state
    # clears and the next drop into the trigger region alerts again.
    rearm_fraction: float = 0.03


def evaluate(
    tracked: TrackedProduct,
    observations: list[PriceObservation],
    ctx: PriceContext,
    params: RuleParams = RuleParams(),
) -> tuple[list[Alert], dict[str, int]]:
    """Evaluate all layers for the latest observation.

    Returns ``(alerts, updated_last_alert)``. ``observations`` must be ordered
    oldest->newest and include the just-recorded current reading.
    """
    last_alert = dict(tracked.last_alert_price_subunits)
    alerts: list[Alert] = []
    if not observations:
        return alerts, last_alert

    current = observations[-1]
    price = current.price_subunits

    # --- Re-arm ------------------------------------------------------------
    # Dedup-by-improvement alone would eat mid-cycle events: alert at $2,279,
    # rebound to $2,450 for two months, drop to $2,299 — no alert, because
    # $2,299 doesn't beat $2,279. Exiting the trigger region resets the layer,
    # so re-entering it is a new event. The caller must persist the returned
    # dedup state even when no alerts fire.
    if price > tracked.target_price_subunits:
        last_alert.pop(AlertLayer.THRESHOLD.value, None)
    l2_prev = last_alert.get(AlertLayer.RELATIVE_VALUE.value)
    if l2_prev is not None and price >= l2_prev * (1 + params.rearm_fraction):
        last_alert.pop(AlertLayer.RELATIVE_VALUE.value, None)

    # --- Layer 1: threshold ------------------------------------------------
    if price <= tracked.target_price_subunits:
        if _improves(last_alert, AlertLayer.THRESHOLD, price):
            alerts.append(
                _alert(
                    tracked,
                    AlertLayer.THRESHOLD,
                    "price_at_or_below_target",
                    f"{tracked.label}: ${price / 100:,.2f} "
                    f"(target ${tracked.target_price_subunits / 100:,.2f}) at {tracked.merchant}",
                    price,
                    ctx,
                )
            )
            last_alert[AlertLayer.THRESHOLD.value] = price

    # --- Layer 2: relative value ------------------------------------------
    l2 = _layer2(tracked, observations, ctx, params)
    if l2 is not None:
        rule, msg = l2
        if _improves(last_alert, AlertLayer.RELATIVE_VALUE, price):
            alerts.append(_alert(tracked, AlertLayer.RELATIVE_VALUE, rule, msg, price, ctx))
            last_alert[AlertLayer.RELATIVE_VALUE.value] = price

    return alerts, last_alert


def _layer2(
    tracked: TrackedProduct,
    observations: list[PriceObservation],
    ctx: PriceContext,
    params: RuleParams,
) -> tuple[str, str] | None:
    current = observations[-1].price_subunits

    if ctx.history_available and ctx.percentile is not None:
        if ctx.percentile <= params.near_low_percentile:
            atl = ctx.amazon_all_time_low_subunits
            atl_s = f"${atl / 100:,.2f}" if atl is not None else "n/a"
            return (
                "near_all_time_low",
                f"{tracked.label}: ${current / 100:,.2f} is at the "
                f"{ctx.percentile:.0f}th percentile of tracked history "
                f"(all-time low {atl_s})",
            )
        return None

    # Cold start: build our own baseline from prior observations.
    prior = [o.price_subunits for o in observations[:-1]]
    if len(prior) < params.cold_start_min_observations:
        return None
    median = statistics.median(prior)
    if current < min(prior):
        return (
            "cold_start_new_low",
            f"{tracked.label}: ${current / 100:,.2f} is a new low since tracking began "
            f"(prior low ${min(prior) / 100:,.2f}); no external history for {tracked.merchant}",
        )
    if current <= median * (1 - params.cold_start_drop_fraction):
        return (
            "cold_start_below_median",
            f"{tracked.label}: ${current / 100:,.2f} is "
            f"{(1 - current / median) * 100:.0f}% below the trailing median "
            f"(${median / 100:,.2f}); cold-start baseline, {tracked.merchant}",
        )
    return None


def _improves(last_alert: dict[str, int], layer: AlertLayer, price: int) -> bool:
    prev = last_alert.get(layer.value)
    return prev is None or price < prev


def _alert(
    tracked: TrackedProduct,
    layer: AlertLayer,
    rule: str,
    message: str,
    price: int,
    ctx: PriceContext,
) -> Alert:
    return Alert(
        tracked_product_id=int(tracked.id) if tracked.id is not None else -1,
        layer=layer,
        rule=rule,
        message=message,
        price_subunits=price,
        deep_link=tracked.url,
        payload={
            "merchant": tracked.merchant,
            "label": tracked.label,
            "target_subunits": tracked.target_price_subunits,
            "price_context": ctx.summary(),
        },
    )
