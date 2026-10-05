import Foundation
import Observation

/// The flight as the phone shows it, built from the live messages.
@MainActor @Observable
public final class FlightStore {
    public static let radioKeep = 200
    /// The path flown: a point every ~200 m in the air (``trailStep`` degrees) and every ~10 m on the ground
    /// (``trailStepGround``), so the taxi from the gate keeps its turns. Past ``trailKeep`` points it's simplified
    /// (``thinned``): straight legs lose their points, the turns and the taxi keep theirs. (Every other point dropped
    /// lost the taxi a few hours in; cutting the oldest off moved the start of the line along behind the aircraft.)
    public static let trailKeep = 2000
    public static let trailStep = 0.002
    public static let trailStepGround = 0.0001
    private var trailGround = false

    public private(set) var status = FlightStatus()
    public private(set) var own: OwnAircraft?
    public private(set) var trail: [Coordinate] = []
    public private(set) var traffic: [Int: TrafficTarget] = [:]
    public private(set) var route: Route?
    public private(set) var zones: AtcZones?
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
            if let t = hello.trail { setTrail(t) }
            if let o = hello.own { setOwn(o) }
            if let t = hello.traffic { setTraffic(t) }
            if let r = hello.route { route = r }
            zones = hello.zones
            if let lines = hello.radio { radio = Array(lines.suffix(Self.radioKeep)) }
            if let a = hello.airports { airports = a }
        case .status(let s):
            if !s.active && status.active { trail = []; airports = []; zones = nil }  // the flight ended: the next one starts clean
            status = s
        case .own(let o): setOwn(o)
        case .traffic(let t): setTraffic(t)
        case .route(let r): route = r
        case .zones(let z): zones = z
        case .radio(let line): append([line])
        case .radioBacklog(let lines):
            // A catch-up after reconnecting: only what isn't already shown.
            let known = Set(radio.map { "\($0.t ?? -1)|\($0.text ?? "")" })
            append(lines.filter { !known.contains("\($0.t ?? -1)|\($0.text ?? "")") })
        case .alert(let a): alerts.append(a)
        case .airports(let a): airports = a
        case .trail(let t):
            setTrail(t)
            if let o = own { setOwn(o) }
        }
    }

    public func reset() {
        status = FlightStatus()
        own = nil
        trail = []
        traffic = [:]
        route = nil
        zones = nil
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
        let ground = o.ground ?? false
        // Lifting off or touching down is always a point.
        if let last = trail.last, ground == trailGround,
           abs(last.lat - point.lat) + abs(last.lon - point.lon) < (ground ? Self.trailStepGround : Self.trailStep) { return }
        trailGround = ground
        trail.append(point)
        trail = Self.thinned(trail)
    }

    /// The whole path in at most ``trailKeep`` points with its shape: Douglas-Peucker, the tolerance (from ~3 m)
    /// doubled until it fits in three quarters of that. The first and last points always stay.
    static func thinned(_ path: [Coordinate]) -> [Coordinate] {
        guard path.count > trailKeep else { return path }
        var out = path
        var tolerance = 0.00003
        while out.count > trailKeep * 3 / 4, out.count > 2 {
            out = douglasPeucker(out, tolerance)
            tolerance *= 2
        }
        return out
    }

    private static func douglasPeucker(_ path: [Coordinate], _ tolerance: Double) -> [Coordinate] {
        var keep = [Bool](repeating: false, count: path.count)
        keep[0] = true
        keep[path.count - 1] = true
        var stack = [(0, path.count - 1)]
        while let (a, b) = stack.popLast() {
            guard b > a + 1 else { continue }
            let k = cos(path[a].lat * .pi / 180)  // a degree of longitude is shorter away from the equator
            let ax = path[a].lon * k, ay = path[a].lat
            let dx = path[b].lon * k - ax, dy = path[b].lat - ay
            let len2 = dx * dx + dy * dy
            var worst = -1.0, at = a
            for i in (a + 1)..<b {
                let px = path[i].lon * k - ax, py = path[i].lat - ay
                let u = len2 == 0 ? 0 : max(0, min(1, (px * dx + py * dy) / len2))
                let d = hypot(px - u * dx, py - u * dy)
                if d > worst { worst = d; at = i }
            }
            if worst > tolerance {
                keep[at] = true
                stack.append((a, at))
                stack.append((at, b))
            }
        }
        return zip(path, keep).compactMap { $1 ? $0 : nil }
    }

    /// The path flown before this phone started watching: it replaces the one drawn so far (it runs up to now).
    private func setTrail(_ points: [[Double]]) {
        let path = points.compactMap { $0.count == 2 ? Coordinate(lat: $0[0], lon: $0[1]) : nil }
        guard !path.isEmpty else { return }
        trail = Self.thinned(path)
    }

    private func setTraffic(_ list: [TrafficTarget]) {
        traffic = Dictionary(list.map { ($0.id, $0) }, uniquingKeysWith: { _, last in last })
    }

    private func append(_ lines: [RadioLine]) {
        radio.append(contentsOf: lines)
        if radio.count > Self.radioKeep { radio.removeFirst(radio.count - Self.radioKeep) }
    }
}
