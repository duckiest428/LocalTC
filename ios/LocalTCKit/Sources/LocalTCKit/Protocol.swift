import Foundation

// The companion protocol, v1 (docs/companion-protocol.md): what LocalTC on the PC sends about a flight.
// Every field is optional except where the desktop always sends it, so an older or newer LocalTC never
// breaks the app.

public struct Station: Codable, Sendable, Equatable {
    public var station: String?
    public var mhz: Double?

    public init(station: String? = nil, mhz: Double? = nil) {
        self.station = station
        self.mhz = mhz
    }

    /// "Albuquerque Center 133.650"
    public var label: String {
        [station, mhz.map { String(format: "%.3f", $0) }].compactMap { $0 }.joined(separator: " ")
    }
}

public struct ETE: Codable, Sendable, Equatable {
    public var nm: Int
    public var min: Int
}

public struct LastATC: Codable, Sendable, Equatable {
    public var station: String?
    public var mhz: Double?
    public var text: String?
}

public struct FlightStatus: Codable, Sendable, Equatable {
    public var active: Bool
    public var callsign: String?
    public var aircraft: String?
    public var origin: String?
    public var destination: String?
    public var phase: String?
    public var phaseLabel: String?
    public var squawk: String?
    public var altitudeFt: Int?
    public var runway: String?
    public var gate: String?
    public var rules: String?  // "IFR" or "VFR": which map the phone opens on
    public var tuned: Station?
    public var next: Station?
    public var ete: ETE?
    public var lastAtc: LastATC?
    public var updatedAt: String?

    public init(active: Bool = false) { self.active = active }
}

public struct OwnAircraft: Codable, Sendable, Equatable {
    public var t: Double?
    public var lat: Double
    public var lon: Double
    public var alt: Int?
    public var agl: Int?
    public var hdg: Int?
    public var gs: Int?
    public var vs: Int?
    public var ground: Bool?
    public var com1: Double?
    public var com2: Double?
    public var squawk: String?
}

public struct TrafficTarget: Codable, Sendable, Equatable, Identifiable {
    public var id: Int
    public var callsign: String?
    public var type: String?
    public var lat: Double
    public var lon: Double
    public var alt: Int?
    public var hdg: Int?
    public var gs: Int?
    public var ground: Bool?
}

public struct RouteFix: Codable, Sendable, Equatable {
    public var ident: String
    public var lat: Double
    public var lon: Double
}

public struct Route: Codable, Sendable, Equatable {
    public var origin: String?
    public var destination: String?
    public var fixes: [RouteFix]
}

public struct RadioLine: Codable, Sendable, Equatable, Identifiable {
    public var id = UUID()
    public var kind: String
    public var t: Double?
    public var station: String?
    public var mhz: Double?
    public var text: String?
    public var ok: Bool?
    public var level: String?
    public var radio: Int?  // 1 or 2: which COM, when the desktop knows

    enum CodingKeys: String, CodingKey { case kind, t, station, mhz, text, ok, level, radio }

    public init(kind: String, text: String?, station: String? = nil, mhz: Double? = nil) {
        self.kind = kind
        self.text = text
        self.station = station
        self.mhz = mhz
    }

    /// Who spoke, for styling: ATC, the pilot (or copilot), somebody else on the frequency, or LocalTC itself.
    public var speaker: Speaker {
        switch kind {
        case "atc", "atis": .atc
        case "pilot", "copilot": .pilot
        case "chatter": .other
        default: .system
        }
    }

    public enum Speaker: Sendable { case atc, pilot, other, system }
}

public struct FlightAlert: Codable, Sendable, Equatable, Identifiable {
    public var id = UUID()
    public var kind: Kind
    public var title: String
    public var body: String
    public var mhz: Double?

    enum CodingKeys: String, CodingKey { case kind, title, body, mhz }

    public enum Kind: String, Codable, Sendable { case handoff, clearance, traffic, emergency }

    public init(kind: Kind, title: String, body: String, mhz: Double? = nil) {
        self.kind = kind
        self.title = title
        self.body = body
        self.mhz = mhz
    }
}

/// One of the flight's airports (origin, destination) for the Frequencies and Airports tabs: published data.
public struct FlightAirport: Codable, Sendable, Equatable, Identifiable {
    public var id: String { icao }
    public var icao: String
    public var name: String?
    public var role: String?  // "departure" or "arrival"
    public var lat: Double?
    public var lon: Double?
    public var elevFt: Int?
    public var atis: String?
    public var frequencies: [Frequency]
    public var runways: [Runway]

    enum CodingKeys: String, CodingKey { case icao, name, role, lat, lon, elevFt, atis, frequencies, runways }

    public struct Frequency: Codable, Sendable, Equatable, Hashable {
        public var label: String?
        public var kind: String?
        public var mhz: Double
        public var name: String?

        public var display: String { String(format: "%.3f", mhz) }
    }

    public struct Runway: Codable, Sendable, Equatable, Hashable {
        public var name: String
        public var lengthFt: Int?
        public var headingMag: Int?
        public var ils: [String]?
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        icao = try c.decode(String.self, forKey: .icao)
        name = try c.decodeIfPresent(String.self, forKey: .name)
        role = try c.decodeIfPresent(String.self, forKey: .role)
        lat = try c.decodeIfPresent(Double.self, forKey: .lat)
        lon = try c.decodeIfPresent(Double.self, forKey: .lon)
        elevFt = try c.decodeIfPresent(Int.self, forKey: .elevFt)
        atis = try c.decodeIfPresent(String.self, forKey: .atis)
        frequencies = try c.decodeIfPresent([Frequency].self, forKey: .frequencies) ?? []
        runways = try c.decodeIfPresent([Runway].self, forKey: .runways) ?? []
    }
}

public struct Hello: Codable, Sendable, Equatable {
    public var `protocol`: Int?
    public var version: String?
    public var status: FlightStatus?
    public var own: OwnAircraft?
    public var traffic: [TrafficTarget]?
    public var route: Route?
    public var radio: [RadioLine]?
    public var airports: [FlightAirport]?
}

public enum LiveMessage: Sendable, Equatable {
    case hello(Hello)
    case status(FlightStatus)
    case own(OwnAircraft)
    case traffic([TrafficTarget])
    case route(Route?)
    case radio(RadioLine)
    case radioBacklog([RadioLine])
    case alert(FlightAlert)
    case airports([FlightAirport])

    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    /// One message from its type and its JSON. Unknown types (a newer LocalTC) are skipped: nil.
    public static func decode(type: String, data: Data) throws -> LiveMessage? {
        let d = decoder
        switch type {
        case "hello": return .hello(try d.decode(Hello.self, from: data))
        case "status": return .status(try d.decode(FlightStatus.self, from: data))
        case "own": return .own(try d.decode(OwnAircraft.self, from: data))
        case "traffic": return .traffic(try d.decode([TrafficTarget].self, from: data))
        case "route": return .route(try d.decode(Route?.self, from: data))
        case "radio":
            // The relay sends one line; the desktop's direct stream too. A list (a backlog) is accepted as well.
            if let line = try? d.decode(RadioLine.self, from: data) { return .radio(line) }
            return .radioBacklog(try d.decode([RadioLine].self, from: data))
        case "alert": return .alert(try d.decode(FlightAlert.self, from: data))
        case "airports": return .airports(try d.decode([FlightAirport].self, from: data))
        default: return nil
        }
    }

    /// A relay WebSocket frame: {"type": ..., "data": ...}.
    public static func decodeEnvelope(_ text: String) throws -> LiveMessage? {
        guard let object = try JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any],
              let type = object["type"] as? String else { return nil }
        let payload = object["data"] ?? NSNull()
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.fragmentsAllowed])
        return try decode(type: type, data: data)
    }
}

/// LocalTC's Server-Sent Events, line by line. The desktop writes one `data:` line per event, and
/// `URLSession.bytes(...).lines` drops the blank line between events, so an event is complete at its data.
public struct SSELineDecoder: Sendable {
    private var event = "message"

    public init() {}

    /// The (event, data) a line completes, if any.
    public mutating func feed(_ line: Substring) -> (event: String, data: String)? {
        if line.hasPrefix(":") || line.isEmpty { return nil }  // a keep-alive comment, or a separator
        if line.hasPrefix("event:") {
            event = line.dropFirst(6).trimmingCharacters(in: .whitespaces)
            return nil
        }
        if line.hasPrefix("data:") {
            defer { event = "message" }
            return (event, line.dropFirst(5).trimmingCharacters(in: .whitespaces))
        }
        return nil
    }
}
