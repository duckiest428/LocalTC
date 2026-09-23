import Foundation
import Observation

/// The flight as the phone shows it, built from the live messages.
@MainActor @Observable
public final class FlightStore {
    public static let radioKeep = 200
    public static let trailKeep = 600

    public private(set) var status = FlightStatus()
    public private(set) var own: OwnAircraft?
    public private(set) var trail: [Coordinate] = []
    public private(set) var traffic: [Int: TrafficTarget] = [:]
    public private(set) var route: Route?
    public private(set) var radio: [RadioLine] = []
    public private(set) var airports: [FlightAirport] = []
    /// Banners waiting to be shown, oldest first. The app removes each as it shows it.
    public var alerts: [FlightAlert] = []
    public private(set) var lastMessage: Date?

    public struct Coordinate: Sendable, Equatable {
        public var lat: Double
        public var lon: Double
    }

    public init() {}

    public func apply(_ message: LiveMessage, at now: Date = .now) {
        lastMessage = now
        switch message {
        case .hello(let hello):
            if let s = hello.status { status = s }
            if let o = hello.own { setOwn(o) }
            if let t = hello.traffic { setTraffic(t) }
            if let r = hello.route { route = r }
            if let lines = hello.radio { radio = Array(lines.suffix(Self.radioKeep)) }
            if let a = hello.airports { airports = a }
        case .status(let s):
            if !s.active && status.active { trail = []; airports = [] }  // the flight ended: the next one starts clean
            status = s
        case .own(let o): setOwn(o)
        case .traffic(let t): setTraffic(t)
        case .route(let r): route = r
        case .radio(let line): append([line])
        case .radioBacklog(let lines):
            // A catch-up after reconnecting: only what isn't already shown.
            let known = Set(radio.map { "\($0.t ?? -1)|\($0.text ?? "")" })
            append(lines.filter { !known.contains("\($0.t ?? -1)|\($0.text ?? "")") })
        case .alert(let a): alerts.append(a)
        case .airports(let a): airports = a
        }
    }

    public func reset() {
        status = FlightStatus()
        own = nil
        trail = []
        traffic = [:]
        route = nil
        radio = []
        airports = []
        alerts = []
    }

    /// Which COM a radio line was on: its own mark, else the frequency against COM1 and COM2 (1 by default).
    public func com(of line: RadioLine) -> Int {
        if let radio = line.radio { return radio }
        if let mhz = line.mhz, let com2 = own?.com2, abs(mhz - com2) < 0.003, abs(mhz - (own?.com1 ?? 0)) >= 0.003 { return 2 }
        return 1
    }

    private func setOwn(_ o: OwnAircraft) {
        own = o
        let point = Coordinate(lat: o.lat, lon: o.lon)
        if trail.last != point {
            trail.append(point)
            if trail.count > Self.trailKeep { trail.removeFirst(trail.count - Self.trailKeep) }
        }
    }

    private func setTraffic(_ list: [TrafficTarget]) {
        traffic = Dictionary(list.map { ($0.id, $0) }, uniquingKeysWith: { _, last in last })
    }

    private func append(_ lines: [RadioLine]) {
        radio.append(contentsOf: lines)
        if radio.count > Self.radioKeep { radio.removeFirst(radio.count - Self.radioKeep) }
    }
}
