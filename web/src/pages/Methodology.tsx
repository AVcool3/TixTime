import { Meta } from '../lib/api';

export function Methodology({ meta }: { meta: Meta | null }) {
  return (
    <div style={{ maxWidth: '86ch' }}>
      <div className="page-head">
        <div>
          <h1>How TixTime works, and what it can’t tell you</h1>
          <p>
            The honest version. What is real, what is generated, and why the two are kept
            visibly apart everywhere in this app.
          </p>
        </div>
      </div>

      <Section title="What is real">
        <p>
          The catalogue. {meta ? `${meta.catalogue.events.toLocaleString()} events` : 'Every event'} across{' '}
          {meta?.catalogue.venues ?? 170} venues and {meta?.catalogue.teams ?? 124} teams comes from the
          SeatGeek export you supplied — real event names, real dates, real venues, real
          purchase links. Venue geography and team identity were recovered by parsing the
          <code> url</code> and <code> name</code> columns, because the export’s
          <code> performerIds</code>, <code> eventScore</code> and <code> popularityScore</code>
          {' '}columns are entirely redacted.
        </p>
      </Section>

      <Section title="What is simulated, and why">
        <p>
          Every price. The export has <strong>zero</strong> price data — all 7,118 rows have
          empty <code>lowestPrice</code>, <code>medianPrice</code>, <code>averagePrice</code> and
          {' '}<code>listingCount</code> fields, and each event appears exactly once, so there is no
          price <em>history</em> in it at all. This deployment also has no network route to any
          ticket API. Without a price series there is nothing to learn buy-timing from, so
          TixTime generates one.
        </p>
        <p>
          The generated series follows the shape the secondary-market literature describes:
          a decline from the opening listing level toward a trough, then a terminal phase whose
          direction depends on demand. Following Sweeting (2012), prices <em>declining</em> into
          game day is the majority regime — sellers hold an asset worth nothing once the game
          starts — with a late run-up as a real but minority outcome for genuinely hot events.
          About 70% of simulated events bottom in the final 72 hours; the rest bottom weeks out.
        </p>
      </Section>

      <Section title="What the accuracy numbers do and don’t mean">
        <p>
          <strong>Legitimate:</strong> the backtest scores events the model never saw, using a
          policy simulation rather than a single prediction, against baselines including perfect
          hindsight. It demonstrates the pipeline recovers timing structure it was not shown —
          the features, the leakage controls, the serving path and the decision rule all work
          end to end.
        </p>
        <p>
          <strong>Not legitimate:</strong> reading those figures as a claim about real ticket
          markets. A model trained on a simulator learns the simulator. The only way to know
          whether this predicts real prices is to connect a live collector and measure it.
        </p>
      </Section>

      <Section title="How the forecast is produced">
        <p>
          Each training row is an <em>(as-of date, horizon)</em> pair: features observed strictly
          at the as-of date, with the horizon as an ordinary model input, predicting the log
          return over that horizon. The forecast curve then sweeps the horizon — a question the
          model has training support for.
        </p>
        <p>
          The tempting alternative — train on <code>days_until_event</code> and sweep it at
          serving time — is invalid, because the rolling-price and inventory features are
          themselves functions of that quantity. Freezing them while sweeping asks the model
          about states that never occur in training, and gradient-boosted trees do not
          extrapolate; they just fall into whichever leaf the frozen features imply.
        </p>
      </Section>

      <Section title="Guards against fooling ourselves">
        <ul>
          <li>
            Training uses only events whose price paths <em>finished</em> before the clock date.
            An unfinished event yields a censored minimum, which biases the label toward “buy now”.
          </li>
          <li>
            The franchise demand priors that seed the simulator are excluded from the feature
            set; the feature builder raises an error if they reappear. Using them would train
            the model to invert its own data generator.
          </li>
          <li>
            History features are backward-looking only, and “price versus its own window median”
            uses an expanding median truncated at the as-of date — a full-window version would
            encode the future.
          </li>
          <li>
            Every train/test split is grouped by event. A random row split would put day 91 of a
            series in training and day 92 in test and report an accuracy that measures nothing.
          </li>
          <li>
            The buy decision is a dollar regression on “how much cheaper does it still get?”,
            not a near-minimum classifier — that label is true by construction near the event.
          </li>
        </ul>
      </Section>

      <Section title="The simulated clock">
        <p>
          The catalogue ends {meta?.catalogue.catalogue_end ?? 'mid-2026'}, which the real
          calendar has passed — only 59 of the 7,118 events are still in the future. A buy-timing
          product needs both sides of “now”, so TixTime runs on an explicit as-of date, shown and
          adjustable in the top bar. Events before it are history the model trains and backtests
          on; events after it are what the site recommends on. Moving the clock backward
          recomputes everything from only what was knowable on that date — the same mechanism the
          backtest uses.
        </p>
      </Section>

      <Section title="Seats, and the limit of the purchase link">
        <p>
          Seat tiers are a modelled construct, not observed sections: the export carries no
          section or listing data. Where SeatGeek reports that an event is general admission or
          has seat selection disabled, TixTime shows a single tier rather than inventing
          sections — that is {' '}
          {meta ? `${(meta.catalogue.events - 4822).toLocaleString()} of ${meta.catalogue.events.toLocaleString()}` : 'a large share of'} events.
        </p>
        <p>
          SeatGeek’s URL addresses an event, not a seat, so no honest “buy this exact seat”
          deep link exists. The event page therefore links to the event and tells you which
          section to filter to and what a good price looks like when you arrive.
        </p>
      </Section>

      <Section title="Connecting real prices">
        <p>
          <code>tixtime/pricing/collectors.py</code> defines the collector interface with
          working SeatGeek and Ticketmaster implementations. Set <code>SEATGEEK_CLIENT_ID</code>
          {' '}or <code>TICKETMASTER_API_KEY</code>, give the process network access, and real
          snapshots land in the same table tagged with the provider name instead of
          {' '}<code>synthetic_v1</code>. Every surface in this app reads that tag, so real data
          stops being labelled as simulated the moment it arrives.
        </p>
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card card-pad" style={{ marginBottom: 14 }}>
      <h2 style={{ marginBottom: 8 }}>{title}</h2>
      <div className="secondary" style={{ fontSize: 13.5, lineHeight: 1.65 }}>{children}</div>
    </div>
  );
}
