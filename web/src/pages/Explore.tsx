import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, EventSummary, Filters, Meta, tierColor } from '../lib/api';
import { useClock } from '../lib/clock';
import {
  ActionChip, BoardStaleNotice, Empty, Loading, Price, Sparkline, Stat,
  formatDate, formatPct,
} from '../components/Primitives';

const SORTS = [
  { key: 'date', label: 'Soonest' },
  { key: 'saving', label: 'Biggest saving' },
  { key: 'price', label: 'Cheapest' },
  { key: 'name', label: 'A–Z' },
] as const;

export function Explore({ meta }: { meta: Meta | null }) {
  const { asOf } = useClock();
  const [filters, setFilters] = useState<Filters | null>(null);
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  const [league, setLeague] = useState('');
  const [city, setCity] = useState('');
  const [team, setTeam] = useState('');
  const [tier, setTier] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [maxPrice, setMaxPrice] = useState('');
  const [sort, setSort] = useState<string>('date');
  const [includeAll, setIncludeAll] = useState(false);
  const [page, setPage] = useState(0);

  const [results, setResults] = useState<EventSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [boardNote, setBoardNote] = useState<string | undefined>();
  const [loading, setLoading] = useState(true);

  const limit = 60;

  useEffect(() => {
    api.filters(asOf).then(setFilters).catch(() => setFilters(null));
  }, [asOf]);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query), 220);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => { setPage(0); }, [debounced, league, city, team, tier, dateFrom, dateTo, maxPrice, sort, includeAll]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .search({
        as_of: asOf, q: debounced || undefined, league: league || undefined,
        city: city || undefined, team: team || undefined,
        postseason_tier: tier || undefined,
        date_from: dateFrom || undefined, date_to: dateTo || undefined,
        max_price: maxPrice || undefined, include_unforecastable: includeAll,
        sort, limit, offset: page * limit,
      })
      .then((response) => {
        if (cancelled) return;
        setResults(response.results);
        setTotal(response.total);
        setBoardNote(response.board_covers_as_of === false ? response.board_note : undefined);
      })
      .catch(() => { if (!cancelled) { setResults([]); setTotal(0); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [asOf, debounced, league, city, team, tier, dateFrom, dateTo, maxPrice, sort, page, includeAll]);

  const activeFilters = [league, city, team, tier, dateFrom, dateTo, maxPrice].filter(Boolean).length;

  const headline = useMemo(() => {
    const waiting = results.filter((r) => r.action === 'WAIT').length;
    const savings = results
      .map((r) => r.expected_saving ?? 0)
      .filter((v) => v > 0);
    const median = savings.length
      ? savings.sort((a, b) => a - b)[Math.floor(savings.length / 2)]
      : null;
    return { waiting, median };
  }, [results]);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Explore the catalogue</h1>
          <p>
            {meta ? `${meta.catalogue.upcoming_events.toLocaleString()} upcoming events across ${meta.catalogue.venues} venues and ${meta.catalogue.teams} teams. ` : ''}
            Filter by league, city, team or date, then open an event to see its full price path and buy timing.
          </p>
        </div>
      </div>

      <BoardStaleNotice note={boardNote} />

      <div className="layout-rail">
        <aside className="card card-pad" style={{ position: 'sticky', top: 74 }}>
          <div className="row" style={{ marginBottom: 12 }}>
            <h3 style={{ flex: 1 }}>Filters</h3>
            {activeFilters ? (
              <button
                className="tiny"
                style={{ padding: '3px 8px' }}
                onClick={() => {
                  setLeague(''); setCity(''); setTeam(''); setTier('');
                  setDateFrom(''); setDateTo(''); setMaxPrice('');
                }}
              >
                Clear {activeFilters}
              </button>
            ) : null}
          </div>

          <label className="field">
            <span>Search</span>
            <input
              placeholder="Team, venue or city…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>

          <FacetSelect label="League" value={league} onChange={setLeague} options={filters?.leagues} />
          <FacetSelect label="Team (home)" value={team} onChange={setTeam} options={filters?.teams} />
          <FacetSelect label="City" value={city} onChange={setCity} options={filters?.cities} />
          <FacetSelect label="Event type" value={tier} onChange={setTier} options={filters?.postseason_tiers} />

          <label className="field">
            <span>From</span>
            <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
          </label>
          <label className="field">
            <span>To</span>
            <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
          </label>
          <label className="field">
            <span>Max price today</span>
            <input
              type="number" min={0} placeholder="any"
              value={maxPrice} onChange={(e) => setMaxPrice(e.target.value)}
            />
          </label>

          <div className="divider" />

          {/* Off by default: the catalogue holds 1,316 stadium tours and other
              non-game inventory that cannot be forecast, and they sort to the
              front by date. */}
          <label className="row small" style={{ gap: 8, cursor: 'pointer' }}>
            <input
              type="checkbox" checked={includeAll} style={{ width: 14, height: 14 }}
              onChange={(e) => setIncludeAll(e.target.checked)}
            />
            <span className="secondary">Include events with no forecast</span>
          </label>
          <div className="tiny muted" style={{ marginTop: 5 }}>
            Tours, tailgates, suites and TBD playoff placeholders — real listings, but nothing
            to time.
          </div>
        </aside>

        <section>
          <div className="stat-row" style={{ marginBottom: 14 }}>
            <Stat label="Matching events" value={total.toLocaleString()} />
            <Stat
              label="Model says wait"
              value={`${headline.waiting}/${results.length}`}
              note="on this page"
              tone="warn"
            />
            <Stat
              label="Median projected saving"
              value={headline.median !== null ? <Price value={headline.median} source="synthetic_v1" size="lg" /> : '—'}
              note="best tier, this page"
              tone="good"
            />
          </div>

          <div className="row-wrap" style={{ marginBottom: 12 }}>
            <span className="tiny muted" style={{ textTransform: 'uppercase', letterSpacing: '.05em' }}>Sort</span>
            {SORTS.map((option) => (
              <button
                key={option.key}
                onClick={() => setSort(option.key)}
                style={{
                  padding: '4px 10px', fontSize: 12,
                  background: sort === option.key ? 'var(--surface-3)' : 'var(--surface-1)',
                  borderColor: sort === option.key ? 'var(--border-strong)' : 'var(--border)',
                }}
              >
                {option.label}
              </button>
            ))}
            <span className="spacer" />
            <span className="tiny muted">
              {total > 0 ? `${page * limit + 1}–${Math.min((page + 1) * limit, total)} of ${total.toLocaleString()}` : ''}
            </span>
            <button disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Prev</button>
            <button disabled={(page + 1) * limit >= total} onClick={() => setPage((p) => p + 1)}>Next</button>
          </div>

          {loading ? (
            <div className="grid-cards">
              {Array.from({ length: 9 }, (_, i) => <div key={i} className="skeleton" style={{ height: 150 }} />)}
            </div>
          ) : results.length === 0 ? (
            <Empty title="No events match those filters" detail="Try widening the date range or clearing a facet." />
          ) : (
            <div className="grid-cards">
              {results.map((event) => <EventCard key={event.event_id} event={event} />)}
            </div>
          )}
        </section>
      </div>
    </>
  );
}

function FacetSelect({
  label, value, onChange, options,
}: {
  label: string; value: string; onChange: (v: string) => void;
  options: { value: string; label: string; count: number }[] | undefined;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">All</option>
        {(options ?? []).map((option) => (
          <option key={option.value} value={option.value}>
            {option.label} ({option.count.toLocaleString()})
          </option>
        ))}
      </select>
    </label>
  );
}

function EventCard({ event }: { event: EventSummary }) {
  const color = tierColor(event.best_tier ?? 'ga');
  return (
    <Link to={`/events/${event.event_id}`} className="event-card">
      <div>
        <div className="event-title">{event.name}</div>
        <div className="event-meta" style={{ marginTop: 4 }}>
          <span>{formatDate(event.event_date, { weekday: 'short', month: 'short', day: 'numeric' })}</span>
          <span>·</span>
          <span>{event.venue_name}</span>
          {event.city ? <><span>·</span><span>{event.city}</span></> : null}
          {event.league ? <><span>·</span><span style={{ textTransform: 'uppercase' }}>{event.league}</span></> : null}
        </div>
      </div>

      <div className="event-foot">
        <div>
          <div className="tiny muted">
            {event.is_forecastable ? 'Cheapest tier today' : 'No forecast'}
          </div>
          <Price value={event.price_now} source={event.source} size="lg" />
          {event.is_forecastable && event.expected_saving && event.expected_saving > 0.5 ? (
            <div className="tiny" style={{ color: 'var(--status-good)', marginTop: 2 }}>
              −<Price value={event.expected_saving} source={event.source} size="sm" tone="good" />
              {' '}({formatPct(event.expected_saving_pct)}) if you wait
            </div>
          ) : (
            <div className="tiny muted" style={{ marginTop: 2 }}>
              {event.days_until_event !== null ? `${event.days_until_event} days out` : '—'}
            </div>
          )}
        </div>
        <div style={{ textAlign: 'right' }}>
          <Sparkline points={event.sparkline} color={color} />
          <div style={{ marginTop: 6 }}>
            <ActionChip action={event.action} confidence={event.confidence} />
          </div>
        </div>
      </div>
    </Link>
  );
}
