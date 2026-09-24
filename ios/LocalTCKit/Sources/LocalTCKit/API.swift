import Foundation

/// The LocalTC account server (server/README.md). No passwords: an emailed 6-digit code signs the phone in.
public struct APIError: Error, LocalizedError, Equatable {
    public var status: Int
    public var message: String
    public var errorDescription: String? { message }

    public init(status: Int, message: String) {
        self.status = status
        self.message = message
    }
}

public struct Device: Codable, Sendable, Equatable, Identifiable {
    public var id: String
    public var kind: String
    public var device: String
    public var lastUsedAt: String?
    public var current: Bool?
}

public struct Me: Codable, Sendable, Equatable {
    public var email: String
    public var sessions: [Device]
}

public struct ConnectInfo: Codable, Sendable, Equatable {
    public var lan: [String]
    public var key: String?

    public init(lan: [String], key: String?) {
        self.lan = lan
        self.key = key
    }
}

public protocol HTTPTransport: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
}

public struct URLSessionTransport: HTTPTransport {
    let session: URLSession
    public init(session: URLSession = .shared) { self.session = session }

    public func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        return (data, http)
    }
}

public final class APIClient: Sendable {
    public static let production = URL(string: "https://api.localtc.tech")!

    public let baseURL: URL
    let transport: HTTPTransport
    let tokens: TokenStore
    let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    public init(baseURL: URL = APIClient.production, transport: HTTPTransport = URLSessionTransport(), tokens: TokenStore) {
        self.baseURL = baseURL
        self.transport = transport
        self.tokens = tokens
    }

    public var signedIn: Bool { tokens.token != nil }
    public var email: String? { tokens.email }

    /// Email a sign-in code (the first time, this creates the account). Returns the server's message.
    public func start(email: String) async throws -> String {
        let data = try await call("POST", "/v1/auth/start", ["email": email], auth: false)
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["message"] as? String
            ?? "Check your email for the sign-in code."
    }

    /// Sign this phone in with the code from the email.
    public func finish(email: String, code: String, device: String) async throws {
        let digits = code.filter(\.isNumber)
        guard digits.count == 6 else { throw APIError(status: 400, message: "Enter the 6-digit code from the email.") }
        let data = try await call("POST", "/v1/auth/finish",
                                  ["email": email, "code": digits, "kind": "ios", "device": device], auth: false)
        struct Answer: Decodable { var token: String; var user: User }
        struct User: Decodable { var email: String }
        let answer = try decoder.decode(Answer.self, from: data)
        tokens.save(token: answer.token, email: answer.user.email)
    }

    public func me() async throws -> Me {
        try decoder.decode(Me.self, from: try await call("GET", "/v1/me"))
    }

    public func revoke(device id: String) async throws {
        _ = try await call("DELETE", "/v1/sessions/\(id)")
    }

    /// Sign out here and on the server. Signed out locally even if the server can't be reached.
    public func logout() async {
        _ = try? await call("POST", "/v1/auth/logout")
        tokens.clear()
    }

    /// Where LocalTC on the PC can be reached on the local network, and its key.
    public func connect() async throws -> ConnectInfo {
        try decoder.decode(ConnectInfo.self, from: try await call("GET", "/v1/live/connect"))
    }

    public func status() async throws -> FlightStatus {
        try decoder.decode(FlightStatus.self, from: try await call("GET", "/v1/live"))
    }

    /// The account's logbook, newest first, a page at a time (`before`: the last page's `next`).
    public func flights(before: String? = nil, limit: Int = 50) async throws -> LogbookPage {
        var query = [URLQueryItem(name: "limit", value: String(limit))]
        if let before { query.append(URLQueryItem(name: "before", value: before)) }
        return try decoder.decode(LogbookPage.self, from: try await call("GET", "/v1/flights", query: query))
    }

    public func stats() async throws -> LogbookStats {
        try decoder.decode(LogbookStats.self, from: try await call("GET", "/v1/stats"))
    }

    /// A flight's replay, if the pilot uploaded it from the PC (404 otherwise).
    public func replay(id: String) async throws -> Replay {
        try Replay.decode(try await call("GET", "/v1/flights/\(id)/replay"))
    }

    /// The relay's WebSocket, signed in.
    public func liveSocketRequest() -> URLRequest? {
        guard let token = tokens.token,
              var components = URLComponents(url: baseURL.appending(path: "/v1/live/ws"), resolvingAgainstBaseURL: false)
        else { return nil }
        components.scheme = components.scheme == "http" ? "ws" : "wss"
        var request = URLRequest(url: components.url!)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return request
    }

    @discardableResult
    func call(_ method: String, _ path: String, _ body: [String: String]? = nil, query: [URLQueryItem] = [],
              auth: Bool = true) async throws -> Data {
        var url = baseURL.appending(path: path)
        if !query.isEmpty { url.append(queryItems: query) }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 20
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if auth, let token = tokens.token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let body { request.httpBody = try JSONSerialization.data(withJSONObject: body) }
        let (data, response) = try await transport.send(request)
        if response.statusCode == 401 && auth {
            tokens.clear()  // signed out on the website, or expired: signed out here too
        }
        guard (200..<300).contains(response.statusCode) else {
            let message = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
            throw APIError(status: response.statusCode, message: message ?? "The server said \(response.statusCode).")
        }
        return data
    }
}
