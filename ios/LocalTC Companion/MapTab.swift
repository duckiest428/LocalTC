import MapKit
import SwiftUI

struct MapTab: View {
    @Environment(AppModel.self) private var model
    @State private var position: MapCameraPosition = .automatic
    @State private var follow = true
    @State private var mapMode = MapMode()  // IFR or VFR: follows the flight's rules, switchable any time

    var body: some View {
        let store = model.store
        Map(position: $position) {
            if let route = store.route, route.fixes.count > 1 {
                MapPolyline(coordinates: route.fixes.map { CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon) })
                    .stroke(.purple.opacity(0.7), style: StrokeStyle(lineWidth: 2, dash: [6, 4]))
            }
            if store.trail.count > 1 {
                MapPolyline(coordinates: store.trail.map { CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon) })
                    .stroke(.green.opacity(0.8), lineWidth: 3)
            }
            ForEach(Array(store.traffic.values)) { target in
                Annotation(label(target), coordinate: CLLocationCoordinate2D(latitude: target.lat, longitude: target.lon)) {
                    plane(heading: target.hdg, size: 14, color: target.ground == true ? .gray : .orange)
                }
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
        .navigationTitle(store.status.callsign ?? "My Flight")
        .navigationBarTitleDisplayMode(.inline)
    }

    /// Closer on the ground, wider in the climb and cruise.
    private func distance(for own: OwnAircraft) -> Double {
        own.ground == true ? 4_000 : min(max(Double(own.alt ?? 0) * 12, 15_000), 400_000)
    }

    private func label(_ t: TrafficTarget) -> String {
        let alt = t.alt.map { $0 >= 18_000 ? "FL\($0 / 100)" : "\($0)" } ?? ""
        return [t.callsign, alt].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " ")
    }

    /// SF Symbols' aeroplane points east; a heading counts from north.
    private func plane(heading: Int?, size: CGFloat, color: Color) -> some View {
        Image(systemName: "airplane")
            .font(.system(size: size, weight: .bold))
            .foregroundStyle(color)
            .rotationEffect(.degrees(Double(heading ?? 0) - 90))
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
