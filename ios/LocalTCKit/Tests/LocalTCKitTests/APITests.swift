import Foundation
import Testing
@testable import LocalTCKit

/// Just enough of the account server for the phone's sign-in.
final class FakeServer: HTTPTransport, @unchecked Sendable {
    var requests: [URLRequest] = []
    var tokens: Set<String> = []
    var connect = ConnectInfo(lan: ["http://192.168.1.20:47800"], key: String(repeating: "k", count: 43))

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        requests.append(request)
        let body = request.httpBody.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: String] } ?? [:]
        let auth = request.value(forHTTPHeaderField: "Authorization")?.replacingOccurrences(of: "Bearer ", with: "")
        func reply(_ status: Int, _ object: Any) -> (Data, HTTPURLResponse) {
            (try! JSONSerialization.data(withJSONObject: object),
             HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!)
        }
        switch (request.httpMethod ?? "GET", request.url!.path) {
        case ("POST", "/v1/auth/start"):
            return reply(202, ["message": "Check your email: we've sent a sign-in code and a link."])
        case ("POST", "/v1/auth/finish"):
            guard body["code"] == "123456", body["kind"] == "ios" else { return reply(401, ["error": "That code is wrong or has expired."]) }
            tokens.insert("phone-token")
            return reply(200, ["token": "phone-token", "user": ["email": body["email"]!]])
        default:
            guard let auth, tokens.contains(auth) else { return reply(401, ["error": "Sign in first."]) }
            switch request.url!.path {
            case "/v1/me":
                return reply(200, ["email": "pilot@example.com", "sessions": [["id": "s1", "kind": "ios", "device": "iPhone", "current": true]]])
            case "/v1/live/connect":
                return reply(200, ["lan": connect.lan, "key": connect.key as Any])
            case "/v1/auth/logout":
                tokens.remove(auth)
                return reply(200, ["ok": true])
            default:
                return reply(404, ["error": "no"])
            }
        }
    }
}

@Suite("Signing the phone in by email")
struct SignInTests {
    @Test("Email, then the 6-digit code, signs in; the token goes to the store")
    func signIn() async throws {
        let server = FakeServer()
        let store = MemoryTokenStore()
        let api = APIClient(baseURL: URL(string: "https://api.test")!, transport: server, tokens: store)
        #expect(!api.signedIn)
        let message = try await api.start(email: "pilot@example.com")
        #expect(message.contains("Check your email"))
        try await api.finish(email: "pilot@example.com", code: "123 456", device: "iPhone")
        #expect(api.signedIn && api.email == "pilot@example.com")
        #expect(try await api.me().sessions.first?.kind == "ios")
        let finish = try #require(server.requests.first { $0.url!.path == "/v1/auth/finish" })
        let body = try #require(JSONSerialization.jsonObject(with: finish.httpBody!) as? [String: String])
        #expect(body["kind"] == "ios" && body["code"] == "123456")
    }

    @Test("A wrong code says so and leaves the phone signed out")
    func wrongCode() async throws {
        let api = APIClient(baseURL: URL(string: "https://api.test")!, transport: FakeServer(), tokens: MemoryTokenStore())
        await #expect(throws: APIError(status: 401, message: "That code is wrong or has expired.")) {
            try await api.finish(email: "pilot@example.com", code: "000000", device: "iPhone")
        }
        #expect(!api.signedIn)
    }

    @Test("Anything but six digits isn't sent")
    func shortCode() async throws {
        let server = FakeServer()
        let api = APIClient(baseURL: URL(string: "https://api.test")!, transport: server, tokens: MemoryTokenStore())
        await #expect(throws: APIError.self) { try await api.finish(email: "a@b.c", code: "123", device: "x") }
        #expect(server.requests.isEmpty)
    }

    @Test("Signed out on the website: the next call signs the phone out too")
    func revoked() async throws {
        let server = FakeServer()
        let api = APIClient(baseURL: URL(string: "https://api.test")!, transport: server, tokens: MemoryTokenStore())
        try await api.finish(email: "pilot@example.com", code: "123456", device: "iPhone")
        server.tokens.removeAll()
        await #expect(throws: APIError.self) { try await api.me() }
        #expect(!api.signedIn)
    }

    @Test("The relay socket is wss, with the token")
    func socketRequest() async throws {
        let api = APIClient(baseURL: URL(string: "https://api.test")!, transport: FakeServer(), tokens: MemoryTokenStore())
        #expect(api.liveSocketRequest() == nil)
        try await api.finish(email: "pilot@example.com", code: "123456", device: "iPhone")
        let request = try #require(api.liveSocketRequest())
        #expect(request.url?.absoluteString == "wss://api.test/v1/live/ws")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer phone-token")
    }
}
