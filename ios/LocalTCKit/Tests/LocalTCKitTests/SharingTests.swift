import Foundation
import Testing
@testable import LocalTCKit

/// Just enough of the account server's shares, moments and Wrapped.
final class ShareServer: HTTPTransport, @unchecked Sendable {
    var requests: [URLRequest] = []
    let wrapped: Data

    init(wrapped: Data) { self.wrapped = wrapped }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        requests.append(request)
        func reply(_ status: Int, _ body: String) -> (Data, HTTPURLResponse) {
            (Data(body.utf8), HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!)
        }
        switch (request.httpMethod ?? "GET", request.url!.path) {
        case ("GET", "/v1/flights/kden-ksea/moments"):
            return reply(200, #"{"moments":[{"kind":"clearance","label":"Clearance","t":62,"station":"Denver Clearance","mhz":118.75,"text":"Delta 2543, cleared to Seattle via the ZIMMR3","score":100}],"names":{"KSEA":"Seattle"}}"#)
        case ("POST", "/v1/shares"):
            return reply(200, #"{"slug":"AbCdEfGhJk","kind":"flight","ref":"kden-ksea","url":"https://localtc.tech/f/AbCdEfGhJk","card":{"kind":"flight","callsign":"DAL2543"}}"#)
        case ("PUT", "/v1/shares/AbCdEfGhJk/image"), ("DELETE", "/v1/shares/AbCdEfGhJk"):
            return reply(200, #"{"ok":true}"#)
        case ("GET", "/v1/wrapped"):
            return (wrapped, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
        default:
            return reply(404, #"{"error":"no"}"#)
        }
    }
}

private func client(_ server: ShareServer) -> APIClient {
    let store = MemoryTokenStore()
    store.save(token: "t", email: "pilot@example.com")
    return APIClient(baseURL: URL(string: "https://api.test")!, transport: server, tokens: store)
}

@Suite("Sharing a flight and ATC Wrapped")
struct SharingTests {
    let recap = Data(try! fixture("wrapped_2026.json").utf8)

    @Test("A flight's card: the lines to quote, the share with the chosen one, its picture, and unsharing")
    func share() async throws {
        let server = ShareServer(wrapped: recap)
        let api = client(server)
        let kept = try await api.moments(id: "kden-ksea")
        #expect(kept.moments.first?.kind == "clearance" && kept.names == ["KSEA": "Seattle"])
        let made = try await api.shareFlight(id: "kden-ksea", quote: kept.moments.first, names: kept.names)
        #expect(made.slug == "AbCdEfGhJk" && made.url.absoluteString == "https://localtc.tech/f/AbCdEfGhJk")
        let sent = try #require(server.requests.last?.httpBody)
        let body = try #require(try JSONSerialization.jsonObject(with: sent) as? [String: Any])
        #expect(body["kind"] as? String == "flight" && body["ref"] as? String == "kden-ksea")
        #expect((body["quote"] as? [String: Any])?["mhz"] as? Double == 118.75)
        #expect(String(data: made.card, encoding: .utf8)!.contains("DAL2543"))

        try await api.uploadShareImage(slug: made.slug, png: Data([0x89, 0x50, 0x4E, 0x47]))
        #expect(server.requests.last?.value(forHTTPHeaderField: "Content-Type") == "image/png")
        try await api.unshare(slug: made.slug)
        #expect(server.requests.last?.httpMethod == "DELETE")
    }

    @Test("A year, as the server tells it, decodes: every slide")
    func decodes() async throws {
        let api = client(ShareServer(wrapped: recap))
        let year = WrappedPeriod(.year, containing: ISO8601DateFormatter().date(from: "2026-06-01T12:00:00Z")!)
        let w = try await api.wrapped(year)
        #expect(w.tier == "year" && w.flights == 5)
        #expect(w.slides.first?.kind == "intro" && w.slides.last?.kind == "summary")
        #expect(w.slides.first { $0.kind == "map" }?.airports?.isEmpty == false)
        #expect(w.slides.last?.airportCount == 9)
    }

    @Test("Every slide says something, in the website's words")
    func words() throws {
        let w = try JSONDecoder.snake.decode(Wrapped.self, from: recap)
        for s in w.slides {
            let text = SlideText(s, recap: w)
            #expect(!text.value.isEmpty || text.quote != nil, "\(s.kind) says nothing")
        }
        let landing = SlideText(try #require(w.slides.first { $0.kind == "best_landing" }), recap: w)
        #expect(landing.value == "-80" && landing.unit == "fpm" && landing.sub.contains("KPAE → KBFI"))
        let moment = SlideText(try #require(w.slides.first { $0.kind == "moment" }), recap: w)
        #expect(moment.quote?.contains("cleared to") == true && moment.kicker.hasPrefix("STANDOUT MOMENT"))
        #expect(SlideText(try #require(w.slides.first { $0.kind == "totals" }), recap: w).value == "9.4")
    }

    @Test("Weeks start on Monday, in the phone's time zone; the period before is one step back")
    func periods() throws {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "America/Denver")!
        let thursday = ISO8601DateFormatter().date(from: "2026-09-24T18:00:00Z")!
        let week = WrappedPeriod(.week, containing: thursday, calendar: cal)
        #expect(ISO8601DateFormatter().string(from: week.start) == "2026-09-21T06:00:00Z")  // Monday, local midnight
        #expect(week.label == "Week of 21 Sept 2026" || week.label == "Week of 21 Sep 2026")
        #expect(week.tz == -360)
        let august = WrappedPeriod(.month, containing: thursday, step: -1, calendar: cal)
        #expect(august.label == "August 2026")
        #expect(august.query.first { $0.name == "to" }?.value == "2026-09-01T06:00:00Z")
    }
}

extension JSONDecoder {
    static var snake: JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }
}
