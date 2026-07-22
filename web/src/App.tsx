import { Link, Route, Routes } from "react-router-dom";
import VenueSearch from "./pages/VenueSearch";
import VenueDetail from "./pages/VenueDetail";
import EventDetail from "./pages/EventDetail";

export default function App() {
  return (
    <div className="shell">
      <header>
        <Link to="/" className="brand">
          Tix<span>Time</span>
        </Link>
        <span className="tagline">know when to buy</span>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<VenueSearch />} />
          <Route path="/venues/:venueId" element={<VenueDetail />} />
          <Route path="/events/:eventId" element={<EventDetail />} />
        </Routes>
      </main>
      <footer>
        TixTime recommends timing — it never automates purchases. Data: SeatGeek
        marketplace via Rebrowser.
      </footer>
    </div>
  );
}
