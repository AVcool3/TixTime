import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { EventDetail, Timeline, api, tierColor } from '../lib/api';
import { useClock } from '../lib/clock';
import { PriceChart } from '../components/PriceChart';
import {
  ActionChip, Empty, Loading, Price, Stat, UnforecastableNotice,
  formatDate, formatPct,
} from '../components/Primitives';
import { AlertDialog } from '../components/AlertDialog';

export function EventPage() {
  const { eventId } = useParams();
  const id = Number(eventId);
  const { asOf } = useClock();

  const [detail, setDetail] = useState<EventDetail | null>(null);
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [tier, setTier] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [alertOpen, setAlertOpen] = useState(false);

  useEffect(() => {
    setLoading(true);
    setTier(null);
    api.event(id, asOf)
      .then((response) => {
        setDetail(response);
        setTier(response.tiers.length ? response.tiers[0].tier_key : null);
      })
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [id, asOf]);

  useEffect(() => {
    if (!detail) return;
    api.timeline(id, tier ?? undefined, asOf).then(setTimeline).catch(() => setTimeline(null));
  }, [id, tier, asOf, detail]);

  const activeTier = useMemo(
    () => detail?.tiers.find((t) => t.tier_key === tier) ?? detail?.tiers[0] ?? null,
    [detail, tier],
  );

  if (loading) return <Loading rows={4} height={120} />;
  if (!detail) return <Empty title="Event not found" />;

  const event = detail.event;
  const recommendation = timeline?.recommendation ?? null;
  const color = tierColor(activeTier?.tier_key ?? 'ga');

  return (
    <>
      <div className="row-wrap" style={{ marginBottom: 12, gap: 6 }}>
        <Link to="/" className="tiny muted">← Explore</Link>
        <span className="tiny muted">/</span>
        <span className="tiny muted">{event.league?.toUpperCase() ?? 'Event'}</span>
      </div>

      <div className="page-head">
        <div style={{ flex: 1, minWidth: 280 }}>
          <h1>{event.name}</h1>
          <p>
            {formatDate(event.event_date, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })}
            {' · '}{event.venue_name}
            {event.city ? ` · ${event.city}, ${event.region}` : ''}
            {event.capacity ? ` · ~${event.capacity.toLocaleString()} seats` : ''}
          </p>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button onClick={() => setAlertOpen(true)}>+ Alert me</button>
          <a className="btn btn-primary" href={event.purchase_url} target="_blank" rel="noopener noreferrer">
            Open on SeatGeek ↗
          </a>
        </div>
      </div>

      {!event.is_forecastable ? (
        <UnforecastableNotice reason={event.unforecastable_reason} />
      ) : null}

      {/* --- decision panel ------------------------------------------- */}
      {recommendation && event.is_forecastable ? (
        <div className="card" style={{ marginBottom: 14, overflow: 'hidden' }}>
          <div
            className="card-pad"
            style={{
              display: 'grid',
              gridTemplateColumns: 'minmax(240px, 320px) 1fr',
              gap: 22,
              alignItems: 'center',
              background:
                recommendation.action === 'BUY_NOW'
                  ? 'linear-gradient(90deg, var(--status-good-dim), transparent 65%)'
                  : 'linear-gradient(90deg, var(--status-warn-dim), transparent 65%)',
            }}
          >
            <div>
              <div className="row" style={{ marginBottom: 8 }}>
                <ActionChip action={recommendation.action} confidence={recommendation.confidence} />
              </div>
              <div className="row" style={{ alignItems: 'baseline', gap: 10 }}>
                <Price value={recommendation.price_now} source={recommendation.source} size="hero" />
                <span className="small muted">today</span>
              </div>
              {recommendation.action === 'WAIT' && recommendation.expected_saving > 0 ? (
                <div className="small" style={{ color: 'var(--status-good)', marginTop: 5 }}>
                  ↓ <Price value={recommendation.forecast_min} source={recommendation.source} tone="good" />
                  {' '}projected around {formatDate(recommendation.forecast_min_date)}
                  {' — save '}<strong>{formatPct(recommendation.expected_saving_pct)}</strong>
                </div>
              ) : null}
            </div>
            <div>
              <p style={{ margin: 0, fontSize: 15, lineHeight: 1.55 }}>{recommendation.rationale}</p>
              <div className="tiny muted" style={{ marginTop: 8 }}>
                Model {recommendation.model_version} · evaluated as of {formatDate(asOf)} ·
                {' '}simulated prices
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {/* --- hero chart ------------------------------------------------ */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <h2>Price path — {activeTier?.tier_label ?? 'all seats'}</h2>
          <span className="tiny muted">
            observed through {formatDate(asOf)}, forecast to event day
          </span>
        </div>
        <div className="card-pad">
          {timeline && timeline.history.length ? (
            <PriceChart
              history={timeline.history}
              forecast={timeline.forecast}
              recommendation={recommendation}
              color={color}
              tierLabel={activeTier?.tier_label ?? 'All seats'}
              source={timeline.source}
              height={400}
            />
          ) : (
            <div className="empty">No price history for this event at the current clock date.</div>
          )}
        </div>
      </div>

      {/* --- seat tiers ------------------------------------------------- */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <h2>Seats</h2>
          {event.has_seat_tiers ? (
            <span className="tiny muted">
              select a tier to redraw the chart — timing differs by section
            </span>
          ) : (
            <span className="chip chip-none">
              {event.is_ga ? 'General admission — one price tier' : 'Seat selection not offered for this event'}
            </span>
          )}
        </div>

        {detail.tiers.length === 0 ? (
          <div className="card-pad"><div className="empty">No tier pricing available.</div></div>
        ) : !event.has_seat_tiers ? (
          <div className="card-pad">
            <p className="small secondary" style={{ marginTop: 0 }}>
              SeatGeek sells this event without seat selection, so TixTime models a single
              price tier for it rather than inventing sections.
            </p>
            <TierTable tiers={detail.tiers} active={tier} onSelect={setTier} selectable={false} />
          </div>
        ) : (
          <div className="table-scroll">
            <TierTable tiers={detail.tiers} active={tier} onSelect={setTier} selectable />
          </div>
        )}
      </div>

      {/* --- how to act ------------------------------------------------- */}
      {event.is_forecastable && recommendation ? (
        <div className="card card-pad">
          <h3 style={{ marginBottom: 8 }}>Buying this</h3>
          <p className="small secondary" style={{ marginTop: 0, maxWidth: '80ch' }}>
            TixTime does not sell tickets and cannot deep-link to an individual seat — SeatGeek's
            URL addresses the event, not a section. What it can tell you is which section to filter
            to and what a good price looks like when you get there.
          </p>
          <div className="row-wrap" style={{ gap: 10, marginTop: 12 }}>
            <a className="btn btn-buy" href={event.purchase_url} target="_blank" rel="noopener noreferrer">
              Open {event.name.slice(0, 40)} on SeatGeek ↗
            </a>
            {activeTier ? (
              <span className="chip">
                Then filter to <strong style={{ marginLeft: 4 }}>{activeTier.tier_label}</strong>
                {' '}· target{' '}
                {/* Through <Price>, not a raw toFixed: this is the highest-trust
                    moment in the product and the figure must carry its
                    provenance like every other price on the site. */}
                <Price value={activeTier.forecast_min} source={activeTier.source} size="sm" />
                {'–'}
                <Price value={activeTier.price_now} source={activeTier.source} size="sm" />
              </span>
            ) : null}
            <button onClick={() => setAlertOpen(true)}>Alert me when the model says buy</button>
          </div>
        </div>
      ) : null}

      {alertOpen ? (
        <AlertDialog
          eventId={id}
          eventName={event.name}
          tiers={detail.tiers}
          defaultTier={tier}
          onClose={() => setAlertOpen(false)}
        />
      ) : null}
    </>
  );
}

function TierTable({
  tiers, active, onSelect, selectable,
}: {
  tiers: EventDetail['tiers']; active: string | null;
  onSelect: (key: string) => void; selectable: boolean;
}) {
  return (
    <table>
      <thead>
        <tr>
          <th>Tier</th>
          <th className="num">Today</th>
          <th className="num">Projected low</th>
          <th>Best around</th>
          <th className="num">Saving</th>
          <th>Signal</th>
        </tr>
      </thead>
      <tbody>
        {tiers.map((row) => {
          const isActive = row.tier_key === active;
          return (
            <tr
              key={row.tier_key}
              onClick={() => selectable && onSelect(row.tier_key)}
              style={{
                cursor: selectable ? 'pointer' : 'default',
                background: isActive ? 'var(--surface-2)' : undefined,
                boxShadow: isActive ? `inset 3px 0 0 ${tierColor(row.tier_key)}` : undefined,
              }}
            >
              <td>
                <span className="row" style={{ gap: 7 }}>
                  <span style={{
                    width: 9, height: 9, borderRadius: 2,
                    background: tierColor(row.tier_key), flexShrink: 0,
                  }} />
                  <strong style={{ fontWeight: isActive ? 650 : 500 }}>{row.tier_label}</strong>
                </span>
              </td>
              <td className="num"><Price value={row.price_now} source={row.source} /></td>
              <td className="num"><Price value={row.forecast_min} source={row.source} tone="good" /></td>
              <td className="small secondary">
                {row.forecast_min_date ? formatDate(row.forecast_min_date, { month: 'short', day: 'numeric' }) : '—'}
              </td>
              <td className="num" style={{ color: row.expected_saving_pct > 0.02 ? 'var(--status-good)' : 'var(--text-muted)' }}>
                {formatPct(row.expected_saving_pct)}
              </td>
              <td><ActionChip action={row.action} confidence={row.confidence} /></td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
