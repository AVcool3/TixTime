import { useEffect, useState } from 'react';
import { NavLink, Route, Routes } from 'react-router-dom';
import { api, Meta } from './lib/api';
import { SyntheticRibbon } from './components/Primitives';
import { ClockControl } from './components/ClockControl';
import { ClockContext, useClockState } from './lib/clock';
import { Explore } from './pages/Explore';
import { DealRadar } from './pages/DealRadar';
import { EventPage } from './pages/EventPage';
import { Alerts } from './pages/Alerts';
import { Accuracy } from './pages/Accuracy';
import { Methodology } from './pages/Methodology';

export default function App() {
  const clock = useClockState();
  const [meta, setMeta] = useState<Meta | null>(null);

  useEffect(() => {
    api.meta(clock.asOf).then(setMeta).catch(() => setMeta(null));
  }, [clock.asOf]);

  return (
    <ClockContext.Provider value={clock}>
      <div className="app">
        <SyntheticRibbon disclaimer={meta?.disclaimer} />

        <header className="topbar">
          <div className="brand">
            <span className="brand-dot" />
            TixTime
            <span className="brand-sub">buy-timing engine</span>
          </div>

          <nav className="nav">
            <NavLink to="/" end className={({ isActive }) => (isActive ? 'active' : '')}>Explore</NavLink>
            <NavLink to="/deals" className={({ isActive }) => (isActive ? 'active' : '')}>Deal Radar</NavLink>
            <NavLink to="/alerts" className={({ isActive }) => (isActive ? 'active' : '')}>Alerts</NavLink>
            <NavLink to="/accuracy" className={({ isActive }) => (isActive ? 'active' : '')}>Accuracy</NavLink>
            <NavLink to="/methodology" className={({ isActive }) => (isActive ? 'active' : '')}>Method</NavLink>
          </nav>

          <div className="topbar-right">
            <ClockControl meta={meta} />
          </div>
        </header>

        <main className="content">
          <Routes>
            <Route path="/" element={<Explore meta={meta} />} />
            <Route path="/deals" element={<DealRadar />} />
            <Route path="/events/:eventId" element={<EventPage />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/accuracy" element={<Accuracy />} />
            <Route path="/methodology" element={<Methodology meta={meta} />} />
          </Routes>
        </main>
      </div>
    </ClockContext.Provider>
  );
}
