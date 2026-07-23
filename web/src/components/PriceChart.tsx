import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Forecast, PricePoint } from "../api/client";

type Row = { date: string; observed?: number; predicted?: number };

export default function PriceChart({
  history,
  forecast,
}: {
  history: PricePoint[];
  forecast: Forecast | undefined;
}) {
  const rows = new Map<string, Row>();
  for (const p of history) {
    const date = p.observed_date.slice(0, 10);
    if (p.median_price != null) rows.set(date, { date, observed: p.median_price });
  }
  if (forecast?.status === "ok") {
    for (const c of forecast.curve) {
      const row = rows.get(c.buy_date) ?? { date: c.buy_date };
      row.predicted = c.predicted_price;
      rows.set(c.buy_date, row);
    }
  }
  const data = [...rows.values()].sort((a, b) => a.date.localeCompare(b.date));
  if (data.length === 0) return null;

  return (
    <div className="chart-panel">
      <div className="legend">
        <span>
          <span className="k" style={{ background: "#4fc3f7" }} /> observed median
        </span>
        <span>
          <span className="k" style={{ background: "#fbbf24" }} /> forecast
        </span>
        {forecast?.status === "ok" && (
          <span>
            <span className="k" style={{ background: "#34d399" }} /> best buy date
          </span>
        )}
      </div>
      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
          <CartesianGrid stroke="#2a303a" strokeDasharray="3 3" />
          <XAxis dataKey="date" stroke="#9aa2ad" fontSize={12} minTickGap={40} />
          <YAxis
            stroke="#9aa2ad"
            fontSize={12}
            tickFormatter={(v: number) => `$${v}`}
            domain={["auto", "auto"]}
          />
          <Tooltip
            contentStyle={{ background: "#191d24", border: "1px solid #2a303a" }}
            formatter={(v: number) => `$${v.toFixed(2)}`}
          />
          <Line
            dataKey="observed"
            stroke="#4fc3f7"
            strokeWidth={2}
            dot={false}
            connectNulls
          />
          <Line
            dataKey="predicted"
            stroke="#fbbf24"
            strokeWidth={2}
            strokeDasharray="6 4"
            dot={false}
            connectNulls
          />
          {forecast?.status === "ok" && (
            <ReferenceLine
              x={forecast.best_buy_date}
              stroke="#34d399"
              strokeWidth={2}
              label={{ value: "buy", fill: "#34d399", fontSize: 12, position: "top" }}
            />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
