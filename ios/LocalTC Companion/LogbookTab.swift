import SwiftUI

/// The account's logbook: the totals, then every flight synced from the PC. A flight whose replay the pilot
/// uploaded (from the Logbook in the desktop app) plays again, map and radio together.
struct LogbookTab: View {
    @Environment(AppModel.self) private var model
    @State private var stats: LogbookStats?
    @State private var flights: [LogbookFlight] = []
    @State private var next: String?
    @State private var error: String?
    @State private var loading = false

    var body: some View {
        List {
            if let stats {
                Section {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 96), spacing: 10)], spacing: 10) {
                        tile("\(stats.flights)", "flights")
                        tile(String(format: "%.1f", stats.airHours), "hours flown")
                        tile(Int(stats.distanceNm).formatted(), "nm flown")
                        tile(stats.averageLandingFpm.map { "\($0)" } ?? "—", "avg landing fpm")
                        tile(stats.readbackAccuracy.map { "\(Int(($0 * 100).rounded()))%" } ?? "—", "readbacks right")
                    }
                    .padding(.vertical, 4)
                }
            }
            Section {
                ForEach(flights) { flight in
                    if flight.hasReplay == true {
                        NavigationLink { ReplayView(flight: flight) } label: { FlightRow(flight: flight) }
                            .accessibilityIdentifier("replay-\(flight.id)")
                    } else {
                        FlightRow(flight: flight)
                    }
                }
                if next != nil {
                    Button("Load more") { Task { await load(more: true) } }
                }
            } header: {
                Text("Flights")
            } footer: {
                if !flights.isEmpty {
                    Text("A flight with \(Image(systemName: "play.circle.fill")) plays again. Upload a replay from the Logbook in the LocalTC app on your PC.")
                }
            }
        }
        .overlay {
            if flights.isEmpty && !loading {
                if let error {
                    ContentUnavailableView("Couldn't load the logbook", systemImage: "exclamationmark.triangle", description: Text(error))
                } else {
                    ContentUnavailableView("No flights yet", systemImage: "book.closed",
                                           description: Text("Each flight in LocalTC on your PC appears here when it ends, while it's signed in to this account."))
                }
            }
        }
        .refreshable { await load() }
        .task { if flights.isEmpty { await load() } }
        .navigationTitle("Logbook")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func tile(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(.title3.bold().monospacedDigit())
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    private func load(more: Bool = false) async {
        loading = true
        defer { loading = false }
        do {
            if more {
                let page = try await model.api.flights(before: next)
                flights += page.flights
                next = page.next
            } else {
                async let s = model.api.stats()
                async let page = model.api.flights()
                let (totals, first) = try await (s, page)
                stats = totals
                flights = first.flights
                next = first.next
            }
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

private struct FlightRow: View {
    let flight: LogbookFlight

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text("\(flight.origin.isEmpty ? "?" : flight.origin) → \(flight.destination.isEmpty ? "?" : flight.destination)")
                        .font(.headline.monospaced())
                    if !flight.landed { Text("no landing").font(.caption).foregroundStyle(.secondary) }
                }
                Text([flight.callsign, flight.aircraft, flight.date?.formatted(date: .abbreviated, time: .omitted)]
                    .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 3) {
                Text(hm(flight.airMin)).font(.subheadline.monospacedDigit())
                Text(flight.landingVsFpm.map { "\($0) fpm" } ?? "—").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
            if flight.hasReplay == true {
                Image(systemName: "play.circle.fill").foregroundStyle(.green).font(.title3)
                    .accessibilityLabel("Has a replay")
            }
        }
        .padding(.vertical, 2)
    }

    private func hm(_ minutes: Double?) -> String {
        guard let minutes else { return "—" }
        let m = Int(minutes.rounded())
        return String(format: "%d:%02d", m / 60, m % 60)
    }
}
