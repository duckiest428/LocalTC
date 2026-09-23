import SwiftUI

struct FlightTab: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let s = model.store.status
        List {
            if !s.active {
                Section { NotFlyingRow() }
            }
            Section("Flight") {
                row("Callsign", s.callsign)
                row("Aircraft", s.aircraft)
                row("Route", [s.origin, s.destination].compactMap { $0 }.joined(separator: " → "))
                row("Phase", s.phaseLabel ?? s.phase)
                if let ete = s.ete { row("To go", "\(ete.nm) nm · \(ete.min) min") }
            }
            Section("Radio") {
                row("Tuned", s.tuned?.label)
                row("Next", s.next?.label)
                if let atc = s.lastAtc, let text = atc.text {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Last from \(atc.station ?? "ATC")").font(.caption).foregroundStyle(.secondary)
                        Text(text)
                    }
                }
            }
            Section("Assigned") {
                row("Squawk", s.squawk)
                row("Altitude", s.altitudeFt.map { $0 >= 18_000 ? "FL\($0 / 100)" : "\($0.formatted()) ft" })
                row("Runway", s.runway)
                row("Gate", s.gate)
            }
        }
        .navigationTitle(s.callsign ?? "Flight")
    }

    @ViewBuilder
    private func row(_ name: String, _ value: String?) -> some View {
        LabeledContent(name) {
            Text(value.flatMap { $0.isEmpty ? nil : $0 } ?? "—").monospacedDigit()
        }
    }
}

private struct NotFlyingRow: View {
    var body: some View {
        Label("Not flying. Press Start in LocalTC on your PC.", systemImage: "moon.zzz")
            .foregroundStyle(.secondary)
    }
}
