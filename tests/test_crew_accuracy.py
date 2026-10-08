"""The copilot as a colleague, not a chatbot: it says what it knows and nothing it doesn't (the model's replies checked
against the aircraft), takes a report as a report, answers "where are we?" from the position, keeps quiet when told,
doesn't answer "check", and knows an aircraft it can't reach (the FSLabs Airbus)."""

import pytest
from test_crew import FakeBackend, own, started
from test_crew_monitor import FakeEngine, crew, fly, said, systems

from localtc.crew import monitor, places
from localtc.crew.model import CrewModel, unsupported_claim
from localtc.crew.pm import PilotMonitoring
from localtc.crew.profiles import for_aircraft, load_all
from localtc.sim_api import AircraftIdentity, AircraftSystems, CrewAction, IntercomHeard, SendSimEvent

PROFILES = load_all()


def model_says(answer: str, text: str, **own_kw) -> list[str]:
    pm = started()
    if own_kw:
        pm.observe(own(0.8, **own_kw))
    pm.model = CrewModel(FakeBackend(answer), mode="llm")
    return said(pm.observe(IntercomHeard(t=2.0, text=text)))


def reply(words: str) -> str:
    return '{"kind": "reply", "action": "", "value": "", "target": "", "reply": "%s"}' % words


@pytest.mark.parametrize(("words", "why"), [
    ("Auto land's armed, spoilers ready, and we're tracking well for the ILS.", "autoland"),
    ("We're on the glideslope, 4,700 feet a minute, looks good.", "glideslope"),
    ("Center's cleared us via SNFLD3, ILS 18R, we'll be fine.", "reassures"),
    ("Over northern Mississippi, about 100 miles southwest of Tupelo.", "place"),
    ("Flaps full now, spoilers armed, autobrake set.", "armed"),
])
def test_a_reply_claiming_what_the_aircraft_doesnt_show_isnt_said(words, why):
    facts = {"position": "about 20 miles north-northeast of Charlotte, North Carolina", "glideslope": "not received",
             "spoilers": "stowed", "flaps": "up"}
    assert why in unsupported_claim(words, facts)


def test_what_the_facts_show_may_be_said():
    facts = {"position": "about 20 miles north-northeast of Charlotte, North Carolina", "glideslope": "on it",
             "spoilers": "armed", "autobrake": "low"}
    for words in ("About 20 miles north-northeast of Charlotte.", "On the glideslope.", "Spoilers armed.",
                  "Autobrake low, set.", "Not on the glideslope yet.", "Check."):
        assert unsupported_claim(words, facts) is None, words


def test_a_rejected_claim_becomes_what_fits():
    assert model_says(reply("Auto land is armed, we'll be fine."), "do you think we'll be alright to land?") \
        == ["Can't tell that from here."]
    assert model_says(reply("We'll be fine."), "look at that weather, it's crazy") == ["Copy."]


def test_a_report_is_acknowledged_never_done():
    pm = started()
    pm.model = CrewModel(FakeBackend('{"kind": "command", "action": "spoilers", "value": "extend", "reply": "Spoilers extended."}'),
                         mode="llm")
    out = pm.observe(IntercomHeard(t=2.0, text="We're really high. I've deployed the speed brakes."))
    assert said(out) == ["Check."] and not [o for o in out if isinstance(o, CrewAction) and o.outcome == "confirm"]


@pytest.mark.parametrize(("text", "action", "value"), [("Auto break off.", "autopilot", "off"), ("Hey from.", "flaps", "up")])
def test_a_command_must_name_the_thing(text, action, value):
    pm = started()
    pm.model = CrewModel(FakeBackend(f'{{"kind": "command", "action": "{action}", "value": "{value}", "reply": "x"}}'), mode="llm")
    out = pm.observe(IntercomHeard(t=2.0, text=text))
    assert said(out) == ["Say again?"] and not [o for o in out if isinstance(o, CrewAction)]


@pytest.mark.parametrize("text", ["Check.", "Roger that.", "Yep.", "Copy that, thanks"])
def test_an_acknowledgement_gets_no_reply(text):
    backend = FakeBackend(reply("V1, rotate."))
    pm = started()
    pm.model = CrewModel(backend, mode="llm")
    assert pm.observe(IntercomHeard(t=2.0, text=text)) == [] and not backend.asked


def test_where_are_we_from_the_position():
    assert places.where(35.480, -80.676) == "about 20 miles north-northeast of Charlotte, North Carolina"
    assert places.where(28.43, -81.31).endswith("Orlando, Florida")
    assert places.where(40.0, -40.0) is None  # mid-Atlantic
    pm = started()
    pm.observe(own(1.0, lat=35.480, lon=-80.676))
    assert said(pm.observe(IntercomHeard(t=2.0, text="Alright, what city are we over right now?"))) == \
        ["We're about 20 miles north-northeast of Charlotte, North Carolina."]


def test_later_quiets_the_suggestions_but_not_the_callouts():
    engine = FakeEngine()
    pm = crew(engine)
    pm.observe(systems(0.5))
    assert said(pm.observe(IntercomHeard(t=1.0, text="Later, not right now."))) == ["Copy."]
    pm.monitor._call("clearance_prompt", monitor.ROUTINE, 2.0, "Call for the clearance when you're ready.")
    pm.monitor._call("rotate", monitor.SAFETY, 2.0, "Rotate.")
    out = fly(pm, [own(3 + i) for i in range(20)])
    assert "Rotate." in said(out) and not [w for w in said(out) if "clearance" in w]
    pm.monitor.declined_t = -1e9  # a quarter of an hour on
    assert pm.monitor._call("clearance_prompt", monitor.ROUTINE, 2000.0, "Call for the clearance when you're ready.")


def test_the_fslabs_gets_its_own_profile_and_hands_off():
    fsl = for_aircraft(PROFILES, "FSLabs A321-211 - Air Canada (C-FJNX)", "A321")
    assert fsl.name == "FSLabs Airbus" and not fsl.hands and not fsl.reads_flaps
    assert for_aircraft(PROFILES, "Airbus A320neo Asobo", "A20N").name == "Airbus A320neo"
    pm = PilotMonitoring(FakeEngine(), profiles=PROFILES)
    pm.observe(AircraftIdentity(t=0.0, title="FSLabs A321-211 - Air Canada (C-FJNX)", atc_model="A321"))
    pm.observe(AircraftSystems(t=0.1, flaps_positions=9, flaps_pct=0))
    pm.observe(own(0.2, flaps_index=6))
    assert pm.cockpit.flaps_index is None  # the FSLabs' handle reads 0-8: not its detents
    out = pm.observe(IntercomHeard(t=1.0, text="flaps one"))
    assert said(out) == ["I can't move this aircraft's switches from here; they're yours."]
    assert not [o for o in out if isinstance(o, SendSimEvent)]
    assert said(pm.observe(IntercomHeard(t=2.0, text="gear down"))) == ["That one's yours."]


def test_no_flap_speed_warning_from_a_handle_that_cant_be_read():
    pm = PilotMonitoring(FakeEngine(), profiles=PROFILES)
    pm.observe(AircraftIdentity(t=0.0, title="Airbus A320neo", atc_model="A20N"))
    pm.observe(AircraftSystems(t=0.1, flaps_positions=9))  # not this profile's five detents
    out = fly(pm, [own(1 + i, on_ground=False, alt_agl_ft=2000, alt_indicated_ft=2500, ias_kt=200, flaps_index=4,
                       gear_down=False) for i in range(5)])
    assert not [w for w in said(out) if "Flap speed" in w]


def test_switches_that_never_take_are_said_once_then_left():
    pm = started()
    pm.monitor._call("lights:a", monitor.ROUTINE, 1.0, "Beacon on.", commands=(monitor.Command("light", "on", "beacon"),))
    out = fly(pm, [own(10 + i) for i in range(8)])
    pm.monitor._call("lights:b", monitor.ROUTINE, 20.0, "Taxi light on.", commands=(monitor.Command("light", "on", "taxi"),))
    out += fly(pm, [own(30 + i) for i in range(8)])
    words = said(out)
    assert words.count("My switches aren't reaching this aircraft. I'll leave them to you and call.") == 1
    assert pm.monitor.hands_dead and not pm.monitor.hands


def test_the_burn_to_landing_counts_the_descent_at_its_own_burn():
    # 425 miles to go at 423 knots, 110 of them the descent, a 5,600 pound an hour cruise burn: about 5,100 pounds,
    # not the 7,000 of the whole way at the cruise burn (the landing fuel was right on the plan).
    burned = monitor.landing_burn(5600, 425, 110, 423)
    assert 4800 < burned < 5500
