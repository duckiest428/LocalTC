import SwiftUI

/// The radio and the intercom, as a cockpit's audio panel has them: COM1, COM2 and INT (the copilot) along the bottom.
/// The channel picked is what the list shows and where a message goes; a dot on another one means something new was
/// said there. Each transmission LocalTC kept the audio of has a play button; the microphone in the message box takes
/// the words by speech recognition instead of typing.
struct CommsTab: View {
    @Environment(AppModel.self) private var model
    @State private var channel = Channel.com1
    @State private var seen: [Channel: Int] = [:]
    @State private var draft = ""
    @State private var sending = false
    @State private var problem: String?
    @State private var dictation = Dictation()
    @State private var player = ClipPlayer()
    @FocusState private var typing: Bool

    enum Channel: String, CaseIterable, Identifiable {
        case com1 = "COM1", com2 = "COM2", int = "INT"
        var id: String { rawValue }
        var listener: Listener { self == .com2 ? .com2 : self == .int ? .crew : .atc }
    }

    private func lines(on channel: Channel) -> [RadioLine] {
        let store = model.store
        return store.radio.filter { line in
            switch line.speaker {
            case .crew, .intercom: channel == .int
            case .system: channel != .int
            default: channel == .com1 ? store.com(of: line) == 1 : channel == .com2 && store.com(of: line) == 2
            }
        }
    }

    var body: some View {
        let store = model.store
        let shown = lines(on: channel)
        let crewOn = store.status.crew == true
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                List(shown) { line in
                    RadioRow(line: line, com: line.speaker == .crew || line.speaker == .intercom ? "INT" : "COM\(store.com(of: line))",
                             player: player) { id in player.toggle(id, from: model.connection) }
                        .id(line.id)
                }
                .listStyle(.plain)
                .overlay {
                    if shown.isEmpty {
                        ContentUnavailableView(channel == .int ? "Nothing on the intercom yet" : "Quiet on \(channel.rawValue)",
                                               systemImage: channel == .int ? "headphones" : "waveform",
                                               description: Text(channel == .int
                                                   ? (crewOn ? "The copilot speaks up when there's something to say, and answers what you say to it here."
                                                      : "The copilot isn't on in LocalTC (Quick Settings → Copilot → Intercom).")
                                                   : "ATC's calls and yours appear here as they happen."))
                    }
                }
                .onChange(of: shown.last?.id) { _, last in
                    seen[channel] = shown.count
                    if let last { withAnimation { proxy.scrollTo(last, anchor: .bottom) } }
                }
                .onAppear {
                    seen[channel] = shown.count
                    if let last = shown.last?.id { proxy.scrollTo(last, anchor: .bottom) }
                }
            }
            VStack(spacing: 8) {
                if channel != .int, let tuned = (channel == .com1 ? store.status.tuned?.label : nil) {
                    Text("COM1 \(tuned)").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                channels(crewOn: crewOn)
                HStack(spacing: 8) {
                    TextField(!model.connection.canSay ? "Typing works once connected"
                              : channel == .int ? "Say it to the copilot: \"flaps one\", \"fuel?\", \"brief\" ..."
                              : "Type a message on \(channel.rawValue) ...",
                              text: $draft, axis: .vertical)
                        .lineLimit(1...3)
                        .textFieldStyle(.roundedBorder)
                        .focused($typing)
                        .submitLabel(.send)
                        .onSubmit(send)
                        .disabled(!model.connection.canSay)
                        .accessibilityIdentifier("message")
                    Button {
                        typing = false
                        dictation.toggle(hints: hints) { draft = $0 }
                    } label: {
                        Image(systemName: dictation.listening ? "mic.fill" : "mic").font(.title3)
                            .symbolEffect(.pulse, isActive: dictation.listening)
                            .foregroundStyle(dictation.listening ? .red : .accentColor)
                    }
                    .disabled(!model.connection.canSay)
                    .accessibilityLabel(dictation.listening ? "Stop listening" : "Talk instead of typing")
                    .accessibilityIdentifier("dictate")
                    Button(action: send) {
                        Image(systemName: sending ? "ellipsis" : "paperplane.fill").font(.title3)
                    }
                    .disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty || sending || !model.connection.canSay)
                    .accessibilityLabel(channel == .int ? "Say it to the copilot" : "Transmit on \(channel.rawValue)")
                }
                if let problem = problem ?? dictation.problem {
                    Text(problem).font(.caption).foregroundStyle(.orange).frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(.horizontal)
            .padding(.vertical, 10)
            .background(.bar)
        }
        .navigationTitle("Comms")
        .navigationBarTitleDisplayMode(.inline)
        .onChange(of: store.status.crew) { _, on in if on != true && channel == .int { channel = .com1 } }
        .onDisappear { dictation.stop(); player.stop() }
    }

    /// COM1, COM2, INT: the channel picked lit up, a dot where there's something new.
    private func channels(crewOn: Bool) -> some View {
        HStack(spacing: 4) {
            ForEach(Channel.allCases) { c in
                let new = lines(on: c).count > (seen[c] ?? lines(on: c).count) && c != channel
                Button {
                    channel = c
                    seen[c] = lines(on: c).count
                } label: {
                    HStack(spacing: 4) {
                        Text(c.rawValue).font(.subheadline.weight(.semibold))
                        if c == .int { Text("Copilot").font(.caption2).opacity(0.75) }
                    }
                    .frame(maxWidth: .infinity, minHeight: 34)
                    .overlay(alignment: .topTrailing) {
                        if new { Circle().fill(.green).frame(width: 7, height: 7).padding(5) }
                    }
                    .foregroundStyle(channel == c ? Color.black : Color.primary)
                    .background(channel == c ? Color.accentColor : Color.clear, in: RoundedRectangle(cornerRadius: 9))
                }
                .buttonStyle(.plain)
                .disabled(c == .int && !crewOn)
                .opacity(c == .int && !crewOn ? 0.45 : 1)
                .accessibilityIdentifier("channel.\(c.rawValue)")
                .accessibilityAddTraits(channel == c ? .isSelected : [])
            }
        }
        .padding(4)
        .background(Color.secondary.opacity(0.15), in: RoundedRectangle(cornerRadius: 12))
    }

    /// What speech recognition should expect: the callsign, the stations and airports of this flight.
    private var hints: [String] {
        let s = model.store.status
        return [s.callsign, s.tuned?.station, s.next?.station, s.origin, s.destination].compactMap { $0 }
    }

    private func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !sending else { return }
        dictation.stop()
        sending = true
        Task {
            do {
                try await model.connection.say(text, to: channel.listener)
                draft = ""
                problem = nil
            } catch {
                problem = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
            }
            sending = false
        }
    }
}

/// One transmission: who (the station, you, the copilot), what was said, and on what; a play button when its audio was
/// kept. ATC on the left, you on the right, the copilot on the intercom in amber.
struct RadioRow: View {
    let line: RadioLine
    var com = "COM1"
    var player: ClipPlayer?
    var play: (String) -> Void = { _ in }

    var body: some View {
        switch line.speaker {
        case .system:
            Text(line.text ?? "")
                .font(.caption)
                .foregroundStyle(line.ok == false || line.level == "error" ? .red : line.level == "warn" ? .orange : .secondary)
                .frame(maxWidth: .infinity)
                .listRowSeparator(.hidden)
        default:
            let mine = line.speaker == .intercom || (line.speaker == .pilot && line.kind != "copilot")
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    if let id = line.audio { playButton(id) }
                    Text(header).font(.subheadline.weight(.semibold)).foregroundStyle(headerColor)
                    if line.live == true, line.atc == true, let cs = line.callsign {
                        // LocalTC's traffic: the controller to a real flight, by its callsign in its airline's colour.
                        Text("to").font(.caption).foregroundStyle(.secondary)
                        Text(cs).font(.subheadline.weight(.bold)).foregroundStyle(AirlineColour.of(line.ident ?? cs))
                    }
                }
                Text(line.text ?? "")
                    .font(line.speaker == .other ? .footnote : .body)
                    .foregroundStyle(line.speaker == .other ? .secondary : .primary)
                Text(meta).font(.caption2.monospacedDigit()).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 12).padding(.vertical, 9)
            .background(background, in: RoundedRectangle(cornerRadius: 14))
            .overlay {
                if line.level == "warn" { RoundedRectangle(cornerRadius: 14).stroke(.orange.opacity(0.6)) }
            }
            .frame(maxWidth: 520, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: mine ? .trailing : .leading)
            .listRowSeparator(.hidden)
        }
    }

    @ViewBuilder private func playButton(_ id: String) -> some View {
        let state = player.map { $0.playing == id ? "stop" : $0.loading == id ? "loading" : $0.missing.contains(id) ? "missing" : "play" } ?? "play"
        Button { play(id) } label: {
            Image(systemName: state == "stop" ? "stop.circle.fill" : state == "loading" ? "ellipsis.circle.fill" : "play.circle.fill")
                .font(.title3)
                .foregroundStyle(state == "missing" ? Color.secondary : Color.orange)
        }
        .buttonStyle(.borderless)
        .disabled(state == "missing")
        .accessibilityLabel(state == "stop" ? "Stop" : "Play this transmission")
    }

    private var header: String {
        switch line.speaker {
        case .pilot: line.kind == "copilot" ? "Copilot" : "You"
        case .crew: "Copilot"
        case .intercom: "You"
        case .traffic: line.callsign ?? line.station ?? "Other"
        case .other: line.station ?? "Other"
        default: line.station ?? "ATC"
        }
    }

    private var headerColor: Color {
        switch line.speaker {
        case .atc: .orange
        case .pilot: line.kind == "copilot" ? .cyan : .green
        case .crew: .yellow
        case .intercom: .green
        case .traffic: AirlineColour.of(line.ident ?? line.callsign ?? "")
        default: .secondary
        }
    }

    private var meta: String {
        [line.mhz.map { String(format: "%.3f", $0) }, com].compactMap { $0 }.joined(separator: " · ")
    }

    private var background: Color {
        switch line.speaker {
        case .pilot: line.kind == "copilot" ? Color.cyan.opacity(0.12) : Color.green.opacity(0.16)
        case .crew: Color.yellow.opacity(0.14)
        case .intercom: Color.green.opacity(0.12)
        case .other: Color.gray.opacity(0.1)
        default: Color.gray.opacity(0.18)
        }
    }
}

/// Each airline in its own colour (its livery's, where known), each of its flights a shade of it: the same as the
/// desktop app's radio log (app.js ``airlineColour``).
enum AirlineColour {
    static let hues: [String: Double] = [
        "RYR": 215, "BAW": 355, "EZY": 28, "DLH": 45, "AFR": 225, "KLM": 200, "UAE": 0, "QTR": 330, "AAL": 210,
        "DAL": 350, "UAL": 230, "SWA": 40, "JBU": 220, "ASA": 190, "ACA": 0, "WJA": 175, "IBE": 5, "VLG": 50,
        "SAS": 220, "FIN": 210, "THY": 0, "AUA": 0, "SWR": 0, "EIN": 140, "WZZ": 300, "NKS": 55, "FFT": 130,
        "VIR": 340, "QFA": 0, "ANA": 210, "JAL": 0, "CPA": 170, "SIA": 40, "ETD": 35,
    ]

    static func of(_ ident: String) -> Color {
        let id = ident.uppercased().filter { !$0.isWhitespace }
        let letters = id.prefix(3)
        let airline = letters.count == 3 && letters.allSatisfy(\.isLetter) && id.dropFirst(3).first?.isNumber == true
        var h = 0
        for c in (airline ? String(letters) : String(id.prefix(3))).unicodeScalars { h = (h * 31 + Int(c.value)) % 360 }
        let hue = hues[String(letters)] ?? Double(h)
        var n = 0
        for c in (airline ? String(id.dropFirst(3)) : id).unicodeScalars { n = (n * 17 + Int(c.value)) % 997 }
        let light = 0.50 + Double(n % 5) * 0.06
        return Color(hue: hue / 360, saturation: airline ? 0.75 : 0.25, lightness: light)
    }
}

extension Color {
    /// HSL, as the desktop app's colours are given.
    init(hue: Double, saturation s: Double, lightness l: Double) {
        let v = l + s * min(l, 1 - l)
        let sv = v == 0 ? 0 : 2 * (1 - l / v)
        self.init(hue: hue, saturation: sv, brightness: v)
    }
}
