import Foundation
import Testing
@testable import LocalTCKit

/// The Denver to Seattle flight (2026-09-23), as the desktop's replay builder wrote it
/// (Fixtures/replay_kden_ksea.json, from replay/rewatch.py).
func kdenKsea() throws -> Replay {
    try Replay.decode(Data(try fixture("replay_kden_ksea.json").utf8))
}

@Suite("Replaying a flight")
struct ReplayTests {
    @Test("The desktop's replay decodes, all of it")
    func decodes() throws {
        let r = try kdenKsea()
        #expect(r.flight.callsign == "DAL2543" && r.flight.origin == "KDEN" && r.flight.destination == "KSEA")
        #expect(r.track.t.count == r.track.lat.count && r.track.t.count == r.track.gnd.count && r.track.t.count > 1000)
        #expect(r.radio.filter { $0.kind == "atc" }.count == 43)
        #expect(r.radio.contains { $0.ok == false && $0.readback == "readback incomplete: squawk" })
        #expect(Set(r.airports.keys) == ["KDEN", "KSEA"] && r.route.first?.ident == "MUGBE")
    }

    @Test("A replay from a newer LocalTC says so")
    func newer() throws {
        var json = try fixture("replay_kden_ksea.json")
        json = json.replacingOccurrences(of: "{\"v\":1,", with: "{\"v\":2,")
        #expect(throws: APIError.self) { try Replay.decode(Data(json.utf8)) }
    }

    @Test("Between two track points, the aircraft is in between, turning the short way")
    func interpolates() throws {
        let clock = ReplayClock(try kdenKsea())
        let k = clock.replay.track
        let i = try #require(k.t.indices.first { k.gnd[$0] == 0 && k.t[$0] > 3000 })
        let mid = (k.t[i] + k.t[i + 1]) / 2
        let s = clock.sample(at: mid)
        #expect(s.index == i && !s.onGround)
        #expect(abs(s.lat - (k.lat[i] + k.lat[i + 1]) / 2) < 1e-9)
        #expect(s.alt > 30000)
        #expect(clock.sample(at: -5).index == 0 && clock.sample(at: 1e9).index == k.t.count - 1)
    }

    @Test("The line, the phase and the next call at a moment")
    func atAMoment() throws {
        let clock = ReplayClock(try kdenKsea())
        #expect(clock.line(at: 0) == nil)
        let takeoff = try #require(clock.replay.marks.first { $0.kind == "takeoff" }).t
        #expect(clock.phase(at: takeoff) == "Takeoff roll")
        let line = try #require(clock.line(at: takeoff))
        #expect(clock.replay.radio[line].t <= takeoff)
        let next = try #require(clock.call(after: takeoff))
        #expect(next > takeoff && clock.call(before: next) ?? .infinity < next)
        #expect(ReplayClock.elapsed(3723) == "+1:02:03")
        #expect(clock.zulu(at: 0) != nil)
    }

    @Test("Playing skips the quiet cruise and stops at the end")
    func advances() throws {
        let clock = ReplayClock(try kdenKsea())
        #expect(clock.advance(100, by: 1, speed: 4, skipQuiet: false) == 104)
        let cruise = 4000.0  // over Idaho, nothing said for a long while
        let upcoming = try #require(clock.replay.radio.map(\.t).first { $0 > cruise })
        #expect(upcoming - cruise > ReplayClock.quietS)
        #expect(clock.advance(cruise, by: 0.02, speed: 4, skipQuiet: true) == upcoming - ReplayClock.leadS)
        #expect(clock.advance(clock.replay.duration - 1, by: 10, speed: 64, skipQuiet: false) == clock.replay.duration)
    }
}

/// The logbook and a replay, from the account.
final class LogbookServer: HTTPTransport, @unchecked Sendable {
    var requests: [URLRequest] = []
    let replay: Data

    init(replay: Data) { self.replay = replay }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        requests.append(request)
        func reply(_ status: Int, _ data: Data) -> (Data, HTTPURLResponse) {
            (data, HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!)
        }
        guard request.value(forHTTPHeaderField: "Authorization") == "Bearer t" else { return reply(401, Data(#"{"error":"Sign in first."}"#.utf8)) }
        switch request.url!.path {
        case "/v1/flights":
            return reply(200, Data(#"{"flights":[{"id":"kden-ksea","started_at":"2026-09-23T22:18:00Z","callsign":"DAL2543","aircraft":"A220-300","origin":"KDEN","destination":"KSEA","arrival_gate":"","air_min":149.4,"block_min":178,"landing_vs_fpm":-290,"landed":true,"has_replay":true,"readbacks":17}],"next":null}"#.utf8))
        case "/v1/stats":
            return reply(200, Data(#"{"flights":1,"air_hours":2.5,"block_hours":3,"distance_nm":880,"landings":1,"average_landing_fpm":-290,"readback_accuracy":0.82,"airports":[],"routes":[]}"#.utf8))
        case "/v1/flights/kden-ksea/replay":
            return reply(200, replay)
        default:
            return reply(404, Data(#"{"error":"No replay for this flight."}"#.utf8))
        }
    }
}

@Suite("The account's logbook on the phone")
struct LogbookAPITests {
    @Test("Flights, totals and a replay come down; a flight without one is a plain 404")
    func logbook() async throws {
        let server = LogbookServer(replay: Data(try fixture("replay_kden_ksea.json").utf8))
        let store = MemoryTokenStore()
        store.save(token: "t", email: "pilot@example.com")
        let api = APIClient(baseURL: URL(string: "https://api.test")!, transport: server, tokens: store)
        let page = try await api.flights(before: "2026-10-01T00:00:00Z", limit: 20)
        #expect(page.flights.first?.hasReplay == true && page.flights.first?.landingVsFpm == -290 && page.next == nil)
        let query = try #require(server.requests.first?.url?.query)
        #expect(query.contains("limit=20") && query.contains("before=2026-10-01T00:00:00Z"))
        #expect(try await api.stats().readbackAccuracy == 0.82)
        #expect(try await api.replay(id: "kden-ksea").flight.callsign == "DAL2543")
        await #expect(throws: APIError.self) { try await api.replay(id: "other") }
    }
}
