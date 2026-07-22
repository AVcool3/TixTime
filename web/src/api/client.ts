export type Venue = {
  venue_id: number;
  name: string;
  city: string;
  state: string;
  capacity: number | null;
  popularity: number | null;
};

export type VenueEvent = {
  event_id: number;
  name: string;
  datetime_utc: string;
  taxonomy_name: string | null;
  lowest_price: number | null;
  median_price: number | null;
  listing_count: number | null;
  last_observed: string | null;
};

export type EventDetail = {
  event_id: number;
  name: string;
  datetime_utc: string;
  venue_name: string;
  venue_city: string;
  venue_state: string;
  url: string | null;
};

export type PricePoint = {
  observed_date: string;
  lowest_price: number | null;
  median_price: number | null;
  average_price: number | null;
  highest_price: number | null;
  listing_count: number | null;
};

export type CurvePoint = {
  buy_date: string;
  days_until_event: number;
  predicted_price: number;
};

export type Forecast =
  | {
      status: "ok";
      curve: CurvePoint[];
      best_buy_date: string;
      best_predicted_price: number;
      today_predicted_price: number;
      expected_savings: number;
      recommendation: "wait" | "buy_now";
      reasoning: string;
      caveat: string;
    }
  | { status: "collecting"; message: string; observed_days: number };

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export const api = {
  searchVenues: (q: string, state: string) =>
    get<Venue[]>(`/api/venues?q=${encodeURIComponent(q)}&state=${encodeURIComponent(state)}`),
  venueEvents: (venueId: string) => get<VenueEvent[]>(`/api/venues/${venueId}/events`),
  eventDetail: (eventId: string) => get<EventDetail>(`/api/events/${eventId}`),
  eventHistory: (eventId: string) => get<PricePoint[]>(`/api/events/${eventId}/history`),
  eventForecast: (eventId: string) => get<Forecast>(`/api/events/${eventId}/forecast`),
};
