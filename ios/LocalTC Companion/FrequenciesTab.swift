import SwiftUI

/// Who to talk to: the frequency tuned and the next one, then every frequency at the flight's airports.
struct FrequenciesTab: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let s = model.store.status
        List {
            Section("Now") {
                StationRow(title: "Tuned", station: s.tuned, accent: .green)
                StationRow(title: "Next", station: s.next == s.tuned ? nil : s.next, accent: .orange)
            }
            ForEach(model.store.airports) { airport in
                Section {
                    if let atis = airport.atis {
                        LabeledContent("ATIS", value: "Information \(atis)")
                    }
                    ForEach(airport.frequencies, id: \.self) { f in
                        FrequencyRow(frequency: f, tuned: matches(f.mhz, s.tuned?.mhz), next: matches(f.mhz, s.next?.mhz))
                    }
                } header: {
                    Text("\(airport.icao) · \(airport.name ?? "")")
                } footer: {
                    Text(airport.role == "departure" ? "Departure" : "Arrival")
                }
            }
        }
        .overlay {
            if model.store.airports.isEmpty && !s.active { NotFlying() }
        }
        .navigationTitle("Frequencies")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func matches(_ a: Double, _ b: Double?) -> Bool {
        guard let b else { return false }
        return abs(a - b) < 0.003
    }
}

private struct StationRow: View {
    let title: String
    let station: Station?
    let accent: Color

    var body: some View {
        LabeledContent {
            Text(station?.mhz.map { String(format: "%.3f", $0) } ?? "—").font(.body.monospacedDigit().bold())
                .foregroundStyle(station == nil ? .secondary : accent)
        } label: {
            VStack(alignment: .leading) {
                Text(title).font(.caption).foregroundStyle(.secondary)
                Text(station?.station ?? "—")
            }
        }
    }
}

private struct FrequencyRow: View {
    let frequency: FlightAirport.Frequency
    let tuned: Bool
    let next: Bool

    var body: some View {
        LabeledContent {
            HStack(spacing: 6) {
                if tuned { Image(systemName: "dot.radiowaves.left.and.right").foregroundStyle(.green) }
                if next && !tuned { Image(systemName: "arrow.right.circle").foregroundStyle(.orange) }
                Text(frequency.display).font(.body.monospacedDigit())
                    .foregroundStyle(tuned ? .green : next ? .orange : .primary)
            }
        } label: {
            VStack(alignment: .leading) {
                Text(frequency.label ?? frequency.kind?.uppercased() ?? "").font(.caption.bold()).foregroundStyle(.secondary)
                Text(frequency.name ?? "")
            }
        }
        .accessibilityElement(children: .combine)
    }
}
