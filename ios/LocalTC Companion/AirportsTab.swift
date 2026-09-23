import SwiftUI

/// The flight's airports: field elevation, the ATIS letter, and the runways with their length and ILS.
struct AirportsTab: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        List {
            ForEach(model.store.airports) { airport in
                Section {
                    LabeledContent("Role", value: airport.role == "departure" ? "Departure" : "Arrival")
                    if let elev = airport.elevFt { LabeledContent("Elevation", value: "\(elev.formatted()) ft") }
                    if let atis = airport.atis { LabeledContent("ATIS", value: "Information \(atis)") }
                    ForEach(airport.runways, id: \.self) { runway in
                        RunwayRow(runway: runway)
                    }
                } header: {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(airport.icao).font(.title3.bold()).foregroundStyle(.primary)
                        Text(airport.name ?? "").textCase(nil)
                    }
                }
            }
        }
        .overlay {
            if model.store.airports.isEmpty {
                ContentUnavailableView("No airports yet", systemImage: "mappin.and.ellipse",
                                       description: Text("The departure and arrival airports appear once a flight starts in LocalTC."))
            }
        }
        .navigationTitle("Airports")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct RunwayRow: View {
    let runway: FlightAirport.Runway

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(runway.name).font(.headline.monospacedDigit())
                Spacer()
                if let length = runway.lengthFt {
                    Text("\(length.formatted()) ft").monospacedDigit().foregroundStyle(.secondary)
                }
            }
            HStack(spacing: 10) {
                if let heading = runway.headingMag { Text(String(format: "%03d°", heading)).monospacedDigit() }
                if let ils = runway.ils, !ils.isEmpty { Text("ILS \(ils.joined(separator: ", "))") }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
    }
}
