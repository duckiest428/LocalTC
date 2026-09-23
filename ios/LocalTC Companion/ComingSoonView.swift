import SwiftUI

/// The two sections to come, shown greyed out: what they'll be, not yet usable.
struct ComingSoonView: View {
    enum Section { case cockpit, flightBag }
    let section: Section

    var body: some View {
        ZStack(alignment: .top) {
            Group {
                switch section {
                case .cockpit: CockpitPreview()
                case .flightBag: FlightBagPreview()
                }
            }
            .disabled(true)
            .grayscale(1)
            .opacity(0.45)
            .accessibilityHidden(true)

            VStack(spacing: 6) {
                Text("Coming soon").font(.headline)
                Text(section == .cockpit
                     ? "Fly from the phone: autopilot, radios and transponder, lights and switches."
                     : "Charts, checklists, performance and weight and balance, with the flight.")
                    .font(.subheadline)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.secondary)
            }
            .padding()
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
            .padding()
        }
        .navigationTitle(section == .cockpit ? "Cockpit" : "Flight Bag")
    }
}

private struct CockpitPreview: View {
    var body: some View {
        List {
            Section("Autopilot") {
                HStack {
                    ForEach(["AP", "FD", "HDG", "NAV", "ALT", "VS", "APR"], id: \.self) { mode in
                        Text(mode).font(.caption.bold()).frame(maxWidth: .infinity).padding(.vertical, 8)
                            .background(Color.secondary.opacity(0.2), in: RoundedRectangle(cornerRadius: 6))
                    }
                }
                LabeledContent("Heading", value: "088")
                LabeledContent("Altitude", value: "FL350")
                LabeledContent("Vertical speed", value: "0")
            }
            Section("Radios") {
                LabeledContent("COM1", value: "133.650 ⇄ 119.200")
                LabeledContent("NAV1", value: "110.300 ⇄ 108.900")
                LabeledContent("Transponder", value: "4512 · ALT")
            }
            Section("Lights") {
                ForEach(["Landing", "Taxi", "Beacon", "Strobe", "Nav"], id: \.self) { Toggle($0, isOn: .constant(true)) }
            }
        }
    }
}

private struct FlightBagPreview: View {
    var body: some View {
        List {
            Section("Charts") {
                Label("KPHX · RNAV (GPS) Y RWY 26", systemImage: "map")
                Label("KPHX · Airport diagram", systemImage: "square.grid.3x3")
            }
            Section("Checklists") {
                ForEach(["Before start", "Before takeoff", "Descent", "Approach", "Shutdown"], id: \.self) {
                    Label($0, systemImage: "checklist")
                }
            }
            Section("Performance") {
                LabeledContent("Takeoff distance", value: "1,840 m")
                LabeledContent("Landing weight", value: "61,200 kg")
                LabeledContent("CG", value: "28.4 %MAC")
            }
        }
    }
}
