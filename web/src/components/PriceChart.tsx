import { useMemo } from 'react';
import {
  Area, CartesianGrid, ComposedChart, Line, ReferenceArea, ReferenceDot,
  ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { ForecastPoint, HistoryPoint, Recommendation } from '../lib/api';
import { formatDate } from './Primitives';

/**
 * The hero chart: realised price history, the forecast ahead, and the buy
 * window, on one date axis.
 *
 * ENCODING DISCIPLINE — the reason this reads in a second rather than as five
 * overlapping lines:
 *
 *   hue          carries SEAT TIER and nothing else. Past vs future never
 *                changes hue, so the two dimensions cannot compete.
 *   line style   solid = observed, dashed = forecast.
 *   band         present only on the future side. The band's ABSENCE before
 *                today is itself the past/future cue, so no extra ink is
 *                needed to say which is which.
 *   surface      a faint tint change behind the future half, reinforcing the
 *                boundary without a hard visual wall.
 *   green area   the recommended buy window, the one status colour here.
 *
 * Layer order matters and is bottom-to-top: tint, buy window, outer band,
 * inner band, history line, forecast line, markers.
 */

export interface PriceChartProps {
  history: HistoryPoint[];
  forecast: ForecastPoint[];
  recommendation: Recommendation | null;
  color: string;
  tierLabel: string;
  source: string;
  height?: number;
}

interface Row {
  t: number;
  date: string;
  actual?: number;
  listings?: number;
  q50?: number;
  band?: [number, number];
  inner?: [number, number];
}

const dayMs = 86_400_000;

export function PriceChart({
  history, forecast, recommendation, color, tierLabel, source, height = 380,
}: PriceChartProps) {
  const { rows, todayT, buyWindow, minPoint, domain } = useMemo(() => {
    const byT = new Map<number, Row>();

    const put = (dateStr: string, patch: Partial<Row>) => {
      const t = new Date(`${dateStr.slice(0, 10)}T00:00:00`).getTime();
      const existing = byT.get(t) ?? { t, date: dateStr.slice(0, 10) };
      byT.set(t, { ...existing, ...patch });
    };

    history.forEach((h) => put(h.date, { actual: h.get_in_price, listings: h.listing_count }));

    const lastHistory = history.length ? history[history.length - 1] : null;
    const today = lastHistory
      ? new Date(`${lastHistory.date.slice(0, 10)}T00:00:00`).getTime()
      : null;

    // Anchor the forecast to the last observed price so the dashed line starts
    // exactly where the solid line ends -- otherwise the curve appears to jump
    // at the boundary, which reads as a data error.
    if (lastHistory) {
      put(lastHistory.date, {
        q50: lastHistory.get_in_price,
        band: [lastHistory.get_in_price, lastHistory.get_in_price],
        inner: [lastHistory.get_in_price, lastHistory.get_in_price],
      });
    }

    forecast.forEach((f) => {
      const mid = (f.q10 + f.q90) / 2;
      const innerLow = f.q50 - (f.q50 - f.q10) * 0.45;
      const innerHigh = f.q50 + (f.q90 - f.q50) * 0.45;
      put(f.date, { q50: f.q50, band: [f.q10, f.q90], inner: [innerLow, innerHigh] });
      void mid;
    });

    const rows = Array.from(byT.values()).sort((a, b) => a.t - b.t);

    // The recommended buy window: a few days either side of the projected
    // trough, because naming a single magic day overstates the precision.
    let buyWindow: [number, number] | null = null;
    let minPoint: { t: number; value: number } | null = null;
    if (recommendation?.forecast_min_date && recommendation.action === 'WAIT') {
      const centre = new Date(`${recommendation.forecast_min_date.slice(0, 10)}T00:00:00`).getTime();
      buyWindow = [centre - 3 * dayMs, centre + 3 * dayMs];
      const at = rows.find((r) => r.t === centre);
      minPoint = { t: centre, value: at?.q50 ?? recommendation.forecast_min ?? 0 };
    }

    const values: number[] = [];
    rows.forEach((r) => {
      if (r.actual !== undefined) values.push(r.actual);
      if (r.band) values.push(r.band[0], r.band[1]);
    });
    const low = values.length ? Math.min(...values) : 0;
    const high = values.length ? Math.max(...values) : 1;
    const pad = (high - low) * 0.12 || 1;

    return {
      rows,
      todayT: today,
      buyWindow,
      minPoint,
      domain: [Math.max(0, low - pad), high + pad] as [number, number],
    };
  }, [history, forecast, recommendation]);

  if (!rows.length) return null;

  const firstT = rows[0].t;
  const lastT = rows[rows.length - 1].t;
  const gridColor = 'rgba(255,255,255,0.055)';

  return (
    <div style={{ width: '100%', height }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ top: 14, right: 20, bottom: 6, left: 4 }}>
          <defs>
            <linearGradient id="futureTint" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#ffffff" stopOpacity={0.045} />
              <stop offset="100%" stopColor="#ffffff" stopOpacity={0.012} />
            </linearGradient>
            {/* Hatched, not solid: texture keeps the band legible for CVD
                readers and in forced-colors / print, where a translucent
                fill can vanish entirely. */}
            <pattern id="bandHatch" width="6" height="6" patternTransform="rotate(45)" patternUnits="userSpaceOnUse">
              <rect width="6" height="6" fill={color} fillOpacity={0.07} />
              <line x1="0" y1="0" x2="0" y2="6" stroke={color} strokeWidth={1.6} strokeOpacity={0.2} />
            </pattern>
          </defs>

          <CartesianGrid stroke={gridColor} vertical={false} />

          {/* 1. future surface tint */}
          {todayT !== null && lastT > todayT ? (
            <ReferenceArea x1={todayT} x2={lastT} fill="url(#futureTint)" stroke="none" />
          ) : null}

          {/* 2. recommended buy window */}
          {buyWindow ? (
            <ReferenceArea
              x1={Math.max(buyWindow[0], firstT)}
              x2={Math.min(buyWindow[1], lastT)}
              fill="var(--status-good)"
              fillOpacity={0.14}
              stroke="var(--status-good)"
              strokeOpacity={0.35}
              strokeDasharray="3 3"
            />
          ) : null}

          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={[firstT, lastT]}
            tickFormatter={(t) => formatDate(new Date(t).toISOString(), { month: 'short', day: 'numeric' })}
            stroke="var(--text-muted)"
            tick={{ fontSize: 11, fill: 'var(--text-muted)' }}
            tickLine={false}
            axisLine={{ stroke: gridColor }}
            minTickGap={44}
          />
          <YAxis
            domain={domain}
            tickFormatter={(v: number) => `$${v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v.toFixed(0)}`}
            stroke="var(--text-muted)"
            tick={{ fontSize: 11, fill: 'var(--text-muted)' }}
            tickLine={false}
            axisLine={false}
            width={54}
          />

          {/* 3+4. nested uncertainty bands, future side only */}
          <Area
            dataKey="band" type="monotone" fill="url(#bandHatch)" stroke="none"
            isAnimationActive={false} connectNulls
          />
          <Area
            dataKey="inner" type="monotone" fill={color} fillOpacity={0.17} stroke="none"
            isAnimationActive={false} connectNulls
          />

          {/* 5. observed history -- solid */}
          <Line
            dataKey="actual" type="monotone" stroke={color} strokeWidth={2}
            dot={false} isAnimationActive={false} name="Observed" connectNulls={false}
          />
          {/* 6. forecast median -- dashed, same hue */}
          <Line
            dataKey="q50" type="monotone" stroke={color} strokeWidth={2}
            strokeDasharray="5 4" dot={false} isAnimationActive={false}
            name="Forecast" connectNulls={false}
          />

          {/* 7. markers */}
          {todayT !== null ? (
            <ReferenceLine
              x={todayT}
              stroke="var(--text-secondary)"
              strokeWidth={1}
              strokeDasharray="2 3"
              label={{ value: 'today', position: 'insideTopRight', fill: 'var(--text-muted)', fontSize: 10 }}
            />
          ) : null}
          {minPoint ? (
            <ReferenceDot
              x={minPoint.t} y={minPoint.value} r={5}
              fill="var(--status-good)" stroke="var(--surface-raised)" strokeWidth={2}
            />
          ) : null}

          <Tooltip
            isAnimationActive={false}
            cursor={{ stroke: 'var(--text-muted)', strokeWidth: 1, strokeDasharray: '3 3' }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              const row = payload[0].payload as Row;
              const isFuture = todayT !== null && row.t > todayT;
              return (
                <div
                  style={{
                    background: 'var(--surface-2)', border: '1px solid var(--border-strong)',
                    borderRadius: 8, padding: '9px 11px', fontSize: 12, boxShadow: 'var(--shadow)',
                  }}
                >
                  <div style={{ color: 'var(--text-muted)', marginBottom: 5, fontSize: 11 }}>
                    {formatDate(new Date(label as number).toISOString())}
                    <span style={{ marginLeft: 6, color: isFuture ? 'var(--status-warn)' : 'var(--text-secondary)' }}>
                      {isFuture ? 'forecast' : 'observed'}
                    </span>
                  </div>
                  <div className="row" style={{ gap: 6 }}>
                    <span style={{ width: 8, height: 8, borderRadius: 2, background: color }} />
                    <span style={{ color: 'var(--text-secondary)' }}>{tierLabel}</span>
                    <strong className="mono" style={{ marginLeft: 'auto' }}>
                      ${(row.actual ?? row.q50 ?? 0).toFixed(2)}
                    </strong>
                  </div>
                  {isFuture && row.band ? (
                    <div className="mono" style={{ color: 'var(--text-muted)', fontSize: 11, marginTop: 3 }}>
                      80% range ${row.band[0].toFixed(2)} – ${row.band[1].toFixed(2)}
                    </div>
                  ) : null}
                  {!isFuture && row.listings !== undefined ? (
                    <div style={{ color: 'var(--text-muted)', fontSize: 11, marginTop: 3 }}>
                      {row.listings.toLocaleString()} listings
                    </div>
                  ) : null}
                  <div style={{ color: 'var(--synthetic)', fontSize: 10, marginTop: 5 }}>
                    {source === 'synthetic_v1' ? 'simulated price' : source}
                  </div>
                </div>
              );
            }}
          />
        </ComposedChart>
      </ResponsiveContainer>

      <ChartLegend color={color} tierLabel={tierLabel} hasBuyWindow={Boolean(buyWindow)} />
    </div>
  );
}

/** Identity is never colour-alone: the legend names every encoding, including
 * the line styles and the band, and is always present. */
function ChartLegend({
  color, tierLabel, hasBuyWindow,
}: { color: string; tierLabel: string; hasBuyWindow: boolean }) {
  return (
    <div className="row-wrap tiny" style={{ gap: 16, padding: '10px 4px 0', color: 'var(--text-muted)' }}>
      <span className="row" style={{ gap: 6 }}>
        <svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" stroke={color} strokeWidth={2} /></svg>
        {tierLabel} — observed
      </span>
      <span className="row" style={{ gap: 6 }}>
        <svg width="22" height="8">
          <line x1="0" y1="4" x2="22" y2="4" stroke={color} strokeWidth={2} strokeDasharray="5 4" />
        </svg>
        forecast
      </span>
      <span className="row" style={{ gap: 6 }}>
        <svg width="22" height="10">
          <rect width="22" height="10" fill={color} fillOpacity={0.17} />
          <rect width="22" height="10" fill="none" stroke={color} strokeOpacity={0.35} />
        </svg>
        80% range
      </span>
      {hasBuyWindow ? (
        <span className="row" style={{ gap: 6 }}>
          <svg width="22" height="10">
            <rect width="22" height="10" fill="var(--status-good)" fillOpacity={0.16}
                  stroke="var(--status-good)" strokeOpacity={0.4} strokeDasharray="3 3" />
          </svg>
          suggested buy window
        </span>
      ) : null}
      <span className="chip-synthetic" style={{ marginLeft: 'auto' }}>simulated market</span>
    </div>
  );
}
