import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useDebounce } from "../useDebounce";

const STATES = [
  "", "AL","AK","AZ","AR","CA","CO","CT","DC","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA",
  "ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA",
  "RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY",
];

export default function VenueSearch() {
  const [q, setQ] = useState("");
  const [state, setState] = useState("");
  const debouncedQ = useDebounce(q, 300);

  const { data: venues, isLoading } = useQuery({
    queryKey: ["venues", debouncedQ, state],
    queryFn: () => api.searchVenues(debouncedQ, state),
  });

  return (
    <div>
      <h1>Find a venue</h1>
      <p className="muted">
        Pick any concert venue in the country — we track its shows and tell you
        the best time to buy.
      </p>
      <div className="searchbar">
        <input
          placeholder="Search venues or cities… (e.g. Red Rocks)"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          autoFocus
        />
        <select value={state} onChange={(e) => setState(e.target.value)}>
          {STATES.map((s) => (
            <option key={s} value={s}>
              {s || "All states"}
            </option>
          ))}
        </select>
      </div>

      {isLoading && <p className="muted">Searching…</p>}
      <div className="cards">
        {venues?.map((v) => (
          <Link key={v.venue_id} to={`/venues/${v.venue_id}`} className="card">
            <div className="title">{v.name}</div>
            <div className="sub">
              {v.city}, {v.state}
              {v.capacity ? ` · cap ${v.capacity.toLocaleString()}` : ""}
            </div>
          </Link>
        ))}
      </div>
      {venues?.length === 0 && <p className="muted">No venues match.</p>}
    </div>
  );
}
