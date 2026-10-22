"""The stands of an airport as the gate map shows them, and a gate asked for (Airport Ops, the gate map): approved
when it's free, fits the aircraft and isn't one ATC already knows is taken; then it's the gate ground sends the
arrival to (a new taxi route if ground has given one already). LocalTC's own gate choice, not where GSX parks the
aircraft: GSX positions it at the stand it's told to, which is the pilot's to choose there.
"""

from localtc.atc_core.airport import gates as stands
from localtc.atc_core.airport.aircraft_size import fits_stand, wingspan_m


class GateRequestsMixin:
    """``AtcEngine``'s gate map and gate requests."""

    def gate_map(self, icao: str | None) -> dict | None:
        """{icao, assigned, stands: [{index, name, word, lat, lon, radius_m, kind, international, real, occupied,
        taken, fits, airline}]} for the gate map; None without the airport."""
        geo = self.geometry(icao) if icao else None
        if geo is None:
            return None
        st = self.state
        real = self._real_gates(geo.icao)
        span = wingspan_m(st.flight.aircraft_type or self._aircraft_title)
        traffic = list(self._traffic.values())
        arriving = icao == st.flight.destination
        assigned = st.assignments.gate_index if arriving and self.gate_given() else None
        out = []
        for g in stands.gates(geo, real):
            near = min(((t, abs(t.lat - g.spot.lat) + abs(t.lon - g.spot.lon)) for t in traffic if t.on_ground),
                       key=lambda x: x[1], default=(None, 1.0))
            parked = near[0] if near[0] is not None and near[1] < 0.0004 else None
            out.append({"index": g.index, "name": g.display, "label": g.label, "word": g.word, "lat": g.spot.lat,
                        "lon": g.spot.lon, "radius_m": round(g.spot.radius_m, 1), "kind": g.spot.kind,
                        "international": g.international, "real": g.real,
                        "occupied": stands.occupied(g, geo, traffic), "taken": g.index in self._gates_taken,
                        "fits": fits_stand(g.spot.radius_m, span),
                        "airline": (parked.atc_id or "")[:3].upper() if parked is not None and parked.atc_id else ""})
        return {"icao": geo.icao, "assigned": assigned, "assigned_name": self.gate_given() if arriving else
                st.assignments.departure_gate, "span_m": span, "stands": out,
                "operator": self._callsign().ident[:3] if self._callsign().is_airline else ""}

    def request_gate(self, icao: str | None, label: str) -> tuple[bool, str]:
        """A gate asked for at the destination: (approved, why). Approved, it's the one ground sends the flight to."""
        st = self.state
        if not icao or icao != st.flight.destination:
            return False, "Gates are given for the arrival: this is the departure airport (your stand is where you are)."
        geo = self.geometry(icao)
        if geo is None:
            return False, f"{icao}'s stands aren't known yet."
        want = label.upper().replace(" ", "").removeprefix("GATE").removeprefix("STAND").removeprefix("PARKING")
        gate = next((g for g in stands.gates(geo, self._real_gates(geo.icao))
                     if g.label.upper().replace(" ", "") == want or g.display.upper().replace(" ", "") == label.upper().replace(" ", "")),
                    None)
        if gate is None:
            return False, f"There's no {label} at {icao}."
        if gate.index in self._gates_taken or stands.occupied(gate, geo, self._traffic.values()):
            return False, f"{gate.display} is occupied."
        span = wingspan_m(st.flight.aircraft_type or self._aircraft_title)
        if not fits_stand(gate.spot.radius_m, span):
            return False, f"{gate.display} is too small for your aircraft."
        if st.assignments.gate_index == gate.index and self.gate_given():
            return True, f"{gate.display} is already yours."
        self._gate_asked = True
        self._assign(gate=gate.display, gate_index=gate.index)
        if "taxi_in" in st.clearances:
            self._gate_changed = True  # ground gives the new route (``_monitor``)
        return True, f"{gate.display} approved" + (", ground will give you the new route." if "taxi_in" in st.clearances else ".")
