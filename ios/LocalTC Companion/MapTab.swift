import MapKit
import SwiftUI

struct MapTab: View {
    @Environment(AppModel.self) private var model
    @State private var position: MapCameraPosition = .automatic
    @State private var follow = true
    @State private var mapMode = MapMode()  // IFR or VFR: follows the flight's rules, switchable any time
    @AppStorage("mapZones") private var showZones = true  // the ATC zones, as on the desktop's Live Map
    @State private var legend = false

    var body: some View {
        let store = model.store
        Map(position: $position) {
            // Bottom to top, as on the desktop: the zones, the runways, the route, the path flown, the aircraft.
            if showZones, let zones = store.zones {
                ZoneLayers(zones: zones, vfr: mapMode.kind == .vfr).content
            }
            if let zones = store.zones {
                ForEach(zones.airports) { airport in
                    ForEach(airport.runways, id: \.self) { runway in
                        MapPolyline(coordinates: runway.ends.map(coordinate)).stroke(Zone.runway, lineWidth: 4)
                    }
                }
            }
            if let route = store.route, route.fixes.count > 1 {
                // Geodesic: the short way across the date line, not back across the whole world.
                MapPolyline(coordinates: route.fixes.map { CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon) },
                            contourStyle: .geodesic)
                    .stroke(Zone.route, lineWidth: 2)
                ForEach(Array(route.fixes.enumerated()), id: \.offset) { _, fix in
                    if fix.kind != "apt" {
                        Annotation("", coordinate: CLLocationCoordinate2D(latitude: fix.lat, longitude: fix.lon), anchor: .topLeading) {
                            HStack(spacing: 3) {
                                Circle().fill(Zone.route).frame(width: 5, height: 5)
                                Text(fix.ident).font(.system(size: 9, weight: .semibold, design: .monospaced)).foregroundStyle(Zone.route)
                                    .shadow(color: Color(uiColor: .systemBackground), radius: 1)
                            }
                        }
                        .annotationTitles(.hidden)
                    }
                }
            } else if let route = store.route, let zones = store.zones,
                      let from = zones.airports.first(where: { $0.icao == route.origin }),
                      let to = zones.airports.first(where: { $0.icao == route.destination }), from.icao != to.icao {
                // A plan typed without fixes: a straight line, dashed.
                MapPolyline(coordinates: [coordinate([from.lat, from.lon]), coordinate([to.lat, to.lon])], contourStyle: .geodesic)
                    .stroke(Zone.route, style: StrokeStyle(lineWidth: 2, dash: [6, 6]))
            }
            if store.trail.count > 1 {
                MapPolyline(coordinates: store.trail.map { CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon) },
                            contourStyle: .geodesic)
                    .stroke(.green.opacity(0.8), lineWidth: 3)
            }
            // The traffic around, as the desktop's Live Map shows it: pointing its way, airborne cyan and on the ground
            // grey, with its callsign, its altitude in hundreds of feet (or GND) and its type.
            ForEach(Array(store.traffic.values)) { target in
                Annotation("", coordinate: CLLocationCoordinate2D(latitude: target.lat, longitude: target.lon), anchor: .center) {
                    HStack(alignment: .top, spacing: 2) {
                        plane(heading: target.hdg, size: 14, color: target.ground == true ? Zone.trafficGround : Zone.traffic)
                        VStack(alignment: .leading, spacing: 0) {
                            Text(target.callsign ?? "")
                            Text(trafficLevel(target))
                        }
                        .font(.system(size: 9, weight: .medium, design: .monospaced))
                        .foregroundStyle(.white)
                        .shadow(color: .black, radius: 1.5)
                    }
                }
                .annotationTitles(.hidden)
            }
            if let own = store.own {
                Annotation(store.status.callsign ?? "You", coordinate: CLLocationCoordinate2D(latitude: own.lat, longitude: own.lon)) {
                    plane(heading: own.hdg, size: 26, color: .green)
                        .shadow(color: .black.opacity(0.4), radius: 2)
                }
            }
        }
        .mapStyle(mapMode.kind == .vfr
                  // VFR: terrain and the airports, like a chart. IFR: a quiet map under the route and traffic.
                  ? .hybrid(elevation: .realistic, pointsOfInterest: .including([.airport]))
                  : .standard(elevation: .flat, emphasis: .muted, pointsOfInterest: .excludingAll))
        .mapControls { MapCompass(); MapScaleView() }
        .onMapCameraChange(frequency: .onEnd) { context in
            // The pilot moved the map by hand: stop following until they ask again.
            if let own = store.own, follow {
                let here = CLLocation(latitude: own.lat, longitude: own.lon)
                let centre = CLLocation(latitude: context.region.center.latitude, longitude: context.region.center.longitude)
                if here.distance(from: centre) > 20_000 { follow = false }
            }
        }
        .onChange(of: store.status.rules, initial: true) { _, rules in
            mapMode.follow(rules: rules)
        }
        .onChange(of: store.own) { _, own in
            guard follow, let own else { return }
            withAnimation(.linear(duration: 0.4)) {
                position = .camera(MapCamera(centerCoordinate: CLLocationCoordinate2D(latitude: own.lat, longitude: own.lon),
                                             distance: distance(for: own)))
            }
        }
        .safeAreaInset(edge: .bottom) {
            // One panel at the bottom: the top of the screen stays clear for the alert banners.
            VStack(spacing: 8) {
                HStack {
                    Picker("Map", selection: Binding(get: { mapMode.kind }, set: { mapMode.pick($0) })) {
                        ForEach(MapMode.Kind.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .frame(width: 140)
                    .padding(6)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
                    .accessibilityIdentifier("mapMode")
                    .accessibilityLabel("Map type: IFR or VFR")
                    Button { showZones.toggle() } label: {
                        Image(systemName: showZones ? "square.3.layers.3d.top.filled" : "square.3.layers.3d")
                            .font(.title3)
                            .padding(12)
                            .background(.regularMaterial, in: Circle())
                    }
                    .accessibilityLabel(showZones ? "Hide the ATC zones" : "Show the ATC zones")
                    .accessibilityIdentifier("mapZones")
                    if showZones && store.zones != nil {
                        Button { legend = true } label: {
                            Image(systemName: "info.circle")
                                .font(.title3)
                                .padding(12)
                                .background(.regularMaterial, in: Circle())
                        }
                        .accessibilityLabel("What the colours mean")
                        .accessibilityIdentifier("mapKey")
                    }
                    Spacer()
                    Button { follow = true } label: {
                        Image(systemName: follow ? "location.fill" : "location")
                            .font(.title3)
                            .padding(12)
                            .background(.regularMaterial, in: Circle())
                    }
                    .accessibilityLabel("Follow the aircraft")
                }
                if store.status.active {
                    NavigationLink { FlightTab() } label: { FlightCard(status: store.status, own: store.own) }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("flightCard")
                }
            }
            .padding(.horizontal)
            .padding(.bottom, 6)
        }
        .overlay {
            if !store.status.active && store.own == nil { NotFlying() }
        }
        .overlay(alignment: .top) {
            if store.status.active && store.own == nil {
                // Flying, but no position yet (the server passes it on a few seconds after the phone connects),
                // or the pilot keeps it at home (Quick Settings → Account on the PC).
                Label("Waiting for the aircraft's position", systemImage: "location.slash")
                    .font(.footnote)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(.regularMaterial, in: Capsule())
                    .padding(.top, 12)
            }
        }
        .sheet(isPresented: $legend) {
            ZoneLegend(zones: store.zones, vfr: mapMode.kind == .vfr)
                .presentationDetents([.medium])
        }
        .navigationTitle(store.status.callsign ?? "My Flight")
        .navigationBarTitleDisplayMode(.inline)
    }

    /// Closer on the ground, wider in the climb and cruise.
    private func distance(for own: OwnAircraft) -> Double {
        own.ground == true ? 4_000 : min(max(Double(own.alt ?? 0) * 12, 15_000), 400_000)
    }

    private func trafficLevel(_ t: TrafficTarget) -> String {
        let level = t.ground == true ? "GND" : t.alt.map { String(format: "%03d", max($0, 0) / 100) } ?? ""
        return [level, t.type ?? ""].filter { !$0.isEmpty }.joined(separator: " ")
    }

    /// SF Symbols' aeroplane points east; a heading counts from north.
    private func plane(heading: Int?, size: CGFloat, color: Color) -> some View {
        Image(systemName: "airplane")
            .font(.system(size: size, weight: .bold))
            .foregroundStyle(color)
            .rotationEffect(.degrees(Double(heading ?? 0) - 90))
    }

}

private func coordinate(_ point: [Double]) -> CLLocationCoordinate2D {
    CLLocationCoordinate2D(latitude: point.first ?? 0, longitude: point.count > 1 ? point[1] : 0)
}

/// The desktop Live Map's colours (atcmap.js).
enum Zone {
    // The desktop's map is dark; Apple's follows the phone's appearance, so the lines are a grey both show.
    static let center = Color(white: 0.45)
    static let runway = Color(white: 0.5)
    static let terminal = Color(red: 0.18, green: 0.71, blue: 0.49)
    static let final = Color(red: 0.91, green: 0.70, blue: 0.29)
    static let tower = Color(red: 0.89, green: 0.34, blue: 0.30)
    static let clearance = Color(red: 0.24, green: 0.55, blue: 0.99)
    static let ground = Color(red: 0.13, green: 0.65, blue: 0.36)
    static let taxi = Color(red: 0.95, green: 0.79, blue: 0.30)
    static let gate = Color(red: 0.54, green: 0.42, blue: 0.94)
    static let route = Color(red: 0.91, green: 0.47, blue: 0.84)
    static let traffic = Color(red: 0.38, green: 0.78, blue: 0.90)
    static let trafficGround = Color(red: 0.55, green: 0.58, blue: 0.62)
    static let classB = Color(red: 0.17, green: 0.44, blue: 0.85)
    static let classC = Color(red: 0.70, green: 0.23, blue: 0.62)

    static func badge(_ letter: String) -> Color {
        switch letter {
        case "D": clearance
        case "G": ground
        case "T": tower
        default: terminal
        }
    }

    /// B and D blue, C and ATZ magenta; D, CTR and ATZ dashed.
    static func style(_ kind: String?) -> (Color, [CGFloat], CGFloat)? {
        switch kind {
        case "B": (classB, [], 2.4)
        case "C": (classC, [], 2.2)
        case "D", "CTR": (classB, [7, 5], 1.8)
        case "ATZ": (classC, [4, 4], 1.6)
        default: nil
        }
    }
}

/// The ATC layer on the map, as the desktop's Live Map draws it: the centres on the route (the one the aircraft
/// is in highlighted), the departure and approach areas, the stretch of final, each tower's control zone, the
/// taxi route and the gate, and each airport's controllers as badges. On the VFR map, the airspace classes.
@MainActor
struct ZoneLayers {
    let zones: AtcZones
    let vfr: Bool

    @MapContentBuilder var content: some MapContent {
        if vfr {
            ForEach(zones.classes) { airspace in
                if let (color, dash, width) = Zone.style(airspace.class) {
                    ForEach(Array(airspace.rings.enumerated()), id: \.offset) { i, ring in
                        MapCircle(center: coordinate([airspace.lat, airspace.lon]), radius: ring.nm * 1852)
                            .foregroundStyle(color.opacity(i == 0 ? 0.07 : 0))
                            .stroke(color, style: StrokeStyle(lineWidth: width, dash: dash))
                        Annotation("", coordinate: coordinate(airspace.labelPoint(ring: i))) {
                            VStack(spacing: 0) {
                                Text(ring.ceilingLabel)
                                Rectangle().frame(height: 1.5)
                                Text(ring.floorLabel)
                            }
                            .fixedSize()
                            .font(.system(size: 10, weight: .bold))
                            .foregroundStyle(color)
                            .shadow(color: .white, radius: 1)
                        }
                        .annotationTitles(.hidden)
                    }
                }
                Annotation(airspace.icao, coordinate: coordinate([airspace.lat, airspace.lon])) {
                    Circle().fill(.white).stroke(airspace.towered == true ? Zone.classB : Zone.classC, lineWidth: 2)
                        .frame(width: 10, height: 10)
                }
            }
        } else {
            ForEach(zones.centers) { center in
                let on = center.active == true || center.working == true
                ForEach(Array(center.rings.flatMap(antimeridianPieces).enumerated()), id: \.offset) { _, ring in
                    MapPolygon(coordinates: ring.map(coordinate))
                        .foregroundStyle(Zone.center.opacity(on ? 0.05 : 0))
                        .stroke(Zone.center.opacity(center.route == true ? 0.85 : 0.35),
                                style: StrokeStyle(lineWidth: on ? 2.2 : 1, dash: center.route == true ? [] : [4, 6]))
                }
                if let label = center.label, let name = center.name {
                    Annotation("", coordinate: coordinate(label), anchor: .center) {
                        Text(name)
                            .font(.system(size: 11, weight: center.route == true ? .semibold : .regular))
                            .foregroundStyle(center.active == true ? Color.green : center.route == true ? Color.primary : Color.secondary)
                            .shadow(color: Color(uiColor: .systemBackground), radius: 2)
                            .fixedSize()
                    }
                    .annotationTitles(.hidden)
                }
            }
            ForEach(zones.terminals) { area in
                ForEach(Array(area.rings.flatMap(antimeridianPieces).enumerated()), id: \.offset) { _, ring in
                    MapPolygon(coordinates: ring.map(coordinate))
                        .foregroundStyle(Zone.terminal.opacity(area.working == true ? 0.22 : 0.12))
                        .stroke(Zone.terminal, lineWidth: area.working == true ? 2.2 : 1.4)
                }
            }
            if let final = zones.final, final.ring.count > 2 {
                MapPolygon(coordinates: final.ring.map(coordinate))
                    .foregroundStyle(Zone.final.opacity(0.1))
                    .stroke(Zone.final, style: StrokeStyle(lineWidth: 1.5, dash: [5, 5]))
            }
            ForEach(zones.airports) { airport in
                if let nm = airport.towerNm, nm > 0 {
                    MapCircle(center: coordinate([airport.lat, airport.lon]), radius: nm * 1852)
                        .foregroundStyle(Zone.tower.opacity(0.05))
                        .stroke(Zone.tower, style: StrokeStyle(lineWidth: 1.3, dash: [4, 5]))
                }
            }
        }
        if let taxi = zones.taxi, taxi.points.count > 1 {
            MapPolyline(coordinates: taxi.points.map(coordinate))
                .stroke(Zone.taxi, style: StrokeStyle(lineWidth: 3.5, dash: [8, 6]))
        }
        if let gate = zones.gate {
            Annotation(gate.name ?? "Gate", coordinate: coordinate([gate.lat, gate.lon])) {
                Circle().fill(Zone.gate).stroke(.white, lineWidth: 2).frame(width: 12, height: 12)
            }
        }
        ForEach(zones.airports) { airport in
            let badges = airport.badges
            if !badges.isEmpty {
                Annotation("", coordinate: coordinate([airport.lat, airport.lon]), anchor: .bottom) {
                    HStack(spacing: 3) {
                        ForEach(badges, id: \.self) { badge in
                            Text(badge.letter)
                                .font(.system(size: 11, weight: .bold))
                                .foregroundStyle(.white)
                                .frame(width: 17, height: 17)
                                .background(Zone.badge(badge.letter), in: RoundedRectangle(cornerRadius: 3))
                                .overlay {
                                    if badge.tuned || badge.next {
                                        RoundedRectangle(cornerRadius: 4).stroke(badge.tuned ? .white : Zone.final, lineWidth: 2)
                                            .padding(-2)
                                    }
                                }
                        }
                    }
                    .padding(.bottom, 14)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("\(airport.icao): " + badges.map(\.letter).joined(separator: ", "))
                }
                .annotationTitles(.hidden)
            }
        }
    }
}

/// What the colours on the map mean, and who the flight talks to now and next.
struct ZoneLegend: View {
    let zones: AtcZones?
    let vfr: Bool

    var body: some View {
        NavigationStack {
            List {
                if let zones, zones.tuned != nil || zones.next != nil || zones.center != nil {
                    Section("Talking to") {
                        if let tuned = zones.tuned { Label(tuned.label, systemImage: "dot.radiowaves.left.and.right").foregroundStyle(.green) }
                        if let next = zones.next { Label("Next: \(next.label)", systemImage: "arrow.right.circle").foregroundStyle(.orange) }
                        if zones.tuned == nil, let center = zones.center { Text("In \(center) airspace") }
                    }
                }
                Section(vfr ? "VFR map" : "IFR map") {
                    if vfr {
                        row(Zone.classB, "Class B: needs \"cleared into the Class Bravo\"")
                        row(Zone.classC, "Class C: talk to approach before entering")
                        row(Zone.classB, "Class D / control zone: call the tower first", dashed: true)
                        row(Zone.classC, "Traffic zone (ATZ)", dashed: true)
                        Text("Labels: ceiling over floor, in hundreds of feet MSL (SFC = surface). Simplified, not for real navigation.")
                            .font(.footnote).foregroundStyle(.secondary)
                    } else {
                        row(Zone.center, "Centre: enroute, handed over at its boundary")
                        row(Zone.terminal, "Departure / Approach area")
                        row(Zone.final, "Joining final: cleared, then to Tower", dashed: true)
                        row(Zone.tower, "Tower's control zone", dashed: true)
                    }
                    row(Zone.taxi, "Taxi route ground gave", dashed: true)
                    row(Zone.route, "Flight plan route")
                    row(.green, "The path flown")
                }
                Section("Controllers") {
                    HStack(spacing: 14) {
                        ForEach(["D": "Clearance", "G": "Ground", "T": "Tower", "A": "Dep/App"].sorted(by: { $0.key < $1.key }), id: \.key) { letter, name in
                            HStack(spacing: 4) {
                                Text(letter).font(.caption.bold()).foregroundStyle(.white)
                                    .frame(width: 18, height: 18).background(Zone.badge(letter), in: RoundedRectangle(cornerRadius: 3))
                                Text(name).font(.caption)
                            }
                        }
                    }
                    Text("A white ring: tuned now. An orange one: call it next.").font(.footnote).foregroundStyle(.secondary)
                }
                if !vfr {
                    Text("Airspace: VATSpy & SimAware TRACON projects, CC BY-SA 4.0").font(.caption2).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Map")
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    private func row(_ color: Color, _ text: String, dashed: Bool = false) -> some View {
        HStack(spacing: 10) {
            Capsule().stroke(color, style: StrokeStyle(lineWidth: 2.5, dash: dashed ? [4, 3] : []))
                .frame(width: 22, height: 10)
            Text(text).font(.subheadline)
        }
    }
}

/// The flight at a glance under the map: tap for everything.
struct FlightCard: View {
    let status: FlightStatus
    let own: OwnAircraft?

    var body: some View {
        VStack(spacing: 10) {
            summary
            if let own {
                HStack {
                    stat("ALT", own.alt.map { $0.formatted() } ?? "—")
                    stat("GS", own.gs.map(String.init) ?? "—")
                    stat("HDG", own.hdg.map { String(format: "%03d", $0) } ?? "—")
                    stat("VS", own.vs.map { ($0 > 0 ? "+" : "") + String($0) } ?? "—")
                }
            }
        }
        .padding(12)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
    }

    private var summary: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text([status.callsign, status.phaseLabel ?? status.phase].compactMap { $0 }.joined(separator: " · "))
                    .font(.subheadline.bold())
                Text([status.origin, status.destination].compactMap { $0 }.joined(separator: " → "))
                    .font(.caption.monospaced()).foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 2) {
                Text(status.tuned?.label ?? "—").font(.caption.monospacedDigit()).lineLimit(1)
                if let next = status.next, next != status.tuned {
                    Text("next \(next.label)").font(.caption2.monospacedDigit()).foregroundStyle(.orange).lineLimit(1)
                }
            }
            Image(systemName: "chevron.right").font(.caption.bold()).foregroundStyle(.tertiary)
        }
    }

    private func stat(_ name: String, _ value: String) -> some View {
        VStack(spacing: 0) {
            Text(name).font(.caption2).foregroundStyle(.secondary)
            Text(value).font(.callout.monospacedDigit().bold())
        }
        .frame(maxWidth: .infinity)
    }
}

struct NotFlying: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        ContentUnavailableView {
            Label("No flight yet", systemImage: "airplane")
        } description: {
            Text(model.connection.state == .offline
                 ? "Start LocalTC on your PC and sign in there with the same email (Quick Settings → Account)."
                 : "Press Start in LocalTC on your PC. The flight appears here.")
        }
        .background(.regularMaterial)
    }
}
