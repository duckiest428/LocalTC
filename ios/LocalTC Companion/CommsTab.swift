import SwiftUI

/// The radio: everything said on the frequencies, filtered by COM, and a line to type a call to ATC.
struct CommsTab: View {
    @Environment(AppModel.self) private var model
    @State private var filter = Filter.all
    @State private var draft = ""
    @State private var sending = false
    @State private var problem: String?
    @FocusState private var typing: Bool

    enum Filter: String, CaseIterable { case all = "All", com1 = "COM1", com2 = "COM2" }

    var body: some View {
        let store = model.store
        let lines = store.radio.filter { line in
            switch filter {
            case .all: true
            case .com1: line.speaker == .system || store.com(of: line) == 1
            case .com2: line.speaker == .system || store.com(of: line) == 2
            }
        }
        VStack(spacing: 0) {
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
            VStack(spacing: 8) {
                if let tuned = store.status.tuned?.label {
                    Text("COM1 \(tuned)").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                Picker("Radio", selection: $filter) {
                    ForEach(Filter.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("comFilter")
                HStack(spacing: 10) {
                    TextField(model.connection.local == nil ? "Typing to ATC works on the same Wi-Fi" : "Type a message ...",
                              text: $draft, axis: .vertical)
                        .lineLimit(1...3)
                        .textFieldStyle(.roundedBorder)
                        .focused($typing)
                        .submitLabel(.send)
                        .onSubmit(send)
                        .disabled(model.connection.local == nil)
                        .accessibilityIdentifier("message")
                    Button(action: send) {
                        Image(systemName: sending ? "ellipsis" : "paperplane.fill").font(.title3)
                    }
                    .disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty || sending || model.connection.local == nil)
                    .accessibilityLabel("Transmit on COM1")
                }
                if let problem {
                    Text(problem).font(.caption).foregroundStyle(.orange).frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(.horizontal)
            .padding(.vertical, 10)
            .background(.bar)
        }
        .navigationTitle("Comms")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !sending else { return }
        sending = true
        Task {
            do {
                try await model.connection.say(text)
                draft = ""
                problem = nil
            } catch {
                problem = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
            }
            sending = false
        }
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
