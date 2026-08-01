import { useState } from 'react';
import { Meta } from '../lib/api';
import { useClock } from '../lib/clock';
import { formatDate } from './Primitives';

/**
 * The simulated-clock control, permanently in the top bar.
 *
 * It is not a debug affordance. The catalogue's last event is mid-2026 and
 * real-world today is past it, so without an explicit clock the site would
 * have nothing upcoming to recommend on. Exposing it also makes the product's
 * central claim inspectable: move the clock back and watch the recommendation
 * and the forecast recompute against only what was knowable on that date.
 */
export function ClockControl({ meta }: { meta: Meta | null }) {
  const { asOf, setAsOf, reset, isDefault } = useClock();
  const [open, setOpen] = useState(false);

  const start = meta?.catalogue.catalogue_start ?? '2025-10-10';
  const end = meta?.catalogue.catalogue_end ?? '2026-07-24';

  const shift = (days: number) => {
    const next = new Date(`${asOf}T00:00:00`);
    next.setDate(next.getDate() + days);
    const clamped = next.toISOString().slice(0, 10);
    if (clamped >= start && clamped <= end) setAsOf(clamped);
  };

  return (
    <div style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen((v) => !v)}
        style={{ display: 'flex', alignItems: 'center', gap: 8 }}
        title="The site runs on a simulated clock. Click to move it."
      >
        <span
          style={{
            width: 6, height: 6, borderRadius: '50%',
            background: isDefault ? 'var(--status-good)' : 'var(--status-warn)',
          }}
        />
        <span className="tiny muted" style={{ textTransform: 'uppercase', letterSpacing: '.05em' }}>
          simulated today
        </span>
        <strong className="mono" style={{ fontSize: 13 }}>{formatDate(asOf)}</strong>
      </button>

      {open ? (
        <div
          className="card card-pad"
          style={{
            position: 'absolute', right: 0, top: 42, width: 330,
            zIndex: 100, boxShadow: 'var(--shadow)',
          }}
        >
          <h3 style={{ marginBottom: 6 }}>Simulated clock</h3>
          <p className="small secondary" style={{ margin: '0 0 12px' }}>
            {meta?.clock_note ??
              'Events before this date are history the model trains on; events after it are what the site recommends on.'}
          </p>

          <label className="field">
            <span>As-of date</span>
            <input
              type="date" value={asOf} min={start} max={end}
              onChange={(e) => e.target.value && setAsOf(e.target.value)}
            />
          </label>

          <div className="row" style={{ gap: 6, marginBottom: 10 }}>
            <button onClick={() => shift(-30)}>−30d</button>
            <button onClick={() => shift(-7)}>−7d</button>
            <button onClick={() => shift(7)}>+7d</button>
            <button onClick={() => shift(30)}>+30d</button>
            <span className="spacer" />
            <button onClick={reset} disabled={isDefault}>Reset</button>
          </div>

          {meta ? (
            <div className="tiny muted" style={{ borderTop: '1px solid var(--border)', paddingTop: 9 }}>
              {meta.catalogue.upcoming_events.toLocaleString()} upcoming events ·{' '}
              {meta.catalogue.venues} venues · {meta.catalogue.teams} teams ·{' '}
              {meta.catalogue.price_snapshots.toLocaleString()} price snapshots
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
