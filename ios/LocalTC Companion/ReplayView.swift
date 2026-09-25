import MapKit
import SwiftUI

/// A flight to rewatch: the aircraft along its track, a timeline to scrub, and the radio transcript in step.
/// The same player as the website's (site/replayplayer.js); the clock's rules are in LocalTCKit's ReplayClock.
struct ReplayView: View {
    let flight: LogbookFlight
    @Environment(AppModel.self) private var model
    @State private var clock: ReplayClock?
    @State private var error: String?
    @State private var sharing = false

    var body: some View {
        Group {
            if let clock {
                ReplayPlayer(clock: clock)
            } else if let error {
                ContentUnavailableView("Couldn't load the replay", systemImage: "exclamationmark.triangle", description: Text(error))
            } else {
                ProgressView("Loading the flight ...")
            }
        }
        .navigationTitle("\(flight.origin) → \(flight.destination)")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { sharing = true } label: { Label("Share", systemImage: "square.and.arrow.up") }
            }
        }
        .sheet(isPresented: $sharing) { ShareFlightSheet(flight: flight) }
        .task {
            do {
                clock = ReplayClock(try await model.api.replay(id: flight.id))
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

private struct ReplayPlayer: View {
    let clock: ReplayClock
    private let coords: [CLLocationCoordinate2D]
    private let routeCoords: [CLLocationCoordinate2D]
    @State private var t = 0.0
    @State private var playing = false
    @State private var speed = 4.0
    @State private var skipQuiet = true
    @State private var follow = false
    @State private var position: MapCameraPosition
    @State private var ticker: Task<Void, Never>?

    init(clock: ReplayClock) {
        self.clock = clock
        let k = clock.replay.track
        coords = k.t.indices.map { CLLocationCoordinate2D(latitude: k.lat[$0], longitude: k.lon[$0]) }
        routeCoords = clock.replay.route.map { CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon) }
        _position = State(initialValue: .rect(Self.bounds(coords)))
    }

    var body: some View {
        let s = clock.sample(at: t)
        let here = CLLocationCoordinate2D(latitude: s.lat, longitude: s.lon)
        VStack(spacing: 0) {
            map(s, here)
                .frame(maxHeight: .infinity)
                .overlay(alignment: .bottomLeading) { readout(s) }
            controls
            transcript
                .frame(maxHeight: .infinity)
        }
        .onDisappear { pause() }
        .onChange(of: t) { _, _ in if follow { centre() } }
    }

    /// The camera on the aircraft: closer on the ground, wider in the climb and cruise (as My Flight's map).
    private func centre() {
        let s = clock.sample(at: t)
        position = .camera(MapCamera(centerCoordinate: CLLocationCoordinate2D(latitude: s.lat, longitude: s.lon),
                                     distance: s.onGround ? 4_000 : min(max(s.alt * 12, 15_000), 400_000)))
    }

    // --- the map ---------------------------------------------------------------------------------------------

    private func map(_ s: ReplayClock.Sample, _ here: CLLocationCoordinate2D) -> some View {
        Map(position: $position) {
            if routeCoords.count > 1 {
                MapPolyline(coordinates: routeCoords).stroke(.purple.opacity(0.6), style: StrokeStyle(lineWidth: 2, dash: [6, 4]))
            }
            MapPolyline(coordinates: coords).stroke(.gray.opacity(0.6), lineWidth: 2)
            MapPolyline(coordinates: Array(coords[...s.index]) + [here]).stroke(.green, lineWidth: 3)
            ForEach(clock.replay.airports.sorted(by: { $0.key < $1.key }), id: \.key) { icao, a in
                Annotation(icao, coordinate: CLLocationCoordinate2D(latitude: a.lat, longitude: a.lon)) {
                    Circle().fill(.cyan).stroke(.white, lineWidth: 2).frame(width: 12, height: 12)
                }
            }
            Annotation(clock.replay.flight.callsign, coordinate: here) {
                Image(systemName: "airplane")
                    .font(.system(size: 24, weight: .bold))
                    .foregroundStyle(.green)
                    .rotationEffect(.degrees(s.hdg - 90))  // SF Symbols' aeroplane points east
                    .shadow(color: .black.opacity(0.5), radius: 2)
            }
        }
        .mapStyle(.standard(elevation: .flat, emphasis: .muted, pointsOfInterest: .excludingAll))
        .onMapCameraChange(frequency: .onEnd) { context in
            // Moved by hand, far from the aircraft: stop following until asked again.
            let s = clock.sample(at: t)
            let a = CLLocation(latitude: s.lat, longitude: s.lon)
            let b = CLLocation(latitude: context.region.center.latitude, longitude: context.region.center.longitude)
            if follow && a.distance(from: b) > 30_000 { follow = false }
        }
        .overlay(alignment: .topTrailing) {
            VStack(spacing: 8) {
                Button { follow.toggle(); if follow { centre() } } label: {
                    Image(systemName: follow ? "location.fill" : "location").padding(10).background(.regularMaterial, in: Circle())
                }
                .accessibilityLabel("Follow the aircraft")
                Button { follow = false; position = .rect(Self.bounds(coords)) } label: {
                    Image(systemName: "arrow.up.left.and.arrow.down.right").padding(10).background(.regularMaterial, in: Circle())
                }
                .accessibilityLabel("The whole flight")
            }
            .padding(10)
        }
    }

    private func readout(_ s: ReplayClock.Sample) -> some View {
        let vs = Int((s.vs / 50).rounded()) * 50
        return HStack(spacing: 10) {
            value("ALT", Int((s.alt / 10).rounded() * 10).formatted())
            value("GS", "\(Int(s.gs.rounded()))")
            value("VS", "\(vs > 0 ? "+" : "")\(vs)")
            value("HDG", String(format: "%03d", Int(s.hdg.rounded()) % 360))
            if let phase = clock.phase(at: t) { Text(phase).font(.caption.weight(.semibold)).foregroundStyle(.white) }
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(.black.opacity(0.7), in: RoundedRectangle(cornerRadius: 8))
        .padding(10)
        .accessibilityElement(children: .combine)
    }

    private func value(_ label: String, _ v: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 3) {
            Text(label).font(.system(size: 9, weight: .semibold)).foregroundStyle(.white.opacity(0.6))
            Text(v).font(.caption.monospacedDigit().weight(.semibold)).foregroundStyle(.green)
        }
    }

    // --- the timeline ----------------------------------------------------------------------------------------

    private var controls: some View {
        VStack(spacing: 6) {
            ZStack(alignment: .top) {
                marks
                Slider(value: Binding(get: { t }, set: { t = $0 }), in: 0...max(1, clock.replay.duration))
                    .tint(.green)
                    .padding(.top, 8)
                    .accessibilityLabel("Time in the flight")
                    .accessibilityValue(ReplayClock.elapsed(t))
            }
            HStack(spacing: 14) {
                Button { if let p = clock.call(before: t) { t = p } } label: { Image(systemName: "backward.end.fill") }
                    .accessibilityLabel("Previous call")
                Button { playing ? pause() : play() } label: {
                    Image(systemName: playing ? "pause.fill" : "play.fill").font(.title2)
                }
                .accessibilityLabel(playing ? "Pause" : "Play")
                .accessibilityIdentifier("replayPlay")
                Button { if let n = clock.call(after: t) { t = n } } label: { Image(systemName: "forward.end.fill") }
                    .accessibilityLabel("Next call")
                VStack(alignment: .leading, spacing: 0) {
                    Text("\(ReplayClock.elapsed(t)) / \(ReplayClock.elapsed(clock.replay.duration).dropFirst())")
                        .font(.caption.monospacedDigit())
                    if let z = clock.zulu(at: t) { Text(z).font(.caption2.monospacedDigit()).foregroundStyle(.secondary) }
                }
                Spacer()
                Menu {
                    ForEach([1.0, 4, 16, 64], id: \.self) { s in
                        Button("\(Int(s))×") { speed = s }
                    }
                    Toggle("Skip quiet", isOn: $skipQuiet)
                } label: {
                    Text("\(Int(speed))×").font(.callout.monospacedDigit().weight(.semibold))
                        .padding(.horizontal, 10).padding(.vertical, 4)
                        .background(.cyan.opacity(0.2), in: Capsule())
                }
                .accessibilityLabel("Speed \(Int(speed)) times, skip quiet \(skipQuiet ? "on" : "off")")
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(.bar)
    }

    /// The calls and the moments that matter, as ticks over the slider.
    private var marks: some View {
        GeometryReader { geo in
            let width = geo.size.width - 20
            let x = { (time: Double) in 10 + width * time / max(1, clock.replay.duration) }
            ForEach(clock.calls, id: \.self) { i in
                let line = clock.replay.radio[i]
                Rectangle().fill(line.kind == "atc" ? Color.cyan : Color.green).frame(width: 1, height: 5).position(x: x(line.t), y: 3)
            }
            ForEach(Array(clock.replay.marks.enumerated()).filter { $0.element.kind != "phase" }, id: \.offset) { _, mark in
                Circle().fill(color(mark.kind)).frame(width: 6, height: 6).position(x: x(mark.t), y: 3)
            }
        }
        .frame(height: 8)
        .accessibilityHidden(true)
    }

    private func color(_ kind: String) -> Color {
        switch kind {
        case "alert": .red
        case "handoff": .orange
        default: .green
        }
    }

    // --- the radio -------------------------------------------------------------------------------------------

    private var transcript: some View {
        let current = clock.line(at: t)
        return ScrollViewReader { proxy in
            List {
                ForEach(Array(clock.replay.radio.enumerated()), id: \.offset) { i, line in
                    TranscriptRow(line: line, now: i == current, past: current.map { i < $0 } ?? false)
                        .id(i)
                        .contentShape(Rectangle())
                        .onTapGesture { t = line.t }
                }
            }
            .listStyle(.plain)
            .onChange(of: current) { _, line in
                guard let line else { return }
                withAnimation(.easeOut(duration: 0.25)) { proxy.scrollTo(line, anchor: .center) }
            }
        }
    }

    // --- the clock -------------------------------------------------------------------------------------------

    private func play() {
        if t >= clock.replay.duration { t = 0 }
        playing = true
        ticker?.cancel()
        ticker = Task { @MainActor in
            var last = Date()
            while !Task.isCancelled && playing {
                try? await Task.sleep(for: .milliseconds(100))
                let now = Date()
                t = clock.advance(t, by: now.timeIntervalSince(last), speed: speed, skipQuiet: skipQuiet)
                last = now
                if t >= clock.replay.duration { pause() }
            }
        }
    }

    private func pause() {
        playing = false
        ticker?.cancel()
        ticker = nil
    }

    static func bounds(_ coords: [CLLocationCoordinate2D]) -> MKMapRect {
        let rect = coords.reduce(MKMapRect.null) { r, c in
            r.union(MKMapRect(origin: MKMapPoint(c), size: MKMapSize(width: 0, height: 0)))
        }
        return rect.insetBy(dx: -rect.size.width * 0.1 - 5_000, dy: -rect.size.height * 0.1 - 5_000)
    }
}

private struct TranscriptRow: View {
    let line: Replay.Line
    let now: Bool
    let past: Bool

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(ReplayClock.elapsed(line.t)).font(.caption2.monospacedDigit()).foregroundStyle(.secondary).frame(width: 58, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                switch line.kind {
                case "phase":
                    Text(line.text).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                case "tuned":
                    Text("Tuned \(line.mhz.map { String(format: "%.3f", $0) } ?? "") \(line.text)").font(.caption).foregroundStyle(.secondary)
                case "alert":
                    Label(line.text, systemImage: "exclamationmark.triangle.fill").font(.caption).foregroundStyle(.red)
                default:
                    HStack(spacing: 6) {
                        Text(who).font(.caption.weight(.semibold)).foregroundStyle(line.kind == "atc" || line.kind == "atis" ? .cyan : .green)
                        if let mhz = line.mhz { Text(String(format: "%.3f", mhz)).font(.caption2.monospacedDigit()).foregroundStyle(.secondary) }
                        if line.ok == true { Image(systemName: "checkmark").font(.caption2.bold()).foregroundStyle(.green) }
                        if line.ok == false {
                            Label(line.readback ?? "Readback not right", systemImage: "xmark").font(.caption2).foregroundStyle(.red)
                        }
                    }
                    Text(line.text).font(.subheadline).italic(line.unclear == true).lineLimit(line.kind == "atis" ? 2 : nil)
                }
            }
        }
        .opacity(now || past ? 1 : 0.5)
        .listRowBackground(now ? Color.green.opacity(0.12) : Color.clear)
        .accessibilityElement(children: .combine)
    }

    private var who: String {
        switch line.kind {
        case "atc", "atis": line.station ?? "ATC"
        case "copilot": "Copilot"
        default: "You"
        }
    }
}
