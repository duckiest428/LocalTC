import SwiftUI

/// The electronic flight bag to come, shown greyed out: what it'll be, not yet usable.
struct ComingSoonView: View {
    var body: some View {
        ZStack(alignment: .top) {
            FlightBagPreview()
                .disabled(true)
                .grayscale(1)
                .opacity(0.45)
                .accessibilityHidden(true)

            VStack(spacing: 6) {
                Text("Coming soon").font(.headline)
                Text("Charts, checklists, performance and weight and balance, with the flight.")
                    .font(.subheadline)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.secondary)
            }
            .padding()
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
            .padding()
        }
        .navigationTitle("EFB")
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
