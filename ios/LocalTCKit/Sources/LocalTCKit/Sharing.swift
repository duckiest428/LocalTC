import Foundation

// Sharing a flight (a public card at localtc.tech/f/<slug>) and ATC Wrapped: the account server's
// /v1/shares, /v1/flights/:id/moments and /v1/wrapped (server/src/shares.ts, wrapped.ts). The card itself is
// drawn by the website's own sharecard.js, in a web view: one picture everywhere.

/// A line from the radio worth quoting on a card (site/cardmodel.js `moments`).
public struct Moment: Codable, Sendable, Equatable, Hashable {
    public var kind: String
    public var label: String
    public var t: Double?
    public var station: String
    public var mhz: Double?
    public var text: String
    public var score: Double?
}

/// A flight's moments, and its airports' names, from its uploaded replay.
public struct Moments: Codable, Sendable, Equatable {
    public var moments: [Moment]
    public var names: [String: String]
}

/// A share made: its link, and the snapshot the public sees (for drawing its picture).
public struct Share: Sendable {
    public var slug: String
    public var url: URL
    public var card: Data  // the snapshot, JSON: handed to sharecard.js as it is
}

// MARK: - Wrapped

public struct WrappedAirport: Codable, Sendable, Equatable, Hashable {
    public var icao: String
    public var visits: Int
    public var lat: Double?
    public var lon: Double?
    public var name: String?
}

public struct WrappedRoute: Codable, Sendable, Equatable, Hashable {
    public var origin: String
    public var destination: String
    public var flights: Int
}

/// A flight as a slide names it: the route, the day, the numbers.
public struct WrappedFlight: Codable, Sendable, Equatable, Hashable {
    public var id: String
    public var origin: String
    public var destination: String
    public var date: String
    public var callsign: String?
    public var aircraft: String?
    public var airMin: Double?
    public var distanceNm: Double?
}

/// One slide. Which fields it has depends on its `kind` (server/src/wrapped.ts).
public struct WrappedSlide: Codable, Sendable, Equatable, Hashable {
    public var kind: String
    public var label: String?
    public var period: String?
    public var flights: Int?
    public var hours: Double?
    public var blockHours: Double?
    public var landings: Int?
    public var hoursFraming: String?
    public var distanceNm: Double?
    public var framing: String?
    public var airports: [WrappedAirport]?
    public var routes: [WrappedRoute]?
    public var month: String?
    public var icao: String?
    public var name: String?
    public var visits: Int?
    public var origin: String?
    public var destination: String?
    public var count: Int?
    public var icaos: [String]?
    public var aircraft: String?
    public var types: Int?
    public var airMin: Double?
    public var date: String?
    public var fpm: Int?
    public var greasers: Int?
    public var flight: WrappedFlight?
    public var days: Int?
    public var from: String?
    public var to: String?
    public var accuracy: Double?
    public var readbacks: Int?
    public var previous: Double?
    public var moment: String?
    public var station: String?
    public var mhz: Double?
    public var text: String?
    public var first: WrappedFlight?
    public var last: WrappedFlight?
    public var blurb: String?
    public var airportCount: Int?
    public var topAirport: String?
    public var bestLandingFpm: Int?
    public var persona: String?
}

public struct WrappedLastFlight: Codable, Sendable, Equatable {
    public var id: String
    public var startedAt: String
    public var origin: String
    public var destination: String
    public var daysAgo: Int
}

/// A recap (GET /v1/wrapped). `tier` is how it's told: a month with one flight is a week's card.
public struct Wrapped: Codable, Sendable, Equatable {
    public var period: String
    public var tier: String
    public var label: String
    public var flights: Int
    public var hours: Double
    public var distanceNm: Double
    public var airports: [WrappedAirport]
    public var routes: [WrappedRoute]
    public var slides: [WrappedSlide]
    public var lastFlight: WrappedLastFlight?
}

/// A week, a month or a year, in the phone's own time zone: what the server is asked for.
public struct WrappedPeriod: Sendable, Equatable {
    public enum Kind: String, Sendable, CaseIterable { case week, month, year }

    public var kind: Kind
    public var start: Date
    public var end: Date
    public var tz: Int  // minutes east of UTC at the start
    public var label: String

    /// The period containing `date`, moved by `step` periods (-1: the one before).
    public init(_ kind: Kind, containing date: Date = Date(), step: Int = 0, calendar: Calendar = .current) {
        var cal = calendar
        cal.firstWeekday = 2  // weeks start on Monday, as the website's do
        let unit: Calendar.Component = kind == .week ? .weekOfYear : kind == .month ? .month : .year
        let interval = cal.dateInterval(of: unit, for: date)!
        let start = cal.date(byAdding: unit, value: step, to: interval.start)!
        self.kind = kind
        self.start = start
        self.end = cal.date(byAdding: unit, value: 1, to: start)!
        self.tz = cal.timeZone.secondsFromGMT(for: start) / 60
        let names = DateFormatter()
        names.locale = Locale(identifier: "en_GB")
        names.timeZone = cal.timeZone
        switch kind {
        case .week:
            names.dateFormat = "d MMM yyyy"
            label = "Week of \(names.string(from: start))"
        case .month:
            names.dateFormat = "LLLL yyyy"
            label = names.string(from: start)
        case .year:
            label = String(cal.component(.year, from: start))
        }
    }

    public var isCurrent: Bool { Date() >= start && Date() < end }

    var query: [URLQueryItem] {
        let iso = ISO8601DateFormatter()
        return [URLQueryItem(name: "period", value: kind.rawValue), URLQueryItem(name: "from", value: iso.string(from: start)),
                URLQueryItem(name: "to", value: iso.string(from: end)), URLQueryItem(name: "tz", value: String(tz)),
                URLQueryItem(name: "label", value: label)]
    }
}

/// What a slide says, in words: the same as the website's (site/wrapped.js `slideCard`).
public struct SlideText: Sendable, Equatable {
    public var kicker = "ATC WRAPPED"
    public var value = ""
    public var unit = ""
    public var label = ""
    public var sub = ""
    public var quote: String?
    public var station: String?

    public init(_ s: WrappedSlide, recap: Wrapped) {
        func num(_ x: Double?) -> String { Int((x ?? 0).rounded()).formatted(.number.locale(Locale(identifier: "en_US"))) }
        func hours(_ h: Double?) -> String { (h ?? 0) < 100 ? String(format: "%.1f", h ?? 0).replacingOccurrences(of: ".0", with: "") : num(h) }
        func route(_ f: WrappedFlight?) -> String { f.map { "\($0.origin) → \($0.destination)" } ?? "" }
        func day(_ iso: String?) -> String {
            guard let iso, let d = ISO8601DateFormatter().date(from: "\(iso.prefix(10))T12:00:00Z") else { return "" }
            let f = DateFormatter()
            f.locale = Locale(identifier: "en_GB")
            f.timeZone = TimeZone(identifier: "UTC")
            f.dateFormat = "d MMM"
            return f.string(from: d)
        }
        func duration(_ min: Double?) -> String {
            let m = Int((min ?? 0).rounded())
            return m >= 60 ? String(format: "%dh %02dm", m / 60, m % 60) : "\(m)m"
        }
        let months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
        switch s.kind {
        case "intro":
            value = recap.label; label = "\(s.flights ?? 0) flights. Let's look back."
        case "totals":
            value = hours(s.hours); unit = "hours"; label = "in the air"
            sub = "\(s.flights ?? 0) flights, \(s.landings ?? 0) landings." + (s.hoursFraming.map { $0.isEmpty ? "" : " \($0)." } ?? "")
        case "distance":
            value = num(s.distanceNm); unit = "nm"; label = "flown"; sub = s.framing ?? ""
        case "map":
            let a = s.airports?.count ?? 0, r = s.routes?.count ?? 0
            value = "\(a)"; unit = a == 1 ? "airport" : "airports"; label = "\(r) \(r == 1 ? "route" : "routes") between them"
        case "busiest_month":
            let parts = (s.month ?? "").split(separator: "-")
            let name = parts.count == 2 ? months[max(0, min(11, (Int(parts[1]) ?? 1) - 1))] : ""
            value = name; label = "was your busiest month"; sub = "\(s.flights ?? 0) flights in \(name) \(parts.first ?? "")."
        case "top_airport":
            value = s.icao ?? ""; label = (s.name?.isEmpty == false) ? "\(s.name!): your most visited airport" : "your most visited airport"
            sub = "\(s.visits ?? 0) visits."
        case "top_route":
            value = "\(s.origin ?? "") → \(s.destination ?? "")"; label = "your most flown route"; sub = "\(s.flights ?? 0) times."
        case "new_airports":
            value = num(Double(s.count ?? 0)); unit = s.count == 1 ? "new airport" : "new airports"
            label = "you'd never flown to before"; sub = (s.icaos ?? []).joined(separator: "  ")
        case "aircraft":
            value = s.aircraft ?? ""; label = "your ride"
            sub = "\(s.flights ?? 0) flights in it" + ((s.types ?? 0) > 1 ? ", of \(s.types!) types flown." : ".")
        case "longest":
            value = duration(s.airMin); label = "your longest flight"
            sub = "\(s.origin ?? "") → \(s.destination ?? ""), \(num(s.distanceNm)) nm, \(day(s.date))."
        case "best_landing":
            value = "\(s.fpm ?? 0)"; unit = "fpm"; label = "your softest landing"
            sub = "\(route(s.flight)), \(day(s.flight?.date)). \(s.greasers ?? 0) of \(s.landings ?? 0) landings softer than 150 fpm."
        case "streak":
            value = "\(s.days ?? 0)"; unit = "days"; label = "flying in a row"; sub = "\(day(s.from)) to \(day(s.to))."
        case "radio":
            value = "\(Int((s.accuracy ?? 0).rounded()))%"; label = "of your readbacks right"
            if let p = s.previous {
                sub = "\((s.accuracy ?? 0) >= p ? "Up" : "Down") from \(Int(p.rounded()))% the time before."
            } else {
                sub = "\(s.readbacks ?? 0) readbacks."
            }
        case "moment":
            kicker = "STANDOUT MOMENT: \((s.label ?? "").uppercased())"; quote = s.text
            station = [s.station, s.mhz.map { String(format: "%.3f", $0) }].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
            sub = s.flight.map { "\(route($0)), \(day($0.date))" } ?? ""
        case "first_last":
            value = route(s.first); label = "is where it started"; sub = "And it ended with \(route(s.last)), \(day(s.last?.date))."
        case "persona":
            kicker = "YOUR PILOT TYPE"; value = s.name ?? ""; label = s.blurb ?? ""
        case "summary":
            value = recap.label; label = s.persona ?? "\(s.flights ?? 0) flights"
            sub = "\(hours(s.hours)) hours · \(num(s.distanceNm)) nm · \(s.airportCount ?? 0) airports"
        default:
            value = s.kind
        }
    }
}

// MARK: - The calls

extension APIClient {
    /// A flight's lines worth quoting, from its uploaded replay (none without one).
    public func moments(id: String) async throws -> Moments {
        try decoder.decode(Moments.self, from: try await call("GET", "/v1/flights/\(id)/moments"))
    }

    /// Make (or change) a flight's public card: the quote the pilot picked, the airports' names.
    public func shareFlight(id: String, quote: Moment?, names: [String: String]) async throws -> Share {
        var body: [String: Any] = ["kind": "flight", "ref": id, "names": names]
        if let quote {
            body["quote"] = ["kind": quote.kind, "station": quote.station, "text": quote.text, "mhz": quote.mhz as Any].compactMapValues { $0 is NSNull ? nil : $0 }
        }
        return try share(try await send("POST", "/v1/shares", json: body))
    }

    /// Share a Wrapped period's summary card.
    public func shareWrapped(_ period: WrappedPeriod) async throws -> Share {
        var body: [String: Any] = [:]
        for item in period.query { body[item.name] = item.value }
        return try share(try await send("POST", "/v1/shares", json: body.merging(["kind": "wrapped"]) { $1 }))
    }

    /// The card's picture, drawn on the phone: what a chat app shows for the link.
    public func uploadShareImage(slug: String, png: Data) async throws {
        _ = try await send("PUT", "/v1/shares/\(slug)/image", raw: png, contentType: "image/png")
    }

    public func unshare(slug: String) async throws {
        _ = try await call("DELETE", "/v1/shares/\(slug)")
    }

    public func wrapped(_ period: WrappedPeriod) async throws -> Wrapped {
        try decodeWrapped(try await wrappedJSON(period))
    }

    /// The recap as the server sent it: the web view draws slides from this, the app from `decodeWrapped`.
    public func wrappedJSON(_ period: WrappedPeriod) async throws -> Data {
        try await call("GET", "/v1/wrapped", query: period.query)
    }

    public func decodeWrapped(_ data: Data) throws -> Wrapped {
        try decoder.decode(Wrapped.self, from: data)
    }

    private func share(_ data: Data) throws -> Share {
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let slug = object["slug"] as? String, let link = object["url"] as? String, let url = URL(string: link),
              let card = object["card"] else { throw APIError(status: 502, message: "The server's answer wasn't a share.") }
        return Share(slug: slug, url: url, card: try JSONSerialization.data(withJSONObject: card))
    }
}
