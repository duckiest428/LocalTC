import MapKit
import SwiftUI

/// ATC Wrapped on the phone: a week, a month or a year of the account's flying, as a story of slides. The
/// account server works it out (the same slides as the website's); the words are LocalTCKit's SlideText.
/// "Image" draws the slide as the website does (sharecard.js); the summary can become a public link.
/// Only the last finished week, month or year can be seen; the one still going is locked, counting down.
struct WrappedScreen: View {
    @Environment(AppModel.self) private var model
    @State private var kind: WrappedPeriod.Kind = .month
    @State private var step = -1  // the last finished period; 0 is the one still going (locked)
    @State private var recap: Wrapped?
    @State private var raw: [String: Any] = [:]
    @State private var error: String?
    @State private var loading = false
    @State private var index = 0
    @State private var paused = false
    @State private var renderer = CardRenderer(site: AppModel.siteURL)
    @State private var image: Image?
    @State private var link: URL?
    @State private var status: String?
    private let slideSeconds = 6.5

    private var period: WrappedPeriod { WrappedPeriod(kind, step: step) }

    var body: some View {
        VStack(spacing: 12) {
            Picker("Period", selection: $kind) {
                ForEach(WrappedPeriod.Kind.allCases, id: \.self) { Text($0.rawValue.capitalized).tag($0) }
            }
            .pickerStyle(.segmented)
            HStack {
                Button { step = -1 } label: { Image(systemName: "chevron.left") }
                    .disabled(step == -1).accessibilityLabel("The period before")
                Spacer()
                Text(period.label).font(.headline)
                Spacer()
                Button { step = 0 } label: { Image(systemName: "chevron.right") }
                    .disabled(step == 0).accessibilityLabel("The period after")
            }
            content
            Spacer(minLength: 0)
        }
        .padding()
        .background(Color(red: 0.043, green: 0.051, blue: 0.063))
        .environment(\.colorScheme, .dark)  // a story is told in the dark, whatever the phone's setting
        .navigationTitle("Wrapped")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(Color(red: 0.043, green: 0.051, blue: 0.063), for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .onChange(of: kind) { step = -1 }
        .task(id: "\(kind.rawValue)\(step)") { if step == -1 { await load() } }
        .task(id: "\(index)-\(paused)-\(recap?.label ?? "")") { await advance() }
        .overlay(alignment: .topLeading) {
            // The renderer draws off screen: its web view has to be in the window to run.
            CardPreview(renderer: renderer).frame(width: 2, height: 1).opacity(0.01).allowsHitTesting(false)
        }
    }

    @ViewBuilder private var content: some View {
        if step == 0 {
            locked
        } else if loading && recap == nil {
            ProgressView("Looking back ...").frame(maxHeight: .infinity)
        } else if let error {
            ContentUnavailableView("Couldn't load Wrapped", systemImage: "exclamationmark.triangle", description: Text(error))
        } else if let recap, recap.flights == 0 {
            empty(recap)
        } else if let recap {
            story(recap)
        }
    }

    /// The period still going: its recap unlocks when it ends.
    private var locked: some View {
        let current = period
        return TimelineView(.periodic(from: .now, by: 1)) { context in
            VStack(spacing: 12) {
                Image(systemName: "lock.fill").font(.largeTitle).foregroundStyle(.secondary)
                Text("\(current.label) is still going").font(.title3.bold())
                Text("Its Wrapped unlocks when it ends, in").foregroundStyle(.secondary)
                Text(Self.countdown(current.end.timeIntervalSince(context.date)))
                    .font(.system(.largeTitle, design: .monospaced).weight(.semibold)).foregroundStyle(.green)
                    .accessibilityIdentifier("wrapped-countdown")
                Button("See \(WrappedPeriod(kind, step: -1).label)") { step = -1 }.buttonStyle(.bordered)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    static func countdown(_ seconds: TimeInterval) -> String {
        let s = max(0, Int(seconds))
        let clock = String(format: "%02d:%02d:%02d", s % 86400 / 3600, s % 3600 / 60, s % 60)
        return s >= 86400 ? "\(s / 86400)d \(clock)" : clock
    }

    private func empty(_ recap: Wrapped) -> some View {
        ContentUnavailableView {
            Label("No flights in \(period.label.replacingOccurrences(of: "Week of", with: "the week of"))", systemImage: "airplane")
        } description: {
            if let last = recap.lastFlight {
                Text("Your last flight was \(last.daysAgo == 0 ? "today" : last.daysAgo == 1 ? "yesterday" : "\(last.daysAgo) days ago"): \(last.origin) → \(last.destination).")
            } else {
                Text("Fly with LocalTC signed in to this account, and each flight you land adds to it.")
            }
            Text("Your next Wrapped unlocks in \(Self.countdown(WrappedPeriod(kind, step: 0).end.timeIntervalSinceNow)).")
        }
    }

    private func story(_ recap: Wrapped) -> some View {
        VStack(spacing: 10) {
            HStack(spacing: 3) {
                if recap.slides.count > 1 {
                    ForEach(recap.slides.indices, id: \.self) { i in
                        Capsule().fill(i <= index ? Color.green : Color.white.opacity(0.15)).frame(height: 3)
                    }
                }
            }
            TabView(selection: $index) {
                ForEach(recap.slides.indices, id: \.self) { i in
                    SlideView(slide: recap.slides[i], recap: recap).tag(i)
                        .accessibilityIdentifier("wrapped-slide-\(recap.slides[i].kind)")
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
            .frame(maxHeight: 520)
            .onLongPressGesture(minimumDuration: 0.2, pressing: { paused = $0 }, perform: {})
            HStack {
                Button { Task { await exportSlide(recap) } } label: { Label("Image", systemImage: "photo") }
                    .buttonStyle(.bordered)
                if let image {
                    ShareLink(item: image, preview: SharePreview("\(recap.label) · LocalTC", image: image)) {
                        Label("Share image", systemImage: "square.and.arrow.up")
                    }
                    .buttonStyle(.bordered)
                }
                Spacer()
                if recap.slides.indices.contains(index), recap.slides[index].kind == "summary" {
                    if let link {
                        ShareLink(item: link) { Label("Send link", systemImage: "link") }.buttonStyle(.borderedProminent)
                    } else {
                        Button("Share my \(kind.rawValue)") { Task { await shareSummary() } }.buttonStyle(.borderedProminent)
                    }
                }
            }
            if let status { Text(status).font(.caption).foregroundStyle(.secondary) }
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        index = 0
        image = nil
        link = nil
        status = nil
        do {
            let data = try await model.api.wrappedJSON(period)
            recap = try model.api.decodeWrapped(data)
            raw = JSONSerialization.dictionary(data)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// The next slide after a few seconds, unless held.
    private func advance() async {
        guard let recap, !paused, index < recap.slides.count - 1 else { return }
        try? await Task.sleep(for: .seconds(slideSeconds))
        guard !Task.isCancelled, !paused else { return }
        withAnimation { index += 1 }
    }

    private func slideSpec(_ recap: Wrapped) -> [String: Any] {
        let slides = raw["slides"] as? [[String: Any]] ?? []
        return ["slide": slides.indices.contains(index) ? slides[index] : [:], "recap": raw]
    }

    private func exportSlide(_ recap: Wrapped) async {
        do {
            status = "Drawing ..."
            let png = try await renderer.png(slideSpec(recap))
            if let ui = UIImage(data: png) { image = Image(uiImage: ui) }
            status = nil
        } catch {
            status = error.localizedDescription
        }
    }

    private func shareSummary() async {
        do {
            status = "Sharing ..."
            let made = try await model.api.shareWrapped(period)
            status = "Drawing the card ..."
            try await model.api.uploadShareImage(slug: made.slug, png: try await renderer.png(["card": JSONSerialization.dictionary(made.card)]))
            link = made.url
            status = "Shared: the summary only, never a flight in detail."
        } catch {
            status = error.localizedDescription
        }
    }
}

/// One slide, drawn natively for the phone's portrait screen.
private struct SlideView: View {
    let slide: WrappedSlide
    let recap: Wrapped

    private var text: SlideText { SlideText(slide, recap: recap) }
    private var accent: Color {
        switch slide.kind {
        case "distance", "longest", "radio", "top_route": .cyan
        case "streak", "persona": .orange
        default: .green
        }
    }

    var body: some View {
        let t = text
        VStack(alignment: .leading, spacing: 14) {
            Text(t.kicker).font(.caption.monospaced().bold()).tracking(2).foregroundStyle(.cyan)
            Spacer(minLength: 0)
            if slide.kind == "map" || slide.kind == "summary" {
                RoutesMap(airports: recap.airports, routes: recap.routes)
                    .frame(height: slide.kind == "map" ? 240 : 170)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
            }
            if let quote = t.quote {
                VStack(alignment: .leading, spacing: 10) {
                    Text(t.station ?? "").font(.caption.monospaced().bold()).foregroundStyle(.cyan)
                    Text("“\(quote)”").font(.title3.monospaced())
                }
                .padding(.leading, 14)
                .overlay(alignment: .leading) { Capsule().fill(.cyan).frame(width: 4) }
            } else {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(t.value).font(.system(size: t.value.count > 9 ? 44 : 72, weight: .heavy)).minimumScaleFactor(0.4).lineLimit(2)
                    if !t.unit.isEmpty { Text(t.unit).font(.title.bold()).foregroundStyle(accent) }
                }
                Text(t.label).font(.title3.weight(.semibold)).foregroundStyle(accent)
            }
            if !t.sub.isEmpty { Text(t.sub).font(.body).foregroundStyle(.secondary) }
            Spacer(minLength: 0)
            Text("\(recap.label) · localtc.tech").font(.caption2.monospaced()).foregroundStyle(.tertiary)
        }
        .foregroundStyle(.white)
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .background(
            RadialGradient(colors: [Color(red: 0.075, green: 0.1, blue: 0.13), Color(red: 0.043, green: 0.051, blue: 0.063)],
                           center: .topTrailing, startRadius: 10, endRadius: 600),
            in: RoundedRectangle(cornerRadius: 20))
        .accessibilityElement(children: .combine)
    }
}

/// Every route of the period, airports sized by visits.
private struct RoutesMap: View {
    let airports: [WrappedAirport]
    let routes: [WrappedRoute]

    var body: some View {
        let places = Dictionary(airports.compactMap { a in a.lat.flatMap { lat in a.lon.map { (a.icao, CLLocationCoordinate2D(latitude: lat, longitude: $0)) } } },
                                uniquingKeysWith: { a, _ in a })
        Map(interactionModes: []) {
            ForEach(routes, id: \.self) { r in
                if let a = places[r.origin], let b = places[r.destination] {
                    MapPolyline(coordinates: [a, b], contourStyle: .geodesic).stroke(.cyan, lineWidth: 2)
                }
            }
            ForEach(airports, id: \.self) { a in
                if let c = places[a.icao] {
                    Annotation("", coordinate: c) { Circle().fill(.green).frame(width: CGFloat(6 + min(a.visits, 10)), height: CGFloat(6 + min(a.visits, 10))) }
                }
            }
        }
        .mapStyle(.imagery(elevation: .flat))
    }
}
