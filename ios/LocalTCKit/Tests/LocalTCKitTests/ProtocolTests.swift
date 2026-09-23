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
}
