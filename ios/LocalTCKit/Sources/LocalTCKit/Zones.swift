import Foundation

/// The ATC layer the desktop's Live Map draws (its ui/zones.py), for the flight as a whole: the centres on the
/// route, the departure and approach areas, the stretch of final, the taxi route ground gave, the gate, each of
/// the flight's airports with its controllers and runways, and for the VFR map each airport's airspace class.
/// Points are [lat, lon]. Every list may be missing (an older or newer desktop): it decodes as empty.
public struct AtcZones: Codable, Sendable, Equatable {
    public var rules: String?
    public var center: String?
    public var tuned: ZoneStation?
    public var next: ZoneStation?
    public var centers: [Area]
    public var terminals: [Area]
    public var final: Final?
    public var taxi: Taxi?
    public var gate: Gate?
    public var airports: [Airport]
    public var classes: [AirspaceClass]

    public struct ZoneStation: Codable, Sendable, Equatable {
        public var station: String?
        public var controller: String?
        public var mhz: Double?

        /// "SoCal Departure 124.300"
        public var label: String {
            [station, mhz.map { String(format: "%.3f", $0) }].compactMap { $0 }.joined(separator: " ")
        }
    }

    /// A centre, or a departure or approach area.
    public struct Area: Codable, Sendable, Equatable, Identifiable {
        public var id: String { key ?? name ?? "" }
        public var key: String?
        public var name: String?
        public var label: [Double]?
        public var rings: [[[Double]]]
        public var active: Bool?  // the aircraft is in it
        public var route: Bool?  // the route passes through it
        public var working: Bool?  // its controller works the flight now
        public var role: String?  // an area's: "departure" or "arrival"

        enum CodingKeys: String, CodingKey { case key = "id", name, label, rings, active, route, working, role }

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            key = try c.decodeIfPresent(String.self, forKey: .key)
            name = try c.decodeIfPresent(String.self, forKey: .name)
            label = try c.decodeIfPresent([Double].self, forKey: .label)
            rings = try c.decodeIfPresent([[[Double]]].self, forKey: .rings) ?? []
            active = try c.decodeIfPresent(Bool.self, forKey: .active)
            route = try c.decodeIfPresent(Bool.self, forKey: .route)
            working = try c.decodeIfPresent(Bool.self, forKey: .working)
            role = try c.decodeIfPresent(String.self, forKey: .role)
        }
    }

    /// Where approach clears the approach and sends the flight to tower.
    public struct Final: Codable, Sendable, Equatable {
        public var runway: String?
        public var ring: [[Double]]
    }

    public struct Taxi: Codable, Sendable, Equatable {
        public var to: String?
        public var points: [[Double]]
        public var taxiways: [String]?
    }

    public struct Gate: Codable, Sendable, Equatable {
        public var icao: String?
        public var name: String?
        public var lat: Double
        public var lon: Double
    }

    public struct Airport: Codable, Sendable, Equatable, Identifiable {
        public var id: String { icao }
        public var icao: String
        public var name: String?
        public var lat: Double
        public var lon: Double
        public var role: String?
        public var towerNm: Double?  // a towered field's control zone, drawn as a circle
        public var stations: [Station]
        public var runways: [Runway]

        enum CodingKeys: String, CodingKey { case icao, name, lat, lon, role, towerNm, stations, runways }

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            icao = try c.decode(String.self, forKey: .icao)
            name = try c.decodeIfPresent(String.self, forKey: .name)
            lat = try c.decode(Double.self, forKey: .lat)
            lon = try c.decode(Double.self, forKey: .lon)
            role = try c.decodeIfPresent(String.self, forKey: .role)
            towerNm = try c.decodeIfPresent(Double.self, forKey: .towerNm)
            stations = try c.decodeIfPresent([Station].self, forKey: .stations) ?? []
            runways = try c.decodeIfPresent([Runway].self, forKey: .runways) ?? []
        }

        public struct Station: Codable, Sendable, Equatable, Hashable {
            public var controller: String?
            public var station: String?
            public var mhz: Double?
            public var tuned: Bool?
            public var next: Bool?
        }

        public struct Runway: Codable, Sendable, Equatable, Hashable {
            public var name: String?
            public var lat: Double
            public var lon: Double
            public var headingTrue: Double
            public var lengthM: Double

            /// Its two ends, [lat, lon] each: a line on the map.
            public var ends: [[Double]] {
                let half = lengthM / 2, h = headingTrue * .pi / 180
                let dLat = half * cos(h) / 111_320, dLon = half * sin(h) / (111_320 * cos(lat * .pi / 180))
                return [[lat - dLat, lon - dLon], [lat + dLat, lon + dLon]]
            }
        }

        /// The controller badges as the desktop shows them: D (clearance), G, T, A (departure or approach),
        /// each once, marked when the flight is tuned to it or should call it next.
        public var badges: [Badge] {
            var out: [Badge] = []
            for s in stations {
                guard let letter = Badge.letter(for: s.controller), !out.contains(where: { $0.letter == letter }) else { continue }
                let same = stations.filter { Badge.letter(for: $0.controller) == letter }
                out.append(Badge(letter: letter, tuned: same.contains { $0.tuned == true }, next: same.contains { $0.next == true }))
            }
            return out
        }

        public struct Badge: Sendable, Equatable, Hashable {
            public var letter: String
            public var tuned: Bool
            public var next: Bool

            public init(letter: String, tuned: Bool, next: Bool) {
                self.letter = letter
                self.tuned = tuned
                self.next = next
            }

            static func letter(for controller: String?) -> String? {
                switch controller {
                case "clearance": "D"
                case "ground": "G"
                case "tower": "T"
                case "departure", "approach": "A"
                default: nil
                }
            }
        }
    }

    /// The VFR map: an airport's airspace class and its rings, each with a floor and ceiling in feet MSL.
    public struct AirspaceClass: Codable, Sendable, Equatable, Identifiable {
        public var id: String { icao }
        public var icao: String
        public var name: String?
        public var lat: Double
        public var lon: Double
        public var `class`: String?  // B, C, D, CTR, ATZ, or nothing (no tower)
        public var label: String?
        public var towered: Bool?
        public var rings: [Ring]

        enum CodingKeys: String, CodingKey { case icao, name, lat, lon, `class`, label, towered, rings }

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            icao = try c.decode(String.self, forKey: .icao)
            name = try c.decodeIfPresent(String.self, forKey: .name)
            lat = try c.decode(Double.self, forKey: .lat)
            lon = try c.decode(Double.self, forKey: .lon)
            `class` = try c.decodeIfPresent(String.self, forKey: .class)
            label = try c.decodeIfPresent(String.self, forKey: .label)
            towered = try c.decodeIfPresent(Bool.self, forKey: .towered)
            rings = try c.decodeIfPresent([Ring].self, forKey: .rings) ?? []
        }

        public struct Ring: Codable, Sendable, Equatable, Hashable {
            public var nm: Double
            public var floor: Double?
            public var ceiling: Double?

            /// "100" over "SFC": hundreds of feet, as a VFR chart prints a ring's ceiling and floor.
            public var ceilingLabel: String { Self.hundreds(ceiling) }
            public var floorLabel: String { Self.hundreds(floor) }

            static func hundreds(_ ft: Double?) -> String {
                guard let ft, ft > 0 else { return "SFC" }
                return String(Int((ft / 100).rounded()))
            }
        }

        /// Where a ring's label goes: in its band, southeast of the field, as the desktop places it. [lat, lon].
        public func labelPoint(ring i: Int) -> [Double] {
            guard rings.indices.contains(i) else { return [lat, lon] }
            let nm = i == 0 ? rings[0].nm * 0.62 : (rings[i - 1].nm + rings[i].nm) / 2
            let d = nm / 60 * 0.5.squareRoot()
            return [lat - d, lon + d / cos(lat * .pi / 180)]
        }
    }

    enum CodingKeys: String, CodingKey { case rules, center, tuned, next, centers, terminals, final, taxi, gate, airports, classes }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        rules = try c.decodeIfPresent(String.self, forKey: .rules)
        center = try c.decodeIfPresent(String.self, forKey: .center)
        tuned = try c.decodeIfPresent(ZoneStation.self, forKey: .tuned)
        next = try c.decodeIfPresent(ZoneStation.self, forKey: .next)
        centers = try c.decodeIfPresent([Area].self, forKey: .centers) ?? []
        terminals = try c.decodeIfPresent([Area].self, forKey: .terminals) ?? []
        final = try c.decodeIfPresent(Final.self, forKey: .final)
        taxi = try c.decodeIfPresent(Taxi.self, forKey: .taxi)
        gate = try c.decodeIfPresent(Gate.self, forKey: .gate)
        airports = try c.decodeIfPresent([Airport].self, forKey: .airports) ?? []
        classes = try c.decodeIfPresent([AirspaceClass].self, forKey: .classes) ?? []
    }
}

/// An outline drawn the right way across the date line. The desktop sends -180...180, so an area across it (Anchorage
/// over the Aleutians, the Pacific oceanic ones) jumps from 179°E to 179°W, and a map draws that edge the long way, right
/// across the world. Here it's joined up, then cut at the date line into the piece on each side.
public func antimeridianPieces(_ ring: [[Double]]) -> [[[Double]]] {
    var joined: [[Double]] = []
    for p in ring where p.count >= 2 {
        guard let last = joined.last else { joined.append([p[0], p[1]]); continue }
        joined.append([p[0], p[1] + 360 * ((last[1] - p[1]) / 360).rounded()])
    }
    guard let low = joined.map({ $0[1] }).min(), let high = joined.map({ $0[1] }).max() else { return [] }
    if low >= -180 && high <= 180 { return [joined] }
    let shift: Double = high > 180 ? -360 : 360
    let here = clip(joined, at: high > 180 ? 180 : -180, keepBelow: high > 180)
    let there = clip(joined.map { [$0[0], $0[1] + shift] }, at: high > 180 ? -180 : 180, keepBelow: high <= 180)
    return [here, there].filter { $0.count >= 3 }
}

/// Sutherland-Hodgman against the meridian ``lon``: the part of the ring west of it (``keepBelow``) or east of it.
private func clip(_ ring: [[Double]], at lon: Double, keepBelow: Bool) -> [[Double]] {
    func inside(_ p: [Double]) -> Bool { keepBelow ? p[1] <= lon : p[1] >= lon }
    func cross(_ a: [Double], _ b: [Double]) -> [Double] {
        let f = (lon - a[1]) / (b[1] - a[1])
        return [a[0] + (b[0] - a[0]) * f, lon]
    }
    var out: [[Double]] = []
    for (i, b) in ring.enumerated() {
        let a = ring[(i + ring.count - 1) % ring.count]
        if inside(b) {
            if !inside(a) { out.append(cross(a, b)) }
            out.append(b)
        } else if inside(a) {
            out.append(cross(a, b))
        }
    }
    return out
}
