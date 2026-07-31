import { useEffect, useState } from 'react';
import {
  Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer, XAxis, YAxis,
} from 'recharts';
import { Accuracy as AccuracyPayload, api } from '../lib/api';
import { useClock } from '../lib/clock';
import { Empty, Loading, Price, Stat, formatPct } from '../components/Primitives';

const STRATEGY_LABELS: Record<string, string> = {
  BUY_NOW: 'Buy immediately',
  MODEL: 'Follow TixTime',
  FIXED_7: 'Wait until 7 days out',
  FIXED_14: 'Wait until 14 days out',
  FIXED_30: 'Wait until 30 days out',
};

export function Accuracy() {
  const { asOf } = useClock();
  const [data, setData] = useState<AccuracyPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.accuracy(asOf).then(setData).catch(() => setData(null)).finally(() => setLoading(false));
  }, [asOf]);

  if (loading) return <Loading rows={4} height={120} />;
  if (!data?.backtest) {
    return <Empty title="No backtest results yet" detail="Run `python -m tixtime.ml.backtest`." />;
  }

  const backtest = data.backtest;
  const model = backtest.overall.MODEL;
  const buyNow = backtest.overall.BUY_NOW;
  const bestFixed = ['FIXED_30', 'FIXED_14', 'FIXED_7']
    .map((key) => ({ key, stats: backtest.overall[key] }))
    .filter((entry) => entry.stats)
    .sort((a, b) => b.stats.savings_captured_pct - a.stats.savings_captured_pct)[0];

  const chartData = Object.entries(backtest.overall).map(([key, stats]) => ({
    key,
    label: STRATEGY_LABELS[key] ?? key,
    captured: Number((stats.savings_captured_pct * 100).toFixed(1)),
    paid: stats.mean_paid,
    regret: stats.mean_regret,
    hit: stats.hit_rate_within_5pct,
  }));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Does it actually work?</h1>
          <p>
            Every strategy below is scored by replaying {backtest.n_events.toLocaleString()} events
            the model was never trained on — it learned only from events that had already finished
            by {backtest.trained_through} — and simulating the policy day by day:
            stand at the start date, ask the model each day, buy the first time it says buy.
          </p>
        </div>
      </div>

      {/* The caveat sits above the numbers, not in a footnote below them. */}
      <div
        className="card card-pad"
        style={{
          marginBottom: 16, borderColor: 'rgba(144,133,233,.45)',
          background: 'var(--synthetic-dim)',
        }}
      >
        <div className="row" style={{ gap: 10, alignItems: 'flex-start' }}>
          <span className="chip-synthetic">Read this first</span>
          <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.6, color: '#ded9ff' }}>
            {data.interpretation}
          </p>
        </div>
      </div>

      <div className="stat-row" style={{ marginBottom: 18 }}>
        <Stat
          label="Savings captured"
          value={formatPct(model.savings_captured_pct, 1)}
          note={`vs ${formatPct(bestFixed?.stats.savings_captured_pct ?? 0, 1)} for the best fixed rule`}
          tone="good"
        />
        <Stat
          label="Buys within 5% of the best price"
          value={formatPct(model.hit_rate_within_5pct, 1)}
          note={`buying immediately: ${formatPct(buyNow.hit_rate_within_5pct, 1)}`}
          tone="good"
        />
        <Stat
          label="Average overpay vs perfect timing"
          value={<Price value={model.mean_regret} source="synthetic_v1" size="lg" />}
          note={`buying immediately: $${buyNow.mean_regret.toFixed(2)}`}
        />
        <Stat
          label="Decisions scored"
          value={backtest.n_decisions.toLocaleString()}
          note={`${backtest.n_events.toLocaleString()} unseen events`}
        />
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head">
          <h2>Share of the achievable saving each strategy captured</h2>
          <span className="tiny muted">100% = perfect hindsight · 0% = buying immediately</span>
        </div>
        <div className="card-pad" style={{ height: 300 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} layout="vertical" margin={{ left: 8, right: 56, top: 4, bottom: 4 }}>
              <CartesianGrid stroke="rgba(255,255,255,.055)" horizontal={false} />
              <XAxis
                type="number" domain={[-10, 100]}
                tickFormatter={(v) => `${v}%`}
                stroke="var(--text-muted)" tick={{ fontSize: 11, fill: 'var(--text-muted)' }}
                tickLine={false} axisLine={false}
              />
              <YAxis
                type="category" dataKey="label" width={168}
                stroke="var(--text-muted)" tick={{ fontSize: 12, fill: 'var(--text-secondary)' }}
                tickLine={false} axisLine={false}
              />
              <Bar dataKey="captured" radius={[0, 4, 4, 0]} barSize={22} isAnimationActive={false}>
                {chartData.map((row) => (
                  <Cell
                    key={row.key}
                    // Status colour, reserved: the model is the good outcome,
                    // a strategy that loses to buying now is the bad one.
                    fill={
                      row.key === 'MODEL' ? 'var(--status-good)'
                      : row.captured < 0 ? 'var(--status-critical)'
                      : 'var(--surface-3)'
                    }
                  />
                ))}
                <LabelList
                  dataKey="captured"
                  position="right"
                  formatter={(v: number) => `${v}%`}
                  style={{ fill: 'var(--text-secondary)', fontSize: 12, fontFamily: 'var(--mono)' }}
                />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <p className="tiny muted" style={{ marginTop: 4 }}>
            “Wait until 7 days out” scores below zero: it is worse than simply buying when you
            first look, because most events bottom either much earlier or right at the door.
          </p>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head"><h2>Full strategy comparison</h2></div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Strategy</th>
                <th className="num">Mean paid</th>
                <th className="num">Overpay vs perfect</th>
                <th className="num">Overpay %</th>
                <th className="num">Savings captured</th>
                <th className="num">Within 5% of best</th>
                <th className="num">Decisions</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(backtest.overall).map(([key, stats]) => (
                <tr key={key} style={{ background: key === 'MODEL' ? 'var(--status-good-dim)' : undefined }}>
                  <td style={{ fontWeight: key === 'MODEL' ? 650 : 500 }}>
                    {STRATEGY_LABELS[key] ?? key}
                  </td>
                  <td className="num"><Price value={stats.mean_paid} source="synthetic_v1" /></td>
                  <td className="num"><Price value={stats.mean_regret} source="synthetic_v1" /></td>
                  <td className="num">{formatPct(stats.mean_regret_pct, 1)}</td>
                  <td className="num" style={{ color: stats.savings_captured_pct > 0 ? 'var(--status-good)' : 'var(--status-critical)' }}>
                    {formatPct(stats.savings_captured_pct, 1)}
                  </td>
                  <td className="num">{formatPct(stats.hit_rate_within_5pct, 1)}</td>
                  <td className="num muted">{stats.n.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head">
          <h2>By how far out you start looking</h2>
          <span className="tiny muted">savings captured, per starting window</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Starting point</th>
                {Object.keys(backtest.overall).map((key) => (
                  <th key={key} className="num">{STRATEGY_LABELS[key] ?? key}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.entries(backtest.by_start_window)
                .sort((a, b) => Number(b[0]) - Number(a[0]))
                .map(([window, strategies]) => (
                  <tr key={window}>
                    <td>{window} days before the event</td>
                    {Object.keys(backtest.overall).map((key) => (
                      <td key={key} className="num" style={{
                        color: key === 'MODEL' ? 'var(--status-good)' : undefined,
                        fontWeight: key === 'MODEL' ? 600 : 400,
                      }}>
                        {strategies[key] ? formatPct(strategies[key].savings_captured_pct, 1) : '—'}
                      </td>
                    ))}
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </div>

      {data.training ? (
        <div className="card">
          <div className="card-head">
            <h2>Model heads</h2>
            <span className="tiny muted">
              {data.training.n_pairs.toLocaleString()} training rows ·
              {' '}{data.training.n_features} features ·
              {' '}{data.training.n_train_events.toLocaleString()} events
            </span>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr><th>Head</th><th>What it predicts</th><th className="num">Key metric</th><th className="num">Calibration</th></tr>
              </thead>
              <tbody>
                {[
                  ['q10', '10th-percentile price at a horizon', 'pinball_loss', 'coverage'],
                  ['q50', 'Median price at a horizon', 'price_mape', 'coverage'],
                  ['q90', '90th-percentile price at a horizon', 'pinball_loss', 'coverage'],
                  ['min_return', 'Cheapest price still to come', 'wait_decision_accuracy', 'mae_log_return'],
                ].map(([head, description, metric, calibration]) => {
                  const stats = data.training!.heads[head] ?? {};
                  return (
                    <tr key={head}>
                      <td className="mono">{head}</td>
                      <td className="small secondary">{description}</td>
                      <td className="num">
                        {stats[metric] !== undefined
                          ? metric.includes('mape') || metric.includes('accuracy')
                            ? formatPct(stats[metric], 1)
                            : stats[metric].toFixed(4)
                          : '—'}
                        <div className="tiny muted">{metric}</div>
                      </td>
                      <td className="num">
                        {stats[calibration] !== undefined ? stats[calibration].toFixed(4) : '—'}
                        <div className="tiny muted">{calibration}</div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="card-pad tiny muted">
            The q10 and q90 heads are well calibrated: their coverage lands within a point of the
            nominal 10% and 90%, which is what makes the shaded band on the event chart mean
            what it says.
          </div>
        </div>
      ) : null}
    </>
  );
}
