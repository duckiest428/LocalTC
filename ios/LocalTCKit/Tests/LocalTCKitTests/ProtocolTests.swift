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
}
