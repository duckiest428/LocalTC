import Foundation
import Testing
@testable import LocalTCKit

func fixture(_ name: String) throws -> String {
    let url = try #require(Bundle.module.url(forResource: "Fixtures/\(name)", withExtension: nil))
    return try String(contentsOf: url, encoding: .utf8)
}

@Suite("The companion protocol, as the desktop sends it")
struct ProtocolTests {
    @Test("The direct stream decodes: hello, then the live messages")
    func localStream() throws {
        var decoder = SSELineDecoder()
        var messages: [LiveMessage] = []
        for line in try fixture("stream.txt").split(separator: "\n", omittingEmptySubsequences: false) {
            if let (event, data) = decoder.feed(line), let message = try LiveMessage.decode(type: event, data: Data(data.utf8)) {
                messages.append(message)
            }
        }
        guard case .hello(let hello) = messages.first else { Issue.record("no hello first"); return }
        #expect(hello.status?.callsign == "FFT2084")
        #expect(hello.status?.phaseLabel == "Cruise")
        #expect(hello.status?.tuned?.label == "Albuquerque Center 133.650")
        #expect(hello.route?.destination == "KPHX")
        #expect((hello.route?.fixes.count ?? 0) > 5)
        #expect((hello.radio?.count ?? 0) == 40)
        #expect(messages.contains { if case .own = $0 { true } else { false } })
        #expect(messages.contains { if case .alert(let a) = $0 { a.kind == .handoff } else { false } })
    }

    @Test("Relay frames decode, and an unknown type from a newer LocalTC is skipped")
    func relay() throws {
        let lines = try fixture("relay.jsonl").split(separator: "\n")
        let messages = try lines.map { try LiveMessage.decodeEnvelope(String($0)) }
        #expect(messages.count == 7)
        #expect(messages.last! == nil)
        guard case .traffic(let traffic)? = messages[2] else { Issue.record("no traffic"); return }
        #expect(!traffic.isEmpty)
    }

    @Test("A radio line says who spoke")
    func speakers() {
        #expect(RadioLine(kind: "atc", text: "x").speaker == .atc)
        #expect(RadioLine(kind: "copilot", text: "x").speaker == .pilot)
        #expect(RadioLine(kind: "readback", text: "x").speaker == .system)
    }

    @Test("Another flight is dimmed, unless it's a real one of LocalTC's traffic")
    func traffic() throws {
        let decode = { (json: String) in try JSONDecoder().decode(RadioLine.self, from: Data(json.utf8)) }
        #expect(try decode(#"{"kind":"chatter","text":"x","callsign":"BAW 6RB"}"#).speaker == .other)
        let pilot = try decode(#"{"kind":"chatter","text":"x","callsign":"BAW 6RB","ident":"BAW6RB","live":true}"#)
        #expect(pilot.speaker == .traffic && pilot.callsign == "BAW 6RB")
        #expect(try decode(#"{"kind":"chatter","text":"x","callsign":"BAW 6RB","atc":true,"live":true}"#).speaker == .atc)
    }

    @Test("A line carries the id of its kept audio, and which COM it was on")
    func audio() throws {
        let line = try JSONDecoder().decode(RadioLine.self, from: Data(#"{"kind":"pilot","text":"hi","radio":2,"audio":"0123456789abcdef"}"#.utf8))
        #expect(line.audio == "0123456789abcdef" && line.radio == 2)
        #expect(Listener(rawValue: "com2") == .com2)
    }

    @Test("An area across the date line is cut there, not drawn across the world")
    func dateLine() {
        // Anchorage-like: 170°E to 160°W, as the desktop sends it (-180...180).
        let ring: [[Double]] = [[50, 170], [50, -160], [60, -160], [60, 170]]
        let pieces = antimeridianPieces(ring)
        #expect(pieces.count == 2)
        for piece in pieces {
            let lons = piece.map { $0[1] }
            #expect(lons.allSatisfy { (-180...180).contains($0) })
            #expect(lons.max()! - lons.min()! <= 30)  // no edge right across the map
        }
        #expect(antimeridianPieces([[40, -120], [40, -110], [45, -110]]).count == 1)  // an ordinary one is left alone
    }
}

@Suite("The flight as the phone keeps it")
@MainActor
struct FlightStoreTests {
    func replay() throws -> FlightStore {
        let store = FlightStore()
        for line in try fixture("relay.jsonl").split(separator: "\n") {
            if let message = try LiveMessage.decodeEnvelope(String(line)) { store.apply(message) }
        }
        return store
    }

    @Test("Messages build the picture")
    func picture() throws {
        let store = try replay()
        #expect(store.own != nil)
        #expect(!store.traffic.isEmpty)
        #expect(store.radio.count == 6)
        #expect(store.alerts.count == 1)
        #expect(store.status.active == false)  // the last frame ends the flight
        #expect(store.trail.isEmpty)  // and clears the trail for the next one
    }

    @Test("A catch-up doesn't repeat lines already shown")
    func backlog() {
        let store = FlightStore()
        var line = RadioLine(kind: "atc", text: "Frontier 2084, roger.")
        line.t = 12
        store.apply(.radio(line))
        store.apply(.radioBacklog([line, RadioLine(kind: "pilot", text: "Wilco")]))
        #expect(store.radio.map(\.text) == ["Frontier 2084, roger.", "Wilco"])
    }

    @Test("The radio log keeps the last 200 lines")
    func radioKeep() {
        let store = FlightStore()
        for i in 0..<250 { store.apply(.radio(RadioLine(kind: "atc", text: "\(i)"))) }
        #expect(store.radio.count == FlightStore.radioKeep)
        #expect(store.radio.first?.text == "50")
    }

    @Test("A map opened mid-flight draws the path flown so far")
    func trail() throws {
        let store = FlightStore()
        let message = try LiveMessage.decodeEnvelope(#"{"type":"trail","data":[[33.0,-117.0],[33.1,-116.9]]}"#)
        store.apply(try #require(message))
        #expect(store.trail == [.init(lat: 33.0, lon: -117.0), .init(lat: 33.1, lon: -116.9)])
        let hello = try LiveMessage.decodeEnvelope(#"{"type":"hello","data":{"trail":[[40.0,-100.0]]}}"#)
        store.apply(try #require(hello))
        #expect(store.trail == [.init(lat: 40.0, lon: -100.0)])
    }

    @Test("A long flight keeps its whole path from the start, less dense, as it goes")
    func longTrail() throws {
        let store = FlightStore()
        for i in 0..<5000 {
            let own = try LiveMessage.decodeEnvelope(#"{"type":"own","data":{"lat":\#(Double(i) * 0.01),"lon":-100.0}}"#)
            store.apply(try #require(own))
        }
        #expect(store.trail.count <= FlightStore.trailKeep)
        #expect(store.trail.first == .init(lat: 0, lon: -100))  // the start stays where it was
        #expect(store.trail.last == .init(lat: 49.99, lon: -100))
    }

    @Test("The taxi from the gate keeps its turns, however long the flight gets")
    func taxiKept() throws {
        let store = FlightStore()
        func own(_ lat: Double, _ lon: Double, ground: Bool) throws {
            let m = try LiveMessage.decodeEnvelope(#"{"type":"own","data":{"lat":\#(lat),"lon":\#(lon),"ground":\#(ground)}}"#)
            store.apply(try #require(m))
        }
        for i in 0..<50 { try own(40, -73 + Double(i) * 0.0002, ground: true) }  // east along the apron
        for i in 0..<50 { try own(40 + Double(i) * 0.0002, -72.99, ground: true) }  // then north to the runway
        for i in 0..<6000 { try own(40.01 + Double(i) * 0.003, -72.99, ground: false) }
        #expect(store.trail.count <= FlightStore.trailKeep)
        #expect(store.trail.first == .init(lat: 40, lon: -73))
        #expect(store.trail.contains { abs($0.lat - 40) < 0.0003 && abs($0.lon + 72.99) < 0.0003 })  // the corner
    }

    @Test("The ATC zones the desktop's Live Map draws come through, and go with the flight")
    func zones() throws {
        let store = FlightStore()
        store.apply(.status(FlightStatus(active: true)))
        let json = #"""
        {"type":"zones","data":{"rules":"IFR","center":"Los Angeles Center",
          "tuned":{"station":"SoCal Departure","controller":"departure","mhz":124.3},
          "centers":[{"id":"KZLA","name":"Los Angeles Center","label":[34,-117],"rings":[[[33,-118],[35,-118],[35,-116]]],"active":true,"route":true}],
          "taxi":{"to":"runway 25R","points":[[33.94,-118.4],[33.95,-118.41]],"taxiways":["B","AA"]},
          "airports":[{"icao":"KLAX","lat":33.94,"lon":-118.4,"tower_nm":5,
            "stations":[{"controller":"tower","station":"LA Tower","mhz":133.9,"tuned":true},{"controller":"ground","next":true},
                        {"controller":"approach"},{"controller":"departure","tuned":false}],
            "runways":[{"name":"07L/25R","lat":33.93,"lon":-118.4,"heading_true":90,"length_m":3000}]}],
          "classes":[{"icao":"KLAX","lat":33.94,"lon":-118.4,"class":"B","rings":[{"nm":10,"floor":0,"ceiling":10000},{"nm":20,"floor":3100,"ceiling":10000}]}],
          "someday":"a newer desktop's extra"}}
        """#
        store.apply(try #require(try LiveMessage.decodeEnvelope(json)))
        let z = try #require(store.zones)
        #expect(z.centers.first?.id == "KZLA" && z.centers.first?.rings.first?.count == 3)
        #expect(z.terminals.isEmpty && z.final == nil)  // missing lists and objects: empty
        #expect(z.tuned?.label == "SoCal Departure 124.300")
        let airport = try #require(z.airports.first)
        #expect(airport.towerNm == 5)
        #expect(airport.badges == [.init(letter: "T", tuned: true, next: false), .init(letter: "G", tuned: false, next: true),
                                   .init(letter: "A", tuned: false, next: false)])
        let ends = try #require(airport.runways.first?.ends)
        #expect(abs(ends[0][0] - 33.93) < 1e-9 && ends[1][1] > ends[0][1])  // east-west: the same latitude, 3 km apart
        let bravo = try #require(z.classes.first?.rings)
        #expect(bravo.map(\.ceilingLabel) == ["100", "100"] && bravo.map(\.floorLabel) == ["SFC", "31"])
        store.apply(.status(FlightStatus(active: false)))
        #expect(store.zones == nil)
    }

    @Test("The EFB comes through: the checklist, Airport Ops, ATC's hold, the notes and the briefings")
    func efb() throws {
        let store = FlightStore()
        let json = #"""
        {"type":"efb","data":{
          "checklist":{"id":"before_start","name":"Before start","status":"waiting","index":1,"source":"Airbus A320 family",
            "lines":[{"challenge":"Cockpit prep","response":"Completed","result":"read"},{"challenge":"Parking brake","response":"Set","result":""}]},
          "ops":{"icao":"KSEA","role":"departure","gsx":false,"services":{"fuel":{"state":"in progress","source":"the sim's fuel"}},
            "deice":{"required":true,"position":2,"wait_min":12,"why":"-2°C with snow","state":"queued"},
            "messages":[{"t":1791983400.5,"kind":"service","text":"Fuelling in progress.","from":"Airport Ops"}],
            "requests":[{"gate":"B25","icao":"KLAX","t":1,"state":"approved","why":"Gate B25 approved."}]},
          "atc":{"hold":{"fix":"BLAKO","lat":47.8,"lon":-122.3,"inbound":160,"turn":"right","leg_min":1.0,"altitude_ft":8000,
            "reason":"arrival traffic","efc":"1432","position":5,"delay_min":8,"entry":"direct","state":"holding","laps":1,
            "racetrack":[[47.8,-122.3],[47.79,-122.28],[47.8,-122.3]]},
            "routing":[{"kind":"reroute","fix":"BRAVO","heading":40,"reason":"restricted airspace R-6714","state":"active","line":[[46,-121.5],[46.5,-121],[47,-121.5]]}],
            "conditions":[{"kind":"behind_landing","traffic":"the landing Airbus A320","runway":"16L","state":"active","value":null}],
            "minimum_altitude_ft":4200},
          "scratchpad":[{"id":"a1","text":"SQ 4521","kind":"squawk","t":100,"pinned":true,"picked":{"squawk":"4521"}}],
          "achievements":{"earned":5,"total":18,"new":[{"id":"first_flight","title":"First flight","detail":"KSEA to KLAX"}]},
          "chartfox":{"connected":false,"credit":{"text":"Chart data powered by ChartFox","url":"https://chartfox.org","privacy":"https://chartfox.org/privacy","disclaimer":"For flight simulation only."}},
          "briefings":{"KSEA":{"icao":"KSEA","metar":"KSEA 141153Z 18007KT","taf":{"raw":"TAF KSEA","available":true},
            "notams":{"items":[{"id":"A1/26","text":"RWY 16L/34R CLSD","works_to":[["runway_closed","16L/34R"]],"in_force":true}],"available":true}}},
          "book":{"name":"Airbus A320 family","source":"QRH","checklists":[{"id":"before_start","name":"Before start","phase":"before_start"}]},
          "hazards":[{"id":"T1","kind":"tfr","name":"Stadium","floor_ft":0,"ceiling_ft":3000,"polygon":[[47.5,-122.3],[47.5,-122.2],[47.6,-122.2]]}],
          "someday":"a newer desktop's extra"}}
        """#
        store.apply(try #require(try LiveMessage.decodeEnvelope(json)))
        let e = try #require(store.efb)
        #expect(e.checklist?.lines?.count == 2 && e.checklist?.running == true)
        #expect(e.ops?.deice?.waitMin == 12 && e.ops?.services?["fuel"]?.state == "in progress")
        #expect(e.atc?.hold?.active == true && e.atc?.hold?.racetrack?.count == 3 && e.atc?.minimumAltitudeFt == 4200)
        #expect(e.atc?.routing?.first?.kind == "reroute" && e.atc?.conditions?.first?.runway == "16L")
        #expect(e.scratchpad?.first?.picked?["squawk"] == "4521")
        #expect(e.briefings?["KSEA"]?.notams?.items?.first?.forAtc == true)
        #expect(e.book?.checklists.first?.id == "before_start" && e.hazards?.first?.kind == "tfr")
        #expect(EfbCommand.checklist(action: "run", id: "before_start").json["do"] as? String == "run")
        #expect(EfbCommand.gates().relayable == false && EfbCommand.gate("B25").relayable)
    }

    @Test("A part of the EFB the app can't read leaves the rest: the briefings still show")
    func efbPartlyUnreadable() throws {
        let store = FlightStore()
        let json = #"""
        {"type":"efb","data":{
          "atc":{"hold":{"fix":"BLAKO","inbound":160.5},"minimum_altitude_ft":4200.7},
          "hazards":[{"id":"S1","name":"no kind","polygon":[]},{"id":"T1","kind":"tfr","polygon":[[47.5,-122.3]]}],
          "scratchpad":[{"text":"no id"},{"id":"a1","text":"SQ 4521"}],
          "briefings":{"KSEA":{"icao":"KSEA","metar":"KSEA 141153Z 18007KT",
            "notams":{"items":[{"id":12,"text":"ILS 16R U/S","works_to":[["ils_out",16]],"in_force":true},{"text":5}]}},
            "KLAX":{"metar":{"odd":true}}}}}
        """#
        store.apply(try #require(try LiveMessage.decodeEnvelope(json)))
        let e = try #require(store.efb)
        #expect(e.atc == nil)
        #expect(e.hazards?.map(\.kind) == ["tfr"] && e.scratchpad?.map(\.id) == ["a1"])
        let notams = try #require(e.briefings?["KSEA"]?.notams?.items)
        #expect(notams.first?.id == "12" && notams.first?.worksTo == [["ils_out", "16"]] && notams.first?.forAtc == true)
        #expect(e.briefings?["KLAX"] == nil)
    }
}
