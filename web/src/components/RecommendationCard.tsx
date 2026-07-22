import type { Forecast } from "../api/client";

export default function RecommendationCard({ forecast }: { forecast: Forecast }) {
  if (forecast.status === "collecting") {
    return (
      <div className="rec">
        <div className="verdict">STILL COLLECTING DATA</div>
        <div className="reasoning">
          {forecast.message} ({forecast.observed_days} day
          {forecast.observed_days === 1 ? "" : "s"} observed so far)
        </div>
      </div>
    );
  }

  const wait = forecast.recommendation === "wait";
  return (
    <div className={`rec ${forecast.recommendation}`}>
      <div className="verdict">{wait ? "WAIT" : "BUY NOW"}</div>
      <div className="savings">
        {wait ? (
          <>
            Best window around <strong>{forecast.best_buy_date}</strong> — expected
            savings <strong>${forecast.expected_savings.toFixed(0)}</strong>
          </>
        ) : (
          <>No meaningful drop expected — today is as good as it gets.</>
        )}
      </div>
      <div className="reasoning">{forecast.reasoning}</div>
      <div className="caveat">{forecast.caveat}</div>
    </div>
  );
}
