import Foundation
import Testing
@testable import LocalTCKit

struct ScriptedFeed: LiveFeed {
    let items: [LiveMessage]
    var hold: Duration = .zero  // stay open this long after the last message (a live connection)

    func messages() -> AsyncThrowingStream<LiveMessage, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                for item in items { continuation.yield(item) }
                try? await Task.sleep(for: hold)
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

final class FakeFeeds: FeedFactory, @unchecked Sendable {
    var localUp: Bool
    var probed: [URL] = []
    var bonjour: [URL] = []
    var usedRelay = false

    init(localUp: Bool) { self.localUp = localUp }

    func probe(_ url: URL, key: String) async -> Bool {
        probed.append(url)
        return localUp && url.absoluteString.hasPrefix("http://192.168.")
    }

    func local(_ url: URL, key: String) -> LiveFeed {
        ScriptedFeed(items: [.status({ var s = FlightStatus(active: true); s.callsign = "LOCAL"; return s }())], hold: .seconds(60))
    }

    func relay() -> LiveFeed? {
        usedRelay = true
        return ScriptedFeed(items: [.status({ var s = FlightStatus(active: true); s.callsign = "RELAY"; return s }())], hold: .seconds(60))
    }

    func discovered() async -> [URL] { bonjour }
}

@Suite("Wi-Fi first, the server otherwise")
@MainActor
struct ConnectionTests {
    func setUp(localUp: Bool, mode: ConnectionMode = .automatic) async throws -> (ConnectionManager, FlightStore, FakeFeeds) {
        let api = APIClient(baseURL: URL(string: "https://api.test")!, transport: FakeServer(), tokens: MemoryTokenStore())
        try await api.finish(email: "pilot@example.com", code: "123456", device: "iPhone")
        let store = FlightStore()
        let feeds = FakeFeeds(localUp: localUp)
        let manager = ConnectionManager(api: api, store: store, feeds: feeds, recheckEvery: .milliseconds(100), retryAfter: .milliseconds(50))
        manager.mode = mode
        return (manager, store, feeds)
    }

    func waitFor(_ condition: @MainActor () -> Bool) async {
        for _ in 0..<100 where !condition() { try? await Task.sleep(for: .milliseconds(20)) }
    }

    @Test("On the same Wi-Fi it connects straight to the PC")
    func local() async throws {
        let (manager, store, feeds) = try await setUp(localUp: true)
        manager.start()
        await waitFor { store.status.callsign != nil }
        #expect(manager.state == .wifi)
        #expect(store.status.callsign == "LOCAL")
        #expect(!feeds.usedRelay)
        manager.stop()
    }

    @Test("Elsewhere it uses the server, and moves to Wi-Fi when the PC appears")
    func relayThenLocal() async throws {
        let (manager, store, feeds) = try await setUp(localUp: false)
        manager.start()
        await waitFor { store.status.callsign != nil }
        #expect(manager.state == .server && store.status.callsign == "RELAY")
        feeds.localUp = true
        await waitFor { store.status.callsign == "LOCAL" }
        #expect(manager.state == .wifi)
        manager.stop()
    }

    @Test("'Same Wi-Fi only' never uses the server")
    func wifiOnly() async throws {
        let (manager, _, feeds) = try await setUp(localUp: false, mode: .wifiOnly)
        manager.start()
        try await Task.sleep(for: .milliseconds(200))
        #expect(manager.state != .server && !feeds.usedRelay)
        manager.stop()
    }

    @Test("'Server only' never probes the local network")
    func serverOnly() async throws {
        let (manager, store, feeds) = try await setUp(localUp: true, mode: .serverOnly)
        manager.start()
        await waitFor { store.status.callsign != nil }
        #expect(manager.state == .server && feeds.probed.isEmpty)
        manager.stop()
    }

    @Test("A Bonjour find is tried too")
    func bonjour() async throws {
        let (manager, store, feeds) = try await setUp(localUp: true)
        feeds.bonjour = [URL(string: "http://192.168.1.99:47800")!]
        manager.start()
        await waitFor { store.status.callsign != nil }
        #expect(feeds.probed.contains(URL(string: "http://192.168.1.20:47800")!))
        manager.stop()
    }
}
