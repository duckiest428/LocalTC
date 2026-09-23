import MapKit
import SwiftUI

struct MapTab: View {
    @Environment(AppModel.self) private var model
    @State private var position: MapCameraPosition = .automatic
    @State private var follow = true

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
        .mapStyle(.standard(elevation: .flat, pointsOfInterest: .excludingAll))
        .mapControls { MapCompass(); MapScaleView() }
        .onMapCameraChange(frequency: .onEnd) { context in
            // The pilot moved the map by hand: stop following until they ask again.
            if let own = store.own, follow {
                let here = CLLocation(latitude: own.lat, longitude: own.lon)
                let centre = CLLocation(latitude: context.region.center.latitude, longitude: context.region.center.longitude)
                if here.distance(from: centre) > 20_000 { follow = false }
            }
        }
        .onChange(of: store.own) { _, own in
            guard follow, let own else { return }
            withAnimation(.linear(duration: 0.4)) {
                position = .camera(MapCamera(centerCoordinate: CLLocationCoordinate2D(latitude: own.lat, longitude: own.lon),
                                             distance: distance(for: own)))
            }
        }
        .overlay(alignment: .bottomTrailing) {
            Button { follow = true } label: {
                Image(systemName: follow ? "location.fill" : "location")
                    .font(.title3)
                    .padding(12)
                    .background(.regularMaterial, in: Circle())
            }
            .padding()
            .accessibilityLabel("Follow the aircraft")
        }
        .overlay(alignment: .bottomLeading) {
            if let own = store.own {
                HStack(spacing: 14) {
                    stat("ALT", own.alt.map { $0.formatted() } ?? "—")
                    stat("GS", own.gs.map(String.init) ?? "—")
                    stat("HDG", own.hdg.map { String(format: "%03d", $0) } ?? "—")
                    stat("VS", own.vs.map { ($0 > 0 ? "+" : "") + String($0) } ?? "—")
                }
                .padding(10)
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
                .padding()
            }
        }
        .overlay {
            if store.own == nil { NotFlying() }
        }
        .navigationTitle(store.status.callsign ?? "Map")
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

    private func stat(_ name: String, _ value: String) -> some View {
        VStack(spacing: 0) {
            Text(name).font(.caption2).foregroundStyle(.secondary)
            Text(value).font(.callout.monospacedDigit().bold())
        }
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
