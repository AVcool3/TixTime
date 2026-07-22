import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import PriceChart from "../components/PriceChart";
import RecommendationCard from "../components/RecommendationCard";

export default function EventDetail() {
  const { eventId } = useParams<{ eventId: string }>();

  const detail = useQuery({
    queryKey: ["event", eventId],
    queryFn: () => api.eventDetail(eventId!),
    enabled: !!eventId,
  });
  const history = useQuery({
    queryKey: ["history", eventId],
    queryFn: () => api.eventHistory(eventId!),
    enabled: !!eventId,
  });
  const forecast = useQuery({
    queryKey: ["forecast", eventId],
    queryFn: () => api.eventForecast(eventId!),
    enabled: !!eventId,
  });

  const e = detail.data;
  return (
    <div>
      <p>
        {e && (
          <Link to={`/venues/${(e as any).venue_id}`} className="muted">
            ← back to venue
          </Link>
        )}
      </p>
      {e && (
        <>
          <h1>{e.name}</h1>
          <p className="muted">
            {e.venue_name} · {e.venue_city}, {e.venue_state} ·{" "}
            {new Date(e.datetime_utc).toLocaleDateString(undefined, {
              weekday: "long", month: "long", day: "numeric", year: "numeric",
            })}
          </p>
        </>
      )}

      {forecast.data && <RecommendationCard forecast={forecast.data} />}
      {forecast.isError && (
        <p className="muted">Forecast unavailable: {String(forecast.error)}</p>
      )}

      <h2>Price history &amp; forecast</h2>
      {history.data && (
        <PriceChart history={history.data} forecast={forecast.data} />
      )}
      {history.data?.length === 0 && (
        <p className="muted">No price observations yet for this event.</p>
      )}

      {e?.url && (
        <p style={{ marginTop: 20 }}>
          <a href={e.url} target="_blank" rel="noreferrer" style={{ color: "#4fc3f7" }}>
            View listings on SeatGeek →
          </a>{" "}
          <span className="muted">(TixTime never buys for you — it tells you when.)</span>
        </p>
      )}
    </div>
  );
}
