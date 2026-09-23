import SwiftUI

struct RadioTab: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let lines = model.store.radio
        ScrollViewReader { proxy in
            List(lines) { line in
                RadioRow(line: line).id(line.id)
            }
            .listStyle(.plain)
            .overlay {
                if lines.isEmpty {
                    ContentUnavailableView("Quiet on the frequency", systemImage: "waveform",
                                           description: Text("ATC's calls and yours appear here as they happen."))
                }
            }
            .onChange(of: lines.last?.id) { _, last in
                if let last { withAnimation { proxy.scrollTo(last, anchor: .bottom) } }
            }
            .onAppear { if let last = lines.last?.id { proxy.scrollTo(last, anchor: .bottom) } }
        }
        .navigationTitle("Radio")
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct RadioRow: View {
    let line: RadioLine

    var body: some View {
        switch line.speaker {
        case .atc, .pilot:
            VStack(alignment: line.speaker == .pilot ? .trailing : .leading, spacing: 3) {
                Text(header).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                Text(line.text ?? "")
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(line.speaker == .atc ? Color.green.opacity(0.15) : Color.blue.opacity(0.15),
                                in: RoundedRectangle(cornerRadius: 14))
            }
            .frame(maxWidth: .infinity, alignment: line.speaker == .pilot ? .trailing : .leading)
            .listRowSeparator(.hidden)
        case .system:
            Text(line.text ?? "")
                .font(.caption)
                .foregroundStyle(line.ok == false || line.level == "error" ? .red : line.level == "warn" ? .orange : .secondary)
                .frame(maxWidth: .infinity)
                .listRowSeparator(.hidden)
        }
    }

    private var header: String {
        switch line.speaker {
        case .pilot: line.kind == "copilot" ? "Copilot" : "You"
        default: [line.station, line.mhz.map { String(format: "%.3f", $0) }].compactMap { $0 }.joined(separator: " · ")
        }
    }
}
