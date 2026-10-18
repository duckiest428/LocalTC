"""The ATC tab's airport box en route: the flight's status (ui/controller.py)."""

def test_en_route_the_airport_box_is_the_flights_status():
    """At cruise, hours from the destination, its frequencies were no use: the flight's status instead (the airline and
    its logo, the way along, the time left), back to the airport near either end."""
    from types import SimpleNamespace

    from localtc.atc_core.facilities import Facility
    from localtc.sim_api import OwnshipState
    from localtc.ui.controller import AppController

    def geo(icao, lat, lon):
        return SimpleNamespace(airport=SimpleNamespace(icao=icao, lat=lat, lon=lon, name=f"{icao} Intl"))

    geos = {"KSEA": geo("KSEA", 47.45, -122.31), "KMCO": geo("KMCO", 28.43, -81.31)}
    own = OwnshipState(t=1.0, lat=39.0, lon=-105.0, alt_msl_ft=33000, alt_indicated_ft=33000, alt_agl_ft=27000,
                       altimeter_inhg=29.92, hdg_mag=110, hdg_true=118, ias_kt=280, gs_kt=480, vs_fpm=0, on_ground=False,
                       squawk="1621", xpdr_mode="alt", com1_mhz=133.5, com2_mhz=121.5, zulu_s=20 * 3600, fuel_lb=21000)
    engine = SimpleNamespace(state=SimpleNamespace(aircraft=own, flight=SimpleNamespace(aircraft_type="A21N")),
                             geometry=geos.get)
    snap = SimpleNamespace(origin="KSEA", destination="KMCO", phase="CRUISE", callsign="DAL328", cruise_ft=33000,
                           tuned=Facility("center", "Denver Center", 133.5))
    ctl = AppController.__new__(AppController)
    ctl.airports = {}
    s = ctl._en_route(engine, snap)
    assert s["flight"] == "DL 328" and s["airline"] == "Delta" and "pics.avs.io" in s["logo"] and s["aircraft"].startswith("Airbus")
    assert 0.3 < s["progress"] < 0.7 and s["altitude"] == "FL330" and s["with"] == "Denver Center 133.5"
    import msgspec

    near = msgspec.structs.replace(own, lat=28.9, lon=-82.0)
    engine.state.aircraft = near
    assert ctl._en_route(engine, snap) is None  # close to Orlando: its frequencies again
