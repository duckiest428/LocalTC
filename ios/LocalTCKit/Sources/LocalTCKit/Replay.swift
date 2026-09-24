import Foundation

/// A flight to rewatch: its track, its radio transcript and the moments that matter (docs/replay-format.md,
/// v1). The PC builds it from the flight's recording; the phone gets the ones the pilot uploaded.
/// Times are seconds from the replay's start.
public struct Replay: Codable, Sendable, Equatable {
    public var v: Int
    public var flight: Flight
    public var airports: [String: Airport]
    public var route: [Fix]
    public var track: Track
    public var radio: [Line]
    public var marks: [Mark]

    public struct Flight: Codable, Sendable, Equatable {
        public var id: String
        public var callsign: String
        public var aircraft: String
        public var origin: String
        public var destination: String
        public var departureRunway: String?
        public var arrivalRunway: String?
        public var departureGate: String?
        public var arrivalGate: String?
        public var startedAt: String
        public var zulu0: Double?
        public var durationS: Double
    }

    public struct Airport: Codable, Sendable, Equatable {
        public var lat: Double
        public var lon: Double
        public var elev: Double?
    }

    public struct Fix: Codable, Sendable, Equatable {
        public var ident: String
        public var lat: Double
        public var lon: Double
    }

    /// The aircraft every few seconds, a column per value.
    public struct Track: Codable, Sendable, Equatable {
        public var t: [Double]
        public var lat: [Double]
        public var lon: [Double]
        public var alt: [Double]
        public var gs: [Double]
        public var hdg: [Double]
        public var vs: [Double]
        public var gnd: [Int]
    }

    /// A line of the radio log, as the desktop app showed it.
    public struct Line: Codable, Sendable, Equatable, Identifiable {
        public var kind: String  // atc, pilot, copilot, atis, tuned, alert, phase
        public var t: Double
        public var text: String
        public var station: String?
        public var mhz: Double?
        public var ok: Bool?  // a readback: right or not
        public var readback: String?  // what was wrong with it
        public var unclear: Bool?

        public var id: String { "\(t)-\(kind)-\(text.prefix(24))" }
        public var isCall: Bool { kind == "atc" || kind == "pilot" || kind == "copilot" }
    }

    public struct Mark: Codable, Sendable, Equatable {
        public var t: Double
        public var kind: String  // phase, alert, handoff, takeoff, landing
        public var text: String
    }

    public static func decode(_ data: Data) throws -> Replay {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let replay = try decoder.decode(Replay.self, from: data)
        guard replay.v == 1 else {
            throw APIError(status: 0, message: "This replay is from a newer LocalTC: update the app to play it.")
        }
        return replay
    }

    public var duration: Double { flight.durationS > 0 ? flight.durationS : (track.t.last ?? 0) }
}

/// Where the aircraft was, what was said and what phase it was in, at any moment of a replay; and how the
/// clock moves on when it plays. The same as the website's player (site/replayplayer.js).
public struct ReplayClock: Sendable {
    public static let quietS = 45.0  // skipping quiet stretches: a gap longer than this ...
    public static let leadS = 8.0  // ... jumps to this long before the next call

    public let replay: Replay
    public let calls: [Int]  // indexes of the radio lines that are calls (ATC, the pilot, the copilot)
    let phases: [Replay.Mark]
    let events: [Double]

    public init(_ replay: Replay) {
        self.replay = replay
        calls = replay.radio.indices.filter { replay.radio[$0].isCall }
        phases = replay.marks.filter { $0.kind == "phase" }
        events = (replay.radio.map(\.t) + replay.marks.map(\.t)).sorted()
    }

    public struct Sample: Sendable, Equatable {
        public var index: Int  // the track point at or before
        public var lat: Double
        public var lon: Double
        public var alt: Double
        public var gs: Double
        public var vs: Double
        public var hdg: Double
        public var onGround: Bool
    }

    /// The last index in a sorted list at or before `t` (nil before the first).
    static func before<T>(_ list: [T], _ t: Double, _ key: (T) -> Double) -> Int? {
        var lo = 0, hi = list.count - 1
        var found: Int?
        while lo <= hi {
            let mid = (lo + hi) / 2
            if key(list[mid]) <= t { found = mid; lo = mid + 1 } else { hi = mid - 1 }
        }
        return found
    }

    /// The aircraft at `t`, in between the track's points.
    public func sample(at t: Double) -> Sample {
        let k = replay.track
        let i = Self.before(k.t, t) { $0 } ?? 0
        let j = min(i + 1, k.t.count - 1)
        let span = k.t[j] - k.t[i]
        let f = span > 0 ? min(1, max(0, (t - k.t[i]) / span)) : 0
        func lerp(_ col: [Double]) -> Double { col[i] + (col[j] - col[i]) * f }
        let turn = (k.hdg[j] - k.hdg[i] + 540).truncatingRemainder(dividingBy: 360) - 180
        let hdg = (k.hdg[i] + turn * f + 360).truncatingRemainder(dividingBy: 360)
        return Sample(index: i, lat: lerp(k.lat), lon: lerp(k.lon), alt: lerp(k.alt), gs: lerp(k.gs), vs: lerp(k.vs),
                      hdg: hdg, onGround: k.gnd[f < 0.5 ? i : j] == 1)
    }

    /// The radio line being said (or last said) at `t`.
    public func line(at t: Double) -> Int? {
        Self.before(replay.radio, t) { $0.t }
    }

    public func phase(at t: Double) -> String? {
        Self.before(phases, t) { $0.t }.map { phases[$0].text }
    }

    /// The time of the next call after `t`, or of the previous one before it.
    public func call(after t: Double) -> Double? {
        calls.map { replay.radio[$0].t }.first { $0 > t + 0.05 }
    }

    public func call(before t: Double) -> Double? {
        calls.map { replay.radio[$0].t }.last { $0 < t - 1 }
    }

    /// Where the clock is after `seconds` of playing at `speed`, from `t`. Skipping quiet, a long stretch with
    /// nothing said jumps to a few seconds before the next call.
    public func advance(_ t: Double, by seconds: Double, speed: Double, skipQuiet: Bool) -> Double {
        var next = t + seconds * speed
        if skipQuiet {
            let k = (Self.before(events, t) { $0 } ?? -1) + 1
            let upcoming = k < events.count ? events[k] : replay.duration
            if upcoming - t > Self.quietS { next = max(next, upcoming - Self.leadS) }
        }
        return min(replay.duration, max(0, next))
    }

    /// "+1:02:03" from the start.
    public static func elapsed(_ t: Double) -> String {
        let s = max(0, Int(t.rounded()))
        return String(format: "+%d:%02d:%02d", s / 3600, (s % 3600) / 60, s % 60)
    }

    /// The sim's time of day at `t`, "2241Z", if the replay has it.
    public func zulu(at t: Double) -> String? {
        guard let z0 = replay.flight.zulu0 else { return nil }
        let z = Int(z0 + t) % 86400
        return String(format: "%02d%02dZ", z / 3600, (z % 3600) / 60)
    }
}

/// A line of the account's logbook (GET /v1/flights).
public struct LogbookFlight: Codable, Sendable, Equatable, Identifiable {
    public var id: String
    public var startedAt: String
    public var callsign: String
    public var aircraft: String
    public var origin: String
    public var destination: String
    public var arrivalGate: String?
    public var airMin: Double?
    public var blockMin: Double?
    public var landingVsFpm: Int?
    public var landed: Bool
    public var hasReplay: Bool?

    public var date: Date? { ISO8601DateFormatter().date(from: startedAt) }
}

public struct LogbookPage: Codable, Sendable, Equatable {
    public var flights: [LogbookFlight]
    public var next: String?
}

/// The account's totals (GET /v1/stats).
public struct LogbookStats: Codable, Sendable, Equatable {
    public var flights: Int
    public var airHours: Double
    public var distanceNm: Double
    public var landings: Int
    public var averageLandingFpm: Int?
    public var readbackAccuracy: Double?
}
