import { useState } from 'react';
import { TierRow, api } from '../lib/api';

export function AlertDialog({
  eventId, eventName, tiers, defaultTier, onClose,
}: {
  eventId: number; eventName: string; tiers: TierRow[];
  defaultTier: string | null; onClose: () => void;
}) {
  const [ruleType, setRuleType] = useState('model_buy');
  const [tierKey, setTierKey] = useState(defaultTier ?? '');
  const [threshold, setThreshold] = useState('');
  const [channel, setChannel] = useState('inapp');
  const [destination, setDestination] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const needsThreshold = ruleType !== 'model_buy';

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.createAlertRule({
        event_id: eventId,
        rule_type: ruleType,
        tier_key: tierKey || null,
        threshold: needsThreshold ? Number(threshold) : null,
        channel,
        destination: destination || null,
        label: eventName,
      });
      setDone(true);
      setTimeout(onClose, 900);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create the alert.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,.62)',
        display: 'grid', placeItems: 'center', zIndex: 200, padding: 20,
      }}
    >
      <div
        className="card card-pad"
        onClick={(e) => e.stopPropagation()}
        style={{ width: 440, maxWidth: '100%', boxShadow: 'var(--shadow)' }}
      >
        <div className="row" style={{ marginBottom: 4 }}>
          <h2 style={{ flex: 1 }}>Alert me</h2>
          <button onClick={onClose} style={{ padding: '2px 8px' }}>✕</button>
        </div>
        <p className="small muted" style={{ marginTop: 0 }}>{eventName}</p>

        <label className="field">
          <span>Trigger</span>
          <select value={ruleType} onChange={(e) => setRuleType(e.target.value)}>
            <option value="model_buy">When the model switches to BUY</option>
            <option value="target_price">When the price falls to a target</option>
            <option value="drop_pct">When it drops by a percentage</option>
          </select>
        </label>

        {tiers.length > 1 ? (
          <label className="field">
            <span>Seat tier</span>
            <select value={tierKey} onChange={(e) => setTierKey(e.target.value)}>
              <option value="">Any tier</option>
              {tiers.map((tier) => (
                <option key={tier.tier_key} value={tier.tier_key}>{tier.tier_label}</option>
              ))}
            </select>
          </label>
        ) : null}

        {needsThreshold ? (
          <label className="field">
            <span>{ruleType === 'target_price' ? 'Target price ($)' : 'Drop from window high (%)'}</span>
            <input
              type="number" min={0} value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
              placeholder={ruleType === 'target_price' ? '75' : '20'}
            />
          </label>
        ) : null}

        <label className="field">
          <span>Deliver via</span>
          <select value={channel} onChange={(e) => setChannel(e.target.value)}>
            <option value="inapp">In-app inbox</option>
            <option value="webhook">Webhook</option>
            <option value="email">Email</option>
          </select>
        </label>

        {channel !== 'inapp' ? (
          <label className="field">
            <span>{channel === 'webhook' ? 'Webhook URL' : 'Email address'}</span>
            <input
              value={destination}
              onChange={(e) => setDestination(e.target.value)}
              placeholder={channel === 'webhook' ? 'https://…' : 'you@example.com'}
            />
          </label>
        ) : null}

        {/* Say plainly what will and will not happen, rather than implying a
            message goes out when no transport is configured. */}
        <div className="tiny muted" style={{ marginBottom: 12, lineHeight: 1.5 }}>
          {channel === 'inapp'
            ? 'Alerts always land in the in-app inbox, which is the system of record.'
            : channel === 'webhook'
              ? 'The webhook is called for real when the rule fires. With no URL set, the alert is still recorded in the inbox and marked “unconfigured”.'
              : 'Email requires TIXTIME_SMTP_HOST on the server. Without it the alert is recorded in the inbox and marked “unconfigured” rather than silently dropped.'}
        </div>

        {error ? <div className="small" style={{ color: 'var(--status-critical)', marginBottom: 10 }}>{error}</div> : null}

        <div className="row" style={{ gap: 8 }}>
          <button className="btn-primary" onClick={submit} disabled={saving || done || (needsThreshold && !threshold)}>
            {done ? 'Created ✓' : saving ? 'Saving…' : 'Create alert'}
          </button>
          <button onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
