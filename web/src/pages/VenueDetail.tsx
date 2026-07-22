import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";

export default function VenueDetail() {
  const { venueId } = useParams<{ venueId: string }>();
  const { data: events, isLoading } = useQuery({
    queryKey: ["venue-events", venueId],
    queryFn: () => api.venueEvents(venueId!),
    enabled: !!venueId,
  });

  return (
    <div>
      <p>
        <Link to="/" className="muted">← all venues</Link>
      </p>
      <h1>Upcoming shows</h1>
      {isLoading && <p className="muted">Loading…</p>}
      <div className="cards">
        {events?.map((e) => (
          <Link key={e.event_id} to={`/events/${e.event_id}`} className="card">
            <div className="title">{e.name}</div>
            <div className="sub">
              {new Date(e.datetime_utc).toLocaleDateString(undefined, {
                weekday: "short", month: "short", day: "numeric", year: "numeric",
              })}
            </div>
            {e.lowest_price != null && (
              <div className="price">
                from ${Math.round(e.lowest_price)} · median $
                {Math.round(e.median_price ?? 0)} · {e.listing_count} listings
              </div>
            )}
          </Link>
        ))}
      </div>
      {events?.length === 0 && (
        <p className="muted">No upcoming events tracked for this venue yet.</p>
      )}
    </div>
  );
}
