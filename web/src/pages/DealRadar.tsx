import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Deal, api, tierColor } from '../lib/api';
import { useClock } from '../lib/clock';
import { BoardStaleNotice, Empty, Loading, Price, Stat, formatDate, formatPct } from '../components/Primitives';

/**
 * The cross-event view.
 *
 * "Which game, which section, and when do I buy?" is a comparison ACROSS
 * events, not a lookup within one. Every other screen is keyed to a single
 * event id; without this page the product is a lookup tool rather than a
 * timing engine. Served from the precomputed deal board, so filtering is a
 * single indexed query rather than a model call per row.
 */
export function DealRadar() {
  const { asOf } = useClock();
  const [deals, setDeals] = useState<Deal[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [boardNote, setBoardNote] = useState<string | undefined>();
  const [league, setLeague] = useState('');
  const [maxPrice, setMaxPrice] = useState('');
  const [minSaving, setMinSaving] = useState('0.15');
  const [horizon, setHorizon] = useState('');
  const [page, setPage] = useState(0);
  const limit = 60;

  useEffect(() => { setPage(0); }, [league, maxPrice, minSaving, horizon]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const params: Record<string, unknown> = {
      as_of: asOf, league: league || undefined,
      max_price: maxPrice || undefined,
      min_saving_pct: minSaving || 0,
      limit, offset: page * limit,
    };
    if (horizon) {
      const to = new Date(`${asOf}T00:00:00`);
      to.setDate(to.getDate() + Number(horizon));
      params.date_to = to.toISOString().slice(0, 10);
    }
    api
      .deals(params)
      .then((response) => {
        if (cancelled) return;
        setDeals(response.deals);
        setTotal(response.total);
        setBoardNote(response.board_covers_as_of === false ? response.board_note : undefined);
      })
      .catch(() => { if (!cancelled) { setDeals([]); setTotal(0); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [asOf, league, maxPrice, minSaving, horizon, page]);

  const topSaving = deals.length ? deals[0] : null;
  const totalOpportunity = deals.reduce((sum, deal) => sum + deal.expected_saving, 0);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Deal Radar</h1>
          <p>
            Every upcoming event and seat tier, ranked by how much the model expects waiting to
            save. This is the cross-event question — not “what does this game cost?” but
            “where in the whole catalogue is patience worth the most right now?”
          </p>
        </div>
      </div>

      <BoardStaleNotice note={boardNote} />

      <div className="stat-row" style={{ marginBottom: 16 }}>
        <Stat label="Ranked opportunities" value={total.toLocaleString()} note={`at ≥${formatPct(Number(minSaving))} projected saving`} />
        <Stat
          label="Best on this page"
          value={topSaving ? formatPct(topSaving.expected_saving_pct) : '—'}
          note={topSaving ? topSaving.name.slice(0, 42) : undefined}
          tone="good"
        />
        <Stat
          label="Combined saving, this page"
          value={<Price value={totalOpportunity} source="synthetic_v1" size="lg" />}
          note={`${deals.length} listings`}
          tone="good"
        />
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-pad row-wrap" style={{ gap: 14 }}>
          <label style={{ minWidth: 130 }}>
            <span className="tiny muted" style={{ display: 'block', marginBottom: 3 }}>League</span>
            <select value={league} onChange={(e) => setLeague(e.target.value)}>
              <option value="">All leagues</option>
              <option value="nba">NBA</option>
              <option value="nhl">NHL</option>
              <option value="mlb">MLB</option>
              <option value="nfl">NFL</option>
            </select>
          </label>
          <label style={{ minWidth: 130 }}>
            <span className="tiny muted" style={{ display: 'block', marginBottom: 3 }}>Within</span>
            <select value={horizon} onChange={(e) => setHorizon(e.target.value)}>
              <option value="">Any date</option>
              <option value="14">14 days</option>
              <option value="30">30 days</option>
              <option value="60">60 days</option>
              <option value="90">90 days</option>
            </select>
          </label>
          <label style={{ minWidth: 130 }}>
            <span className="tiny muted" style={{ display: 'block', marginBottom: 3 }}>Min saving</span>
            <select value={minSaving} onChange={(e) => setMinSaving(e.target.value)}>
              <option value="0">Any</option>
              <option value="0.05">5%+</option>
              <option value="0.15">15%+</option>
              <option value="0.25">25%+</option>
              <option value="0.4">40%+</option>
            </select>
          </label>
          <label style={{ minWidth: 130 }}>
            <span className="tiny muted" style={{ display: 'block', marginBottom: 3 }}>Max price today</span>
            <input type="number" min={0} placeholder="any" value={maxPrice} onChange={(e) => setMaxPrice(e.target.value)} />
          </label>
          <span className="spacer" />
          <div className="row">
            <button disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Prev</button>
            <span className="tiny muted mono">
              {total > 0 ? `${page * limit + 1}–${Math.min((page + 1) * limit, total)}` : '0'}
            </span>
            <button disabled={(page + 1) * limit >= total} onClick={() => setPage((p) => p + 1)}>Next</button>
          </div>
        </div>
      </div>

      {loading ? (
        <Loading rows={8} height={44} />
      ) : deals.length === 0 ? (
        <Empty title="Nothing clears that bar" detail="Lower the minimum saving or widen the date range." />
      ) : (
        <div className="card table-scroll">
          <table>
            <thead>
              <tr>
                <th style={{ minWidth: 260 }}>Event</th>
                <th>Seat tier</th>
                <th>Date</th>
                <th className="num">Today</th>
                <th className="num">Projected low</th>
                <th>Buy around</th>
                <th className="num">Saving</th>
                <th>Confidence</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {deals.map((deal) => (
                <tr key={`${deal.event_id}-${deal.tier_key}`}>
                  <td>
                    <Link to={`/events/${deal.event_id}`} style={{ fontWeight: 550 }}>{deal.name}</Link>
                    <div className="tiny muted">{deal.venue_name} · {deal.city}</div>
                  </td>
                  <td>
                    <span className="row" style={{ gap: 6 }}>
                      <span style={{
                        width: 8, height: 8, borderRadius: 2,
                        background: tierColor(deal.tier_key), flexShrink: 0,
                      }} />
                      <span className="small">{deal.tier_label}</span>
                    </span>
                  </td>
                  <td className="small secondary">
                    {formatDate(deal.event_date, { month: 'short', day: 'numeric' })}
                    <div className="tiny muted">{deal.days_until_event}d out</div>
                  </td>
                  <td className="num"><Price value={deal.price_now} source={deal.source} /></td>
                  <td className="num"><Price value={deal.forecast_min} source={deal.source} tone="good" /></td>
                  <td className="small secondary">
                    {deal.forecast_min_date ? formatDate(deal.forecast_min_date, { month: 'short', day: 'numeric' }) : '—'}
                    {deal.days_to_forecast_min ? <div className="tiny muted">in {deal.days_to_forecast_min}d</div> : null}
                  </td>
                  <td className="num">
                    <span style={{ color: 'var(--status-good)', fontWeight: 600 }}>
                      {formatPct(deal.expected_saving_pct)}
                    </span>
                    <div className="tiny muted">
                      <Price value={deal.expected_saving} source={deal.source} size="sm" />
                    </div>
                  </td>
                  <td>
                    <ConfidenceBar level={deal.confidence} />
                  </td>
                  <td>
                    <a
                      className="btn tiny"
                      href={deal.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ whiteSpace: 'nowrap' }}
                    >
                      SeatGeek ↗
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/** Confidence is ordinal, so it gets an ordinal mark plus its label -- never
 * colour alone, and never a spurious percentage the model does not produce. */
function ConfidenceBar({ level }: { level: string }) {
  const filled = level === 'high' ? 3 : level === 'medium' ? 2 : 1;
  const color = level === 'high' ? 'var(--status-good)' : level === 'medium' ? 'var(--status-warn)' : 'var(--text-muted)';
  return (
    <span className="row" style={{ gap: 5 }}>
      <span className="row" style={{ gap: 2 }}>
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            style={{
              width: 5, height: 12, borderRadius: 1,
              background: i < filled ? color : 'var(--surface-3)',
            }}
          />
        ))}
      </span>
      <span className="tiny muted">{level}</span>
    </span>
  );
}
