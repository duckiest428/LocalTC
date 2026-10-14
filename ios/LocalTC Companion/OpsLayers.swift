import MapKit
import SwiftUI

/// What the desktop's Live Map draws besides the ATC zones, from the EFB message: the SIGMETs, TFRs and restricted
/// areas around (the hazards ATC routes around), the hold ATC gave with its fix and EFC, and each route amendment
/// (a direct, vectors around an area, a deviation).
@MainActor
struct OpsLayers {
    let efb: EfbState
    var weather = true  // the hazards and the NOTAMs (the map's button); ATC's hold and reroutes always
    var airports: [AtcZones.Airport] = []

    static func hazardColor(_ h: Hazard) -> Color {
        switch h.kind {
        case "tfr": return Color(red: 1, green: 0.30, blue: 0.43)
        case "prohibited": return .red
        case "danger": return .orange
        case "restricted": return Color(red: 0.84, green: 0.36, blue: 0.69)
        default:
            switch (h.hazard ?? "").uppercased() {
            case "TS", "CONVECTIVE", "TC": return Color(red: 0.89, green: 0.34, blue: 0.30)
            case "TURB", "MTW": return Color(red: 0.91, green: 0.64, blue: 0.24)
            case "ICE": return Color(red: 0.35, green: 0.66, blue: 0.90)
            default: return .yellow
            }
        }
    }

    @MapContentBuilder var content: some MapContent {
        ForEach(Array((weather ? efb.hazards ?? [] : []).enumerated()), id: \.offset) { _, h in
            if h.polygon.count > 2 {
                let color = Self.hazardColor(h)
                MapPolygon(coordinates: h.polygon.compactMap(point))
                    .foregroundStyle(color.opacity(h.kind == "airmet" ? 0.06 : 0.14))
                    .stroke(color, style: StrokeStyle(lineWidth: 1.6, dash: h.kind == "tfr" || h.kind == "airmet" ? [6, 4] : []))
            }
        }
        // The airports' NOTAMs: a badge with how many are in force (and how many ATC works to), a closed runway red.
        ForEach(weather ? airports : []) { airport in
            let items = (efb.briefings?[airport.icao]?.notams?.items ?? []).filter { $0.inForce != false }
            if !items.isEmpty {
                let atc = items.filter(\.forAtc)
                let closed = Set(atc.flatMap { ($0.worksTo ?? []).filter { $0.first == "runway_closed" }.compactMap { $0.last } })
                ForEach(airport.runways.filter { r in closed.contains { c in Self.sameRunway(c, r.name ?? "") } }, id: \.self) { r in
                    MapPolyline(coordinates: r.ends.compactMap(point))
                        .stroke(.red, style: StrokeStyle(lineWidth: 6, dash: [10, 6]))
                }
                Annotation("", coordinate: CLLocationCoordinate2D(latitude: airport.lat, longitude: airport.lon), anchor: .topLeading) {
                    Text("NOTAM \(items.count)" + (atc.isEmpty ? "" : " · \(atc.count) ATC"))
                        .font(.system(size: 10, weight: .bold))
                        .padding(.horizontal, 5)
                        .background(atc.isEmpty ? Color.yellow.opacity(0.9) : Color.orange, in: Capsule())
                        .foregroundStyle(.black)
                        .offset(x: 8, y: 10)
                }
                .annotationTitles(.hidden)
            }
        }
        if let hold = efb.atc?.hold, hold.active, let track = hold.racetrack, track.count > 2 {
            MapPolyline(coordinates: track.compactMap(point)).stroke(Zone.hold, lineWidth: 2.5)
            Annotation("", coordinate: CLLocationCoordinate2D(latitude: hold.lat, longitude: hold.lon), anchor: .bottom) {
                Text("HOLD \(hold.fix)" + (hold.efc.map { " · EFC \($0)" } ?? ""))
                    .font(.system(size: 10, weight: .bold)).foregroundStyle(Zone.hold)
                    .shadow(color: Color(uiColor: .systemBackground), radius: 2).fixedSize()
            }
            .annotationTitles(.hidden)
        }
        ForEach(Array((efb.atc?.routing ?? []).filter { $0.state == "active" }.enumerated()), id: \.offset) { _, a in
            if let line = a.line, line.count > 1 {
                MapPolyline(coordinates: line.compactMap(point))
                    .stroke(a.kind == "reroute" ? Color.orange : a.kind == "deviation" ? Color.red : Zone.route,
                            style: StrokeStyle(lineWidth: 2.4, dash: [7, 5]))
            }
        }
    }

    /// A NOTAM's runway ("16L/34R") is this runway ("16L/34R"), by either end.
    static func sameRunway(_ notam: String, _ runway: String) -> Bool {
        let a = Set(notam.replacingOccurrences(of: " ", with: "").split(separator: "/").map(String.init))
        let b = Set(runway.replacingOccurrences(of: " ", with: "").split(separator: "/").map(String.init))
        return !a.isDisjoint(with: b)
    }

    private func point(_ p: [Double]) -> CLLocationCoordinate2D? {
        p.count >= 2 ? CLLocationCoordinate2D(latitude: p[0], longitude: p[1]) : nil
    }
}
