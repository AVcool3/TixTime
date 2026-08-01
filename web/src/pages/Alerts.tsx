import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertEvent, AlertRule, api } from '../lib/api';
import { useClock } from '../lib/clock';
import { Empty, Price, Stat, formatDate } from '../components/Primitives';

export function Alerts() {
  const { asOf } = useClock();
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [alerts, setAlerts] = useState<AlertEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [lastRun, setLastRun] = useState<number | null>(null);

  const refresh = useCallback(() => {
    api.alertRules().then((r) => setRules(r.rules)).catch(() => setRules([]));
    api.alerts().then((r) => setAlerts(r.alerts)).catch(() => setAlerts([]));
  }, []);

  useEffect(refresh, [refresh, asOf]);

  const evaluate = async () => {
    setRunning(true);
    try {
      const response = await api.evaluateAlerts(asOf);
      setLastRun(response.fired.length);
      refresh();
    } finally {
      setRunning(false);
    }
  };

  const activeRules = rules.filter((r) => r.active);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Alerts</h1>
          <p>
            Rules are evaluated against the simulated clock. The in-app inbox below is the
            system of record — every fired alert is written here regardless of channel, and
            each one carries the true delivery status of its transport rather than implying
            a message went out.
          </p>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button className="btn-primary" onClick={evaluate} disabled={running || !activeRules.length}>
            {running ? 'Evaluating…' : `Run evaluation for ${formatDate(asOf)}`}
          </button>
        </div>
      </div>

      {lastRun !== null ? (
        <div className="card card-pad small" style={{ marginBottom: 14, borderColor: 'var(--status-info)' }}>
          Evaluation complete — {lastRun} alert{lastRun === 1 ? '' : 's'} fired for {formatDate(asOf)}.
          {lastRun === 0 ? ' No rule matched on this simulated day; move the clock forward and run again.' : ''}
        </div>
      ) : null}

      <div className="stat-row" style={{ marginBottom: 16 }}>
        <Stat label="Active rules" value={activeRules.length} />
        <Stat label="Alerts fired" value={alerts.length} />
        <Stat label="Unread" value={alerts.filter((a) => !a.acknowledged).length} tone="warn" />
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head"><h2>Rules</h2></div>
        {activeRules.length === 0 ? (
          <div className="card-pad">
            <Empty
              title="No alert rules yet"
              detail="Open any event and choose “Alert me” to watch it."
            />
          </div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Event</th><th>Trigger</th><th>Tier</th>
                  <th className="num">Threshold</th><th>Channel</th><th>Created</th><th />
                </tr>
              </thead>
              <tbody>
                {activeRules.map((rule) => (
                  <tr key={rule.rule_id}>
                    <td>
                      <Link to={`/events/${rule.event_id}`}>{rule.event_name}</Link>
                      <div className="tiny muted">{formatDate(rule.event_date)}</div>
                    </td>
                    <td className="small">{describeRule(rule.rule_type)}</td>
                    <td className="small secondary">{rule.tier_key ?? 'any'}</td>
                    <td className="num">
                      {rule.threshold === null ? '—'
                        : rule.rule_type === 'target_price'
                          ? <Price value={rule.threshold} source="synthetic_v1" size="sm" />
                          : `${rule.threshold}%`}
                    </td>
                    <td><span className="chip">{rule.channel}</span></td>
                    <td className="small muted">{formatDate(rule.created_at)}</td>
                    <td>
                      <button
                        className="tiny"
                        onClick={() => api.deleteAlertRule(rule.rule_id).then(refresh)}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Inbox</h2>
          <span className="tiny muted">newest first</span>
        </div>
        {alerts.length === 0 ? (
          <div className="card-pad">
            <Empty
              title="Nothing has fired yet"
              detail="Create a rule, then run an evaluation for the current simulated day."
            />
          </div>
        ) : (
          <div className="col" style={{ padding: 12, gap: 10 }}>
            {alerts.map((alert) => <AlertRow key={alert.alert_id} alert={alert} onAck={refresh} />)}
          </div>
        )}
      </div>
    </>
  );
}

function AlertRow({ alert, onAck }: { alert: AlertEvent; onAck: () => void }) {
  const status = parseStatus(alert.body);
  return (
    <div
      className="card card-pad"
      style={{
        borderColor: alert.acknowledged ? 'var(--border)' : 'var(--border-strong)',
        opacity: alert.acknowledged ? 0.65 : 1,
        background: 'var(--surface-1)',
      }}
    >
      <div className="row-wrap" style={{ gap: 8, marginBottom: 5 }}>
        <strong style={{ flex: 1 }}>{alert.headline}</strong>
        <DeliveryChip channel={status.channel ?? alert.channel ?? 'inapp'} status={status.status} />
        <span className="tiny muted">{formatDate(alert.as_of_date)}</span>
      </div>
      <div className="small secondary">{status.message}</div>
      <div className="row-wrap" style={{ gap: 8, marginTop: 10 }}>
        <Link className="btn tiny" to={`/events/${alert.event_id}`}>View event</Link>
        <a className="btn tiny" href={alert.purchase_url} target="_blank" rel="noopener noreferrer">
          Buy on SeatGeek ↗
        </a>
        {alert.price !== null ? (
          <span className="chip">at <Price value={alert.price} source="synthetic_v1" size="sm" /></span>
        ) : null}
        <span className="spacer" />
        {!alert.acknowledged ? (
          <button className="tiny" onClick={() => api.ackAlert(alert.alert_id).then(onAck)}>
            Mark read
          </button>
        ) : <span className="tiny muted">read</span>}
      </div>
    </div>
  );
}

/** The engine appends "[channel: status -- detail]" to the body. Surfacing it
 * verbatim is the point: an unconfigured transport must look unconfigured. */
function parseStatus(body: string): { message: string; channel: string | null; status: string } {
  const match = body.match(/^(.*)\s\[([a-z]+):\s([a-z]+)\s--\s(.*)\]$/s);
  if (!match) return { message: body, channel: null, status: 'delivered' };
  return { message: `${match[1]} ${match[4]}`, channel: match[2], status: match[3] };
}

function DeliveryChip({ channel, status }: { channel: string; status: string }) {
  const tone =
    status === 'delivered' ? { bg: 'var(--status-good-dim)', fg: '#7ee0b8', border: 'rgba(25,158,112,.45)' }
    : status === 'failed' ? { bg: 'rgba(230,103,103,.15)', fg: '#f0a0a0', border: 'rgba(230,103,103,.45)' }
    : { bg: 'var(--surface-2)', fg: 'var(--text-muted)', border: 'var(--border)' };
  return (
    <span
      className="chip"
      style={{ background: tone.bg, color: tone.fg, borderColor: tone.border, fontWeight: 600 }}
      title={`${channel}: ${status}`}
    >
      {channel}: {status}
    </span>
  );
}

function describeRule(type: string) {
  return {
    model_buy: 'Model switches to BUY',
    target_price: 'Price at or below target',
    drop_pct: 'Drop from window high',
  }[type] ?? type;
}
