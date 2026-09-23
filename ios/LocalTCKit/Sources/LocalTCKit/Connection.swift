import Foundation
import Network
import Observation

/// A live feed of messages: LocalTC's direct stream on the local network, or the account relay.
public protocol LiveFeed: Sendable {
    func messages() -> AsyncThrowingStream<LiveMessage, Error>
}

/// How the phone reaches the PC.
public protocol FeedFactory: Sendable {
    /// Is LocalTC answering at this local URL with this key? Quick: the phone may be elsewhere.
    func probe(_ url: URL, key: String) async -> Bool
    func local(_ url: URL, key: String) -> LiveFeed
    func relay() -> LiveFeed?
    /// Addresses found by Bonjour on this network (best effort).
    func discovered() async -> [URL]
}

public enum ConnectionMode: String, CaseIterable, Sendable, Codable {
    case automatic, wifiOnly, serverOnly

    public var label: String {
        switch self {
        case .automatic: "Automatic"
        case .wifiOnly: "Same Wi-Fi only"
        case .serverOnly: "Through the server only"
        }
    }
}

/// Keeps the phone connected: straight to the PC when it's on the same Wi-Fi, through the relay otherwise,
/// and back to the direct line as soon as it's reachable again.
@MainActor @Observable
public final class ConnectionManager {
    public enum State: Equatable, Sendable {
        case offline, connecting, wifi, server

        public var label: String {
            switch self {
            case .offline: "Not connected"
            case .connecting: "Connecting"
            case .wifi: "Same Wi-Fi"
            case .server: "Via server"
            }
        }
    }

    public private(set) var state: State = .offline
    public private(set) var lastError: String?
    public var mode: ConnectionMode = .automatic

    let api: APIClient
    let store: FlightStore
    let feeds: FeedFactory
    let recheckEvery: Duration
    let retryAfter: Duration
    private var task: Task<Void, Never>?

    public init(api: APIClient, store: FlightStore, feeds: FeedFactory,
                recheckEvery: Duration = .seconds(30), retryAfter: Duration = .seconds(5)) {
        self.api = api
        self.store = store
        self.feeds = feeds
        self.recheckEvery = recheckEvery
        self.retryAfter = retryAfter
    }

    public func start() {
        guard task == nil else { return }
        task = Task { [weak self] in await self?.run() }
    }

    public func stop() {
        task?.cancel()
        task = nil
        state = .offline
    }

    public func restart() {
        stop()
        start()
    }

    func run() async {
        while !Task.isCancelled {
            state = .connecting
            if let (url, key) = await findLocal() {
                state = .wifi
                lastError = nil
                await consume(feeds.local(url, key: key))
                continue  // the PC went away (LocalTC closed, Wi-Fi changed): look again
            }
            if mode != .wifiOnly, let relay = feeds.relay() {
                state = .server
                await consumeRelayWhileNoLocal(relay)
                if Task.isCancelled { break }
                continue
            }
            state = .offline
            try? await Task.sleep(for: retryAfter)
        }
    }

    /// The first local URL that answers, from the account's list or Bonjour.
    func findLocal() async -> (URL, String)? {
        guard mode != .serverOnly else { return nil }
        let info: ConnectInfo
        do {
            info = try await api.connect()
        } catch {
            lastError = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
            return nil
        }
        guard let key = info.key else { return nil }
        var candidates = info.lan.compactMap(URL.init(string:))
        for url in await feeds.discovered() where !candidates.contains(url) { candidates.append(url) }
        for url in candidates where await feeds.probe(url, key: key) {
            return (url, key)
        }
        return nil
    }

    func consume(_ feed: LiveFeed) async {
        do {
            for try await message in feed.messages() {
                store.apply(message)
            }
        } catch {
            if !Task.isCancelled { lastError = error.localizedDescription }
        }
    }

    /// Stay on the relay, but check for the direct line every so often and switch to it when it's there.
    func consumeRelayWhileNoLocal(_ relay: LiveFeed) async {
        let done = Flag()
        let reading = Task {
            await self.consume(relay)
            done.set()
        }
        defer { reading.cancel() }
        var wait = min(recheckEvery, .seconds(5))  // soon after connecting (the PC may just have started), then less often
        while !Task.isCancelled {
            try? await Task.sleep(for: wait)
            wait = recheckEvery
            if done.value { return }  // the relay dropped: start over
            if mode != .serverOnly, await findLocal() != nil { return }  // the PC is on this Wi-Fi now
        }
    }
}

/// A one-way switch shared between tasks.
final class Flag: @unchecked Sendable {
    private let lock = NSLock()
    private var on = false
    var value: Bool { lock.withLock { on } }
    func set() { lock.withLock { on = true } }
}

// MARK: - The real feeds

public struct NetworkFeeds: FeedFactory {
    let api: APIClient
    let session: URLSession

    public init(api: APIClient, session: URLSession = .shared) {
        self.api = api
        self.session = session
    }

    public func probe(_ url: URL, key: String) async -> Bool {
        var request = URLRequest(url: url.appending(path: "/companion/v1/hello"))
        request.timeoutInterval = 1.5
        request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        guard let (_, response) = try? await session.data(for: request) else { return false }
        return (response as? HTTPURLResponse)?.statusCode == 200
    }

    public func local(_ url: URL, key: String) -> LiveFeed {
        LocalStreamFeed(url: url.appending(path: "/companion/v1/stream"), key: key, session: session)
    }

    public func relay() -> LiveFeed? {
        api.liveSocketRequest().map { RelayFeed(request: $0, session: session) }
    }

    public func discovered() async -> [URL] {
        await BonjourBrowser.find(timeout: .seconds(1.5))
    }
}

/// LocalTC's direct stream: Server-Sent Events over the local network.
struct LocalStreamFeed: LiveFeed {
    let url: URL
    let key: String
    let session: URLSession

    func messages() -> AsyncThrowingStream<LiveMessage, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                var request = URLRequest(url: url)
                request.timeoutInterval = 60  // the desktop sends a keep-alive every 15 s
                request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
                request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                do {
                    let (bytes, response) = try await session.bytes(for: request)
                    guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.userAuthenticationRequired) }
                    var decoder = SSELineDecoder()
                    for try await line in bytes.lines {
                        if let (event, data) = decoder.feed(Substring(line)),
                           let message = try LiveMessage.decode(type: event, data: Data(data.utf8)) {
                            continuation.yield(message)
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

/// The account relay: a WebSocket of {type, data} frames.
struct RelayFeed: LiveFeed {
    let request: URLRequest
    let session: URLSession

    func messages() -> AsyncThrowingStream<LiveMessage, Error> {
        AsyncThrowingStream { continuation in
            let socket = session.webSocketTask(with: request)
            socket.resume()
            let pinger = Task {
                while !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(20))
                    try? await socket.send(.string("ping"))
                }
            }
            let reader = Task {
                do {
                    while !Task.isCancelled {
                        let frame = try await socket.receive()
                        guard case .string(let text) = frame, text != "pong" else { continue }
                        if let message = try LiveMessage.decodeEnvelope(text) { continuation.yield(message) }
                    }
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in
                pinger.cancel()
                reader.cancel()
                socket.cancel(with: .goingAway, reason: nil)
            }
        }
    }
}

/// LocalTC advertises `_localtc._tcp` on the local network; this finds it and resolves its address.
enum BonjourBrowser {
    static func find(timeout: Duration) async -> [URL] {
        await withCheckedContinuation { continuation in
            let browser = NWBrowser(for: .bonjour(type: "_localtc._tcp", domain: nil), using: .tcp)
            let queue = DispatchQueue(label: "localtc.bonjour")
            let lock = NSLock()
            nonisolated(unsafe) var done = false
            nonisolated(unsafe) var found: [URL] = []
            nonisolated(unsafe) var pending = 0
            @Sendable func finish() {
                lock.lock()
                defer { lock.unlock() }
                guard !done else { return }
                done = true
                browser.cancel()
                continuation.resume(returning: found)
            }
            browser.browseResultsChangedHandler = { results, _ in
                for result in results {
                    lock.withLock { pending += 1 }
                    let connection = NWConnection(to: result.endpoint, using: .tcp)
                    connection.stateUpdateHandler = { state in
                        if case .ready = state, case .hostPort(let host, let port)? = connection.currentPath?.remoteEndpoint {
                            var address = "\(host)"
                            if let percent = address.firstIndex(of: "%") { address = String(address[..<percent]) }
                            if !address.contains(":"), let url = URL(string: "http://\(address):\(port.rawValue)") {
                                lock.withLock { if !found.contains(url) { found.append(url) } }
                            }
                            connection.cancel()
                        } else if case .failed = state {
                            connection.cancel()
                        }
                    }
                    connection.start(queue: queue)
                }
            }
            browser.start(queue: queue)
            queue.asyncAfter(deadline: .now() + .milliseconds(Int(timeout.components.seconds * 1000
                + timeout.components.attoseconds / 1_000_000_000_000_000))) { finish() }
        }
    }
}
