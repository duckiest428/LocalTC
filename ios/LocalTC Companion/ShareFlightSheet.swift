import SwiftUI

/// Share a flight: its card as the public will see it, the line from the radio it quotes (from the flight's
/// uploaded replay, if there is one), then a link to send. Nothing is public until Share is pressed.
struct ShareFlightSheet: View {
    let flight: LogbookFlight
    var onChange: (String?) -> Void = { _ in }
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var renderer = CardRenderer(site: AppModel.siteURL)
    @State private var kept = Moments(moments: [], names: [:])
    @State private var quote: Int = 0  // index into the moments; -1: none
    @State private var withReplay = true  // the path flown and the radio on the page, when there's a replay
    @State private var link: URL?
    @State private var slug: String?
    @State private var busy = false
    @State private var status: String?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    CardPreview(renderer: renderer)
                        .aspectRatio(1200.0 / 630.0, contentMode: .fit)
                        .listRowInsets(EdgeInsets())
                        .accessibilityLabel("The card: \(flight.origin) to \(flight.destination)")
                }
                Section {
                    if let link {
                        ShareLink(item: link) { Label("Send the link", systemImage: "square.and.arrow.up") }
                        Text(link.absoluteString).font(.caption.monospaced()).textSelection(.enabled)
                    }
                    if flight.hasReplay == true {
                        Toggle(isOn: $withReplay) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Put the replay on the page")
                                Text("The path you flew and the radio, both sides").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .accessibilityIdentifier("share-replay")
                    }
                    Button(link == nil ? "Share" : "Update the card") { Task { await share() } }
                        .disabled(busy)
                        .accessibilityIdentifier("share-go")
                    if link != nil {
                        Button("Stop sharing", role: .destructive) { Task { await unshare() } }.disabled(busy)
                    }
                } footer: {
                    Text(status ?? privacy)
                }
                if !kept.moments.isEmpty {
                    Section("The line from the radio") {
                        Picker("Quote", selection: $quote) {
                            ForEach(kept.moments.indices, id: \.self) { i in
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(kept.moments[i].label.uppercased()).font(.caption2.bold()).foregroundStyle(.cyan)
                                    Text(kept.moments[i].text).font(.caption.monospaced()).lineLimit(3)
                                }
                                .tag(i)
                            }
                            Text("No quote").tag(-1)
                        }
                        .pickerStyle(.inline)
                        .labelsHidden()
                    }
                }
            }
            .navigationTitle("Share this flight")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .task {
                if let share = flight.share, let url = URL(string: share) {
                    link = url
                    slug = url.lastPathComponent
                }
                if flight.hasReplay == true, let got = try? await model.api.moments(id: flight.id) { kept = got }
                await renderer.show(spec)
            }
            .onChange(of: quote) { Task { await renderer.show(spec) } }
        }
    }

    private var replayOn: Bool { flight.hasReplay == true && withReplay }

    private var privacy: String {
        "Anyone with the link sees this card: the route, the date, these numbers\(kept.moments.isEmpty ? "" : ", the line you pick") and the aircraft and runways"
            + (replayOn ? ", with the path flown and the radio (gates included, if ATC said them)." : ".")
            + " Never your email or the time of day\(replayOn ? "" : ", the track or the rest of the radio")."
    }

    private var chosen: Moment? { kept.moments.indices.contains(quote) ? kept.moments[quote] : nil }

    private var spec: [String: Any] {
        var s: [String: Any] = ["flight": JSONSerialization.dictionary(flight.json), "names": kept.names]
        if let chosen, let data = try? JSONEncoder().encode(chosen) { s["quote"] = JSONSerialization.dictionary(data) }
        return s
    }

    private func share() async {
        busy = true
        defer { busy = false }
        do {
            status = "Sharing ..."
            let made = try await model.api.shareFlight(id: flight.id, quote: chosen, names: kept.names, replay: replayOn)
            link = made.url
            slug = made.slug
            onChange(made.url.absoluteString)
            status = "Drawing the card ..."
            let png = try await renderer.png(["card": JSONSerialization.dictionary(made.card)])
            try await model.api.uploadShareImage(slug: made.slug, png: png)
            status = "Shared. Anyone with the link can see it."
        } catch {
            status = error.localizedDescription
        }
    }

    private func unshare() async {
        guard let slug else { return }
        busy = true
        defer { busy = false }
        do {
            try await model.api.unshare(slug: slug)
            link = nil
            self.slug = nil
            onChange(nil)
            status = "Not shared any more."
        } catch {
            status = error.localizedDescription
        }
    }
}
