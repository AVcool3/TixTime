/** Typed client for the TixTime API.
 *
 * Every price-bearing payload carries `source` / `is_simulated`. Those fields
 * are required in the types on purpose: the <Price> component refuses to
 * render a figure without them, so dropping provenance is a type error rather
 * than a silent omission that ships fabricated dollars next to a Buy button.
 */

export interface Provenance {
  as_of: string;
  source: string;
  is_simulated: boolean;
  disclaimer: string;
  /** The ranked deal board is precomputed for a single date. When the clock is
   * moved off that date these say so, rather than serving stale rankings. */
  board_as_of?: string | null;
  board_covers_as_of?: boolean;
  board_note?: string;
}

export interface Meta extends Provenance {
  catalogue: {
    events: number;
    modelable_events: number;
    upcoming_events: number;
    venues: number;
    teams: number;
    price_snapshots: number;
    catalogue_start: string | null;
    catalogue_end: string | null;
  };
  model_version: string | null;
  clock_note: string;
}

export interface FacetValue { value: string; label: string; count: number }

export interface Filters extends Provenance {
  leagues: FacetValue[];
  cities: FacetValue[];
  venues: FacetValue[];
  teams: FacetValue[];
  postseason_tiers: FacetValue[];
}

export type Action = 'BUY_NOW' | 'WAIT' | 'NO_FORECAST';

export interface EventSummary {
  event_id: number;
  name: string;
  short_name: string | null;
  league: string | null;
  event_date: string | null;
  home_team: string | null;
  away_team: string | null;
  postseason_tier: string;
  venue_name: string | null;
  city: string | null;
  region: string | null;
  is_ga: boolean;
  seat_selection: boolean;
  is_forecastable: boolean;
  unforecastable_reason: string | null;
  purchase_url: string;
  days_until_event: number | null;
  best_tier: string | null;
  price_now: number | null;
  forecast_min: number | null;
  forecast_min_date: string | null;
  days_to_forecast_min: number | null;
  expected_saving: number | null;
  expected_saving_pct: number | null;
  action: Action | null;
  confidence: string | null;
  sparkline: number[] | null;
  source: string;
  is_simulated: boolean;
}

export interface SearchResponse extends Provenance {
  total: number; limit: number; offset: number; results: EventSummary[];
}

export interface Deal {
  event_id: number; name: string; league: string | null; event_date: string;
  home_team: string | null; away_team: string | null; postseason_tier: string;
  is_ga: boolean; url: string; venue_name: string; city: string; region: string;
  tier_key: string; tier_label: string; price_now: number; forecast_min: number;
  forecast_min_date: string | null; days_to_forecast_min: number | null;
  expected_saving: number; expected_saving_pct: number;
  action: Action; confidence: string; days_until_event: number;
  source: string; is_simulated: boolean;
}

export interface DealsResponse extends Provenance {
  total: number; limit: number; offset: number; deals: Deal[];
}

export interface EventDetail extends Provenance {
  event: {
    event_id: number; name: string; league: string | null; event_type: string | null;
    event_date: string | null; event_datetime_utc: string | null;
    home_team: string | null; away_team: string | null; postseason_tier: string;
    venue_name: string | null; city: string | null; region: string | null;
    capacity: number | null; is_ga: boolean; seat_selection: boolean;
    has_seat_tiers: boolean; is_forecastable: boolean;
    unforecastable_reason: string | null; purchase_url: string;
    announce_date: string | null;
  };
  tiers: TierRow[];
}

export interface TierRow {
  tier_key: string; tier_label: string; tier_rank: number | null;
  price_now: number; forecast_min: number; forecast_min_date: string | null;
  days_to_forecast_min: number | null; expected_saving: number;
  expected_saving_pct: number; action: Action; confidence: string;
  days_until_event: number; source: string; model_version: string;
}

export interface HistoryPoint {
  date: string; days_until_event: number;
  get_in_price: number; median_price: number; listing_count: number;
}

export interface ForecastPoint {
  date: string; days_until_event: number; horizon_days: number;
  q10: number; q50: number; q90: number;
}

export interface Recommendation {
  action: Action; confidence: string;
  price_now: number | null; forecast_min: number | null;
  forecast_min_date: string | null; days_to_forecast_min: number | null;
  expected_saving: number; expected_saving_pct: number;
  rationale: string; purchase_url: string;
  source: string; model_version: string;
}

export interface Timeline extends Provenance {
  event_id: number; tier_key: string | null;
  history: HistoryPoint[]; forecast: ForecastPoint[];
  recommendation: Recommendation | null;
  unforecastable_reason?: string;
}

export interface Interval {
  point: number; ci_low: number; ci_high: number; excludes_zero: boolean;
}

export interface StrategyStats {
  mean_paid: number; mean_regret: number; mean_regret_pct: number;
  savings_captured_pct: number; hit_rate_within_5pct: number; n: number;
}

export interface RegimeResult {
  description: string;
  verdict: 'survives' | 'degrades' | 'fails' | 'unknown';
  n_decisions: number;
  model_fall_through_rate: number;
  strategies: Record<string, StrategyStats>;
  model_vs_buy_now: number;
  model_vs_always_wait: number;
}

export interface RegimeHoldout {
  model_version: string;
  trained_on: string;
  n_events: number;
  question: string;
  regimes: Record<string, RegimeResult>;
  summary: string;
}

export interface Accuracy extends Provenance {
  backtest: {
    model_version: string; trained_through: string; data_source: string;
    caveat: string; n_events: number; n_decisions: number;
    overall: Record<string, StrategyStats>;
    by_start_window: Record<string, Record<string, StrategyStats>>;
    model_days_waited_median: number;
    model_fall_through_rate?: number;
    honest_reading?: string;
    uncertainty?: Record<string, Interval | string>;
  } | null;
  training: {
    model_version: string; trained_through: string; n_train_events: number;
    n_pairs: number; n_features: number;
    heads: Record<string, Record<string, number>>;
    train_seconds: number;
  } | null;
  regime_holdout: RegimeHoldout | null;
  interpretation: string;
}

export interface AlertRule {
  rule_id: string; event_id: number; tier_key: string | null; rule_type: string;
  threshold: number | null; channel: string; destination: string | null;
  label: string | null; created_at: string; active: boolean;
  event_name: string; event_date: string; purchase_url: string;
}

export interface AlertEvent {
  alert_id: string; rule_id: string; event_id: number; tier_key: string | null;
  fired_at: string; as_of_date: string; headline: string; body: string;
  price: number | null; purchase_url: string; delivered: boolean;
  acknowledged: boolean; event_name: string; event_date: string;
  rule_type: string | null; channel: string | null;
}

const BASE = '/api';

async function get<T>(path: string, params: Record<string, unknown> = {}): Promise<T> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value));
  }
  const suffix = query.toString() ? `?${query}` : '';
  const response = await fetch(`${BASE}${path}${suffix}`);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText} on ${path}`);
  return response.json() as Promise<T>;
}

async function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText} on ${path}`);
  return response.json() as Promise<T>;
}

export const api = {
  meta: (asOf?: string) => get<Meta>('/meta', { as_of: asOf }),
  filters: (asOf?: string) => get<Filters>('/filters', { as_of: asOf }),
  search: (params: Record<string, unknown>) => get<SearchResponse>('/search', params),
  deals: (params: Record<string, unknown>) => get<DealsResponse>('/deals', params),
  event: (id: number, asOf?: string) => get<EventDetail>(`/events/${id}`, { as_of: asOf }),
  timeline: (id: number, tier?: string, asOf?: string) =>
    get<Timeline>(`/events/${id}/timeline`, { tier_key: tier, as_of: asOf }),
  accuracy: (asOf?: string) => get<Accuracy>('/accuracy', { as_of: asOf }),
  alertRules: () => get<{ rules: AlertRule[] }>('/alerts/rules'),
  createAlertRule: (payload: Record<string, unknown>) =>
    send<{ rule_id: string }>('/alerts/rules', 'POST', payload),
  deleteAlertRule: (id: string) => send<unknown>(`/alerts/rules/${id}`, 'DELETE'),
  evaluateAlerts: (asOf?: string) =>
    send<{ fired: unknown[] }>(`/alerts/evaluate${asOf ? `?as_of=${asOf}` : ''}`, 'POST'),
  alerts: () => get<{ alerts: AlertEvent[] }>('/alerts'),
  ackAlert: (id: string) => send<unknown>(`/alerts/${id}/ack`, 'POST'),
};

/** Fixed hue order for seat tiers. Colour follows the tier, never its rank in
 * a filtered list -- filtering the chart must not repaint the survivors. */
export const TIER_ORDER = [
  'courtside', 'glass', 'dugout', 'field_sideline',
  'club',
  'lower_bowl', 'infield_lower', 'field_endzone',
  'upper_bowl', 'upper_sideline', 'outfield',
  'upper_corner', 'upper_end', 'upper_deck', 'upper_endzone',
  'ga',
];

const SERIES = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)', 'var(--series-5)'];

export function tierColor(tierKey: string): string {
  const index = TIER_ORDER.indexOf(tierKey);
  return SERIES[(index < 0 ? 0 : index) % SERIES.length];
}
