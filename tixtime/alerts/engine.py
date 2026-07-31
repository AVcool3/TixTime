"""Alert rules and delivery.

DESIGN NOTE ON HONESTY
----------------------
"Notify me when to buy" is easy to fake and hard to do. This environment has
no SMTP, no push infrastructure and no outbound network, and a browser
Notification only fires while the tab is open -- so it cannot tell anyone
anything when they are not already looking.

Rather than ship three dead code paths, the in-app inbox is the SYSTEM OF
RECORD. Every fired alert is written to `alert_events` regardless of channel,
and each delivery attempt records its true status: 'delivered' for in-app,
'unconfigured' for webhook/email when no destination or credentials exist.
The UI shows those statuses per alert instead of implying a message was sent.
Configure a webhook and it genuinely fires; leave it unset and the UI says so.
"""

from __future__ import annotations

import abc
import json
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from tixtime import db
from tixtime.config import as_of_date

MODEL_BUY = "model_buy"
TARGET_PRICE = "target_price"
DROP_PCT = "drop_pct"

RULE_TYPES = (MODEL_BUY, TARGET_PRICE, DROP_PCT)


@dataclass(frozen=True)
class Delivery:
    channel: str
    status: str      # 'delivered' | 'unconfigured' | 'failed'
    detail: str


class Channel(abc.ABC):
    name: str

    @abc.abstractmethod
    def send(self, headline: str, body: str, destination: str | None) -> Delivery: ...


class InAppChannel(Channel):
    """Always works: the alert row itself is the delivery."""

    name = "inapp"

    def send(self, headline: str, body: str, destination: str | None) -> Delivery:
        return Delivery(self.name, "delivered", "Shown in the TixTime alert inbox.")


class WebhookChannel(Channel):
    name = "webhook"

    def send(self, headline: str, body: str, destination: str | None) -> Delivery:
        if not destination:
            return Delivery(self.name, "unconfigured", "No webhook URL set on this rule.")
        import urllib.error
        import urllib.request

        payload = json.dumps({"headline": headline, "body": body}).encode()
        request = urllib.request.Request(
            destination, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return Delivery(self.name, "delivered", f"HTTP {response.status}")
        except (urllib.error.URLError, OSError) as exc:
            return Delivery(self.name, "failed", f"{type(exc).__name__}: {exc}")


class EmailChannel(Channel):
    name = "email"

    def send(self, headline: str, body: str, destination: str | None) -> Delivery:
        host = os.environ.get("TIXTIME_SMTP_HOST")
        if not host:
            return Delivery(self.name, "unconfigured", "TIXTIME_SMTP_HOST is not set.")
        if not destination:
            return Delivery(self.name, "unconfigured", "No email address set on this rule.")
        import smtplib
        from email.message import EmailMessage

        message = EmailMessage()
        message["Subject"] = headline
        message["From"] = os.environ.get("TIXTIME_SMTP_FROM", "tixtime@localhost")
        message["To"] = destination
        message.set_content(body)
        try:
            with smtplib.SMTP(host, int(os.environ.get("TIXTIME_SMTP_PORT", "25")), timeout=10) as smtp:
                smtp.send_message(message)
            return Delivery(self.name, "delivered", f"Sent via {host}")
        except OSError as exc:
            return Delivery(self.name, "failed", f"{type(exc).__name__}: {exc}")


CHANNELS: dict[str, Channel] = {c.name: c for c in (InAppChannel(), WebhookChannel(), EmailChannel())}


def create_rule(
    con,
    event_id: int,
    rule_type: str,
    tier_key: str | None = None,
    threshold: float | None = None,
    channel: str = "inapp",
    destination: str | None = None,
    label: str | None = None,
) -> str:
    if rule_type not in RULE_TYPES:
        raise ValueError(f"unknown rule type {rule_type!r}; expected one of {RULE_TYPES}")
    if rule_type in (TARGET_PRICE, DROP_PCT) and threshold is None:
        raise ValueError(f"{rule_type} rules need a threshold")
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r}; expected one of {sorted(CHANNELS)}")

    rule_id = uuid.uuid4().hex[:12]
    con.execute(
        """
        INSERT INTO alert_rules
            (rule_id, event_id, tier_key, rule_type, threshold, channel,
             destination, label, created_at, active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)
        """,
        [rule_id, event_id, tier_key, rule_type, threshold, channel,
         destination, label, datetime.now()],
    )
    return rule_id


def _headline(rule_type: str, event_name: str, tier_label: str, price: float, extra: str) -> tuple[str, str]:
    if rule_type == MODEL_BUY:
        return (
            f"Buy now: {event_name}",
            f"TixTime's model has switched to BUY for {tier_label}. "
            f"Current get-in price ${price:,.0f}. {extra}",
        )
    if rule_type == TARGET_PRICE:
        return (
            f"Target hit: {event_name}",
            f"{tier_label} has reached ${price:,.0f}, at or below your target. {extra}",
        )
    return (
        f"Price drop: {event_name}",
        f"{tier_label} has fallen to ${price:,.0f}. {extra}",
    )


def evaluate(con, as_of: date | None = None) -> list[dict]:
    """Check every active rule against the deal board and fire what matches.

    Idempotent per (rule, as-of date): re-running on the same simulated day
    will not duplicate alerts.
    """
    as_of = as_of or as_of_date()
    rules = con.execute(
        "SELECT rule_id, event_id, tier_key, rule_type, threshold, channel, destination "
        "FROM alert_rules WHERE active"
    ).df()
    if rules.empty:
        return []

    # DuckDB returns SQL NULL as pandas NaN, not None. Passing that NaN back as
    # a query parameter made `CAST(? AS VARCHAR)` render the STRING 'nan',
    # which is not NULL -- so every "watch any tier" rule (tier_key NULL)
    # matched no tiers and silently never fired. Normalise NA to None once,
    # here, rather than guarding at each use.
    rules = rules.astype(object).where(pd.notna(rules), None)

    fired = []
    for rule in rules.itertuples():
        already = con.execute(
            "SELECT count(*) FROM alert_events WHERE rule_id = ? AND as_of_date = ?",
            [rule.rule_id, as_of],
        ).fetchone()[0]
        if already:
            continue

        board = con.execute(
            """
            SELECT d.*, e.name, e.url, t.label AS tier_label
            FROM deal_board d
            JOIN events e USING (event_id)
            LEFT JOIN seat_tiers t
                   ON t.tier_key = d.tier_key
                  AND t.archetype = (SELECT archetype FROM venues v WHERE v.venue_id = e.venue_id)
            WHERE d.event_id = ?
              -- The board is precomputed per date; without this an evaluation
              -- for an earlier simulated day would fire on prices from after
              -- the day being evaluated.
              AND d.as_of_date = ?
              -- CAST is required: DuckDB types a bare `? IS NULL` placeholder
              -- as DOUBLE, then tries to cast tier_key to DOUBLE and fails
              -- with "Could not convert string 'lower_bowl' to DOUBLE".
              -- Only triggers for rules that watch every tier (tier_key NULL).
              AND (CAST(? AS VARCHAR) IS NULL OR d.tier_key = CAST(? AS VARCHAR))
            ORDER BY d.expected_saving_pct DESC
            """,
            [rule.event_id, as_of, rule.tier_key, rule.tier_key],
        ).df()
        if board.empty:
            continue

        for row in board.itertuples():
            price = float(row.price_now)
            matched, extra = False, ""
            if rule.rule_type == MODEL_BUY and row.action == "BUY_NOW":
                matched = True
                extra = f"Confidence: {row.confidence}."
            elif rule.rule_type == TARGET_PRICE and rule.threshold is not None and price <= rule.threshold:
                matched = True
                extra = f"Your target was ${float(rule.threshold):,.0f}."
            elif rule.rule_type == DROP_PCT and rule.threshold is not None:
                spark = con.execute(
                    "SELECT high FROM event_sparkline WHERE event_id = ? AND tier_key = ?",
                    [rule.event_id, row.tier_key],
                ).fetchone()
                if spark and spark[0] and price <= float(spark[0]) * (1 - float(rule.threshold) / 100):
                    matched = True
                    extra = f"Down {100 * (1 - price / float(spark[0])):.0f}% from its window high."

            if not matched:
                continue

            tier_label = row.tier_label or row.tier_key
            headline, body = _headline(rule.rule_type, row.name, tier_label, price, extra)
            delivery = CHANNELS[rule.channel].send(headline, body, rule.destination)
            alert_id = uuid.uuid4().hex[:12]
            con.execute(
                """
                INSERT INTO alert_events
                    (alert_id, rule_id, event_id, tier_key, fired_at, as_of_date,
                     headline, body, price, purchase_url, delivered, acknowledged)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE)
                """,
                [alert_id, rule.rule_id, int(row.event_id), row.tier_key, datetime.now(),
                 as_of, headline, f"{body} [{delivery.channel}: {delivery.status} -- {delivery.detail}]",
                 price, row.url, delivery.status == "delivered"],
            )
            fired.append(
                {
                    "alert_id": alert_id,
                    "rule_id": rule.rule_id,
                    "event_id": int(row.event_id),
                    "tier_key": row.tier_key,
                    "headline": headline,
                    "channel": delivery.channel,
                    "status": delivery.status,
                    "detail": delivery.detail,
                }
            )
            break  # one alert per rule per day

    return fired


def main() -> None:
    with db.warehouse() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_rules (
                rule_id VARCHAR PRIMARY KEY, event_id BIGINT NOT NULL, tier_key VARCHAR,
                rule_type VARCHAR NOT NULL, threshold DOUBLE, channel VARCHAR NOT NULL,
                destination VARCHAR, label VARCHAR, created_at TIMESTAMP NOT NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE)
            """
        )
        fired = evaluate(con)
    print(f"fired {len(fired)} alert(s)")
    for alert in fired:
        print(f"  [{alert['status']:<12}] {alert['headline']}")


if __name__ == "__main__":
    main()
