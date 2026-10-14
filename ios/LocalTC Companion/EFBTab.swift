import MapKit
import SwiftUI

/// The EFB, with the flight: the copilot's checklists, the briefing, the charts, the airports, the gates and Airport
/// Ops, the scratchpad and the achievements. What changes in flight arrives with the "efb" message; the gates, a chart
/// and the full list of achievements are asked of the PC on the same Wi-Fi.
struct EFBTab: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let efb = model.store.efb
        List {
            if let hold = efb?.atc?.hold, hold.active {
                Section("ATC") {
                    HoldSummary(hold: hold)
                }
            }
            if let checklist = efb?.checklist, checklist.running {
                Section("Now") {
                    NavigationLink { ChecklistsView() } label: {
                        Label("\(checklist.name ?? "Checklist") checklist: \(checklist.status ?? "")", systemImage: "checklist")
                    }
                }
            }
            Section {
                row("Checklists", "checklist", "efb.checklists") { ChecklistsView() }
                row("Briefing", "cloud.sun", "efb.briefing") { BriefingView() }
                row("Charts", "map", "efb.charts") { ChartsView() }
                row("Airports", "mappin.and.ellipse", "efb.airports") { AirportsTab() }
                row("Gates", "square.grid.3x3", "efb.gates") { GatesView() }
                row("Airport Ops", "person.2.badge.gearshape", "efb.ops") { OpsView() }
                row("Scratchpad", "square.and.pencil", "efb.scratchpad") { ScratchpadView() }
                row("Achievements", "rosette", "efb.achievements") { AchievementsView() }
            } footer: {
                Text("For flight simulation only, not for real-world navigation.")
            }
        }
        .navigationTitle("EFB")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func row<Destination: View>(_ title: String, _ icon: String, _ id: String,
                                        @ViewBuilder _ destination: @escaping () -> Destination) -> some View {
        NavigationLink { destination() } label: { Label(title, systemImage: icon) }
            .accessibilityIdentifier(id)
    }
}

private struct HoldSummary: View {
    let hold: AtcOperations.Hold

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Holding at \(hold.fix)").font(.headline)
            Text([hold.inbound.map { "inbound \(String(format: "%03d", $0))°" }, hold.turn.map { "\($0) turns" },
                  hold.altitudeFt.map { "\($0.formatted()) ft" }].compactMap { $0 }.joined(separator: " · "))
                .font(.subheadline)
            Text([hold.reason == "arrival traffic" ? hold.position.map { "number \($0)" } : nil, hold.reason,
                  hold.efc.map { "EFC \($0)Z" }].compactMap { $0 }.joined(separator: " · "))
                .font(.subheadline).foregroundStyle(.secondary)
        }
    }
}

/// Something asked of LocalTC that failed: shown under the view.
private struct ProblemText: View {
    let problem: String?

    var body: some View {
        if let problem { Text(problem).font(.footnote).foregroundStyle(.red) }
    }
}

// MARK: - Checklists

/// The copilot's checklist as it reads it, item by item: ticked, asked, held on a wrong one, skipped. Run another,
/// answer "checked", or hold, resume, skip or stop it, as said to the copilot on the intercom.
struct ChecklistsView: View {
    @Environment(AppModel.self) private var model
    @State private var problem: String?

    var body: some View {
        let efb = model.store.efb
        List {
            if let checklist = efb?.checklist, let lines = checklist.lines {
                Section {
                    ForEach(Array(lines.enumerated()), id: \.offset) { i, line in
                        ChecklistLineRow(line: line, current: checklist.running && i == checklist.index)
                    }
                } header: {
                    Text("\(checklist.name ?? "") · \(checklist.status ?? "")")
                } footer: {
                    Text(checklist.source ?? "")
                }
                if checklist.running {
                    Section {
                        HStack {
                            command("Checked", "check")
                            command("Skip", "skip")
                            command("Hold", "pause")
                            command("Resume", "resume")
                            command("Stop", "stop")
                        }
                        .buttonStyle(.bordered)
                    }
                }
            }
            Section {
                if let book = efb?.book {
                    ForEach(book.checklists) { item in
                        HStack {
                            VStack(alignment: .leading) {
                                Text(item.name)
                                Text((item.phase ?? "").replacingOccurrences(of: "_", with: " ")).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("Run") { send(.checklist(action: "run", id: item.id)) }.buttonStyle(.bordered)
                        }
                    }
                } else {
                    Text("The aircraft's checklists show once the copilot is on in LocalTC. An aircraft without any gets none: the copilot doesn't make them up.")
                        .foregroundStyle(.secondary)
                }
            } header: {
                Text(efb?.book?.name ?? "Checklists")
            } footer: {
                VStack(alignment: .leading) {
                    Text(efb?.book?.source ?? "")
                    ProblemText(problem: problem)
                }
            }
        }
        .navigationTitle("Checklists")
    }

    private func command(_ title: String, _ action: String) -> some View {
        Button(title) { send(.checklist(action: action)) }.font(.caption)
    }

    private func send(_ command: EfbCommand) {
        Task {
            do { try await model.connection.efb(command); problem = nil } catch { problem = error.localizedDescription }
        }
    }
}

private struct ChecklistLineRow: View {
    let line: EfbChecklist.Line
    let current: Bool

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Image(systemName: icon).foregroundStyle(color).frame(width: 18)
            Text(line.challenge)
            Spacer()
            Text(line.response ?? "").font(.subheadline.monospaced()).foregroundStyle(.secondary)
        }
        .padding(.vertical, 2)
        .listRowBackground(current ? Color.teal.opacity(0.18) : nil)
    }

    private var icon: String {
        switch line.result ?? "" {
        case "ok": "checkmark.circle.fill"
        case "wrong": "exclamationmark.triangle.fill"
        case "skipped": "arrow.uturn.right.circle"
        case "read": "circle.fill"
        default: current ? "arrow.right.circle" : "circle"
        }
    }

    private var color: Color {
        switch line.result ?? "" {
        case "ok": .green
        case "wrong": .red
        case "skipped": .orange
        default: .secondary
        }
    }
}

// MARK: - Briefing

/// The flight's airports' METAR, TAF and NOTAMs (the ones ATC works to marked), and the ATIS ATC gives.
struct BriefingView: View {
    @Environment(AppModel.self) private var model
    @State private var icao: String?

    var body: some View {
        let briefings = model.store.efb?.briefings ?? [:]
        let airports = briefings.keys.sorted()
        let pick = icao ?? airports.first
        List {
            if airports.count > 1 {
                Picker("Airport", selection: Binding(get: { pick ?? "" }, set: { icao = $0 })) {
                    ForEach(airports, id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.segmented)
            }
            if let pick, let b = briefings[pick] {
                Section("METAR") { Text(b.metar?.isEmpty == false ? b.metar! : "Not known yet").font(.footnote.monospaced()) }
                Section("TAF") { Text(b.taf?.raw ?? b.taf?.why ?? "None").font(.footnote.monospaced()) }
                if let atis = b.atis {
                    Section("ATIS") {
                        Text("Information \(atis.letter ?? "?") · runway \(atis.runway ?? "") · \(atis.approach ?? "")")
                        Text(atis.source ?? "").font(.caption).foregroundStyle(.secondary)
                    }
                }
                Section("NOTAMs") {
                    let items = b.notams?.items ?? []
                    if items.isEmpty { Text(b.notams?.why ?? "None in force").foregroundStyle(.secondary) }
                    ForEach(items, id: \.self) { n in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text(n.id ?? "").font(.caption.bold())
                                if n.forAtc { Text("ATC works to it").font(.caption2).padding(.horizontal, 4).background(.orange.opacity(0.3), in: Capsule()) }
                            }
                            Text(n.text ?? "").font(.caption)
                        }
                        .opacity(n.inForce == false ? 0.6 : 1)
                    }
                }
            }
        }
        .overlay {
            if airports.isEmpty {
                ContentUnavailableView("No briefing yet", systemImage: "cloud.sun",
                                       description: Text(model.store.efb == nil
                                           ? "The flight's airports' weather and NOTAMs appear once a flight starts in LocalTC."
                                           : "Fetching the weather and NOTAMs: they show once LocalTC knows the flight's airports (from the sim or the flight plan)."))
            }
        }
        .navigationTitle("Briefing")
    }
}

// MARK: - Charts

/// The flight's airports' charts from ChartFox (signed in in the app), by kind; one opens with the aircraft on it when
/// it's georeferenced. On the PC's Wi-Fi: its link is asked of LocalTC.
struct ChartsView: View {
    @Environment(AppModel.self) private var model
    @State private var icao: String?

    var body: some View {
        let efb = model.store.efb
        let charts = efb?.charts ?? [:]
        let airports = charts.keys.sorted()
        let pick = icao ?? airports.first
        List {
            if efb?.chartfox?.connected != true {
                Text("Connect ChartFox in the LocalTC app (EFB → Charts) to see the charts here.").foregroundStyle(.secondary)
            }
            if airports.count > 1 {
                Picker("Airport", selection: Binding(get: { pick ?? "" }, set: { icao = $0 })) {
                    ForEach(airports, id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.segmented)
            }
            if let pick, let groups = charts[pick] {
                ForEach(groups.keys.sorted(), id: \.self) { kind in
                    Section(kind) {
                        ForEach(groups[kind] ?? []) { chart in
                            NavigationLink { ChartScreen(chart: chart) } label: {
                                HStack {
                                    Text(chart.name)
                                    if chart.hasGeoref == true { Spacer(); Image(systemName: "location.circle").foregroundStyle(.secondary) }
                                }
                            }
                        }
                    }
                }
            }
            Section {
                ChartCredit(credit: efb?.chartfox?.credit)
            }
        }
        .navigationTitle("Charts")
    }
}

private struct ChartCredit: View {
    let credit: ChartFoxInfo.Credit?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let url = URL(string: credit?.url ?? "https://chartfox.org") {
                Link(credit?.text ?? "Chart data powered by ChartFox", destination: url)
            }
            if let privacy = URL(string: credit?.privacy ?? "https://chartfox.org/privacy") {
                Link("ChartFox privacy policy", destination: privacy).font(.caption)
            }
            Text(credit?.disclaimer ?? "For flight simulation only. Not for real-world navigation.").font(.caption).foregroundStyle(.secondary)
        }
    }
}

private struct ChartScreen: View {
    @Environment(AppModel.self) private var model
    let chart: ChartItem
    @State private var view: ChartView?
    @State private var problem: String?

    var body: some View {
        GeometryReader { geo in
            ScrollView([.horizontal, .vertical]) {
                if let view, let link = view.viewUrl, let url = URL(string: link) {
                    if link.lowercased().contains(".pdf") {
                        Link("Open \(view.name)", destination: url).padding()
                    } else {
                        ZStack(alignment: .topLeading) {
                            AsyncImage(url: url) { image in
                                image.resizable().scaledToFit().frame(height: geo.size.height)
                            } placeholder: { ProgressView().frame(width: geo.size.width, height: geo.size.height) }
                            if let own = view.own, own.count == 2 {
                                Image(systemName: "airplane").foregroundStyle(.red).font(.title2)
                                    .position(x: own[0], y: own[1])
                            }
                        }
                    }
                } else if let problem {
                    Text(problem).foregroundStyle(.secondary).padding()
                } else {
                    ProgressView().padding()
                }
            }
            .task { await load(height: Int(geo.size.height)) }
        }
        .safeAreaInset(edge: .bottom) { ChartCredit(credit: view?.credit).padding(8).background(.bar) }
        .navigationTitle(chart.name)
        .navigationBarTitleDisplayMode(.inline)
    }

    private func load(height: Int) async {
        do {
            guard let data = try await model.connection.efb(.chart(id: chart.id, height: max(400, height))) else { return }
            view = try JSONDecoder.localtc.decode(ChartView.self, from: data)
        } catch {
            problem = error.localizedDescription
        }
    }
}

// MARK: - Gates

/// The destination's stands, as Airport Ops sees them: free, occupied, yours, or too small; your airline's or all of
/// them. Tap a free one to ask Airport Ops for it: approved, ground sends you there. On the PC's Wi-Fi.
struct GatesView: View {
    @Environment(AppModel.self) private var model
    @State private var map: GateMap?
    @State private var problem: String?
    @State private var mine = false
    @State private var asking: GateMap.Stand?

    var body: some View {
        VStack(spacing: 0) {
            if let map {
                Picker("Gates", selection: $mine) {
                    Text("\(map.operator_ ?? "My airline") gates").tag(true)
                    Text("All gates").tag(false)
                }
                .pickerStyle(.segmented)
                .padding(8)
                Map {
                    ForEach(shown(map)) { stand in
                        Annotation(stand.label, coordinate: CLLocationCoordinate2D(latitude: stand.lat, longitude: stand.lon)) {
                            Circle().fill(color(stand, map)).stroke(.black.opacity(0.6), lineWidth: 1)
                                .frame(width: stand.index == map.assigned ? 16 : 11, height: stand.index == map.assigned ? 16 : 11)
                                .onTapGesture { asking = stand }
                        }
                    }
                }
                GateLegend(assigned: map.assignedName)
            } else if let problem {
                ContentUnavailableView("Gates aren't here", systemImage: "square.grid.3x3", description: Text(problem))
            } else {
                ProgressView().frame(maxHeight: .infinity)
            }
        }
        .confirmationDialog(asking.map { "Ask Airport Ops for \($0.name)?" } ?? "", isPresented: Binding(get: { asking != nil }, set: { if !$0 { asking = nil } }),
                            titleVisibility: .visible) {
            Button("Request \(asking?.name ?? "")") {
                if let stand = asking { request(stand) }
            }
        }
        .task { await load() }
        .navigationTitle(map.map { "Gates · \($0.icao)" } ?? "Gates")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func shown(_ map: GateMap) -> [GateMap.Stand] {
        guard mine, let op = map.operator_, !op.isEmpty else { return map.stands }
        let theirs = map.stands.filter { $0.airline == op || $0.index == map.assigned }
        return theirs.isEmpty ? map.stands : theirs
    }

    private func color(_ s: GateMap.Stand, _ map: GateMap) -> Color {
        if s.index == map.assigned { return Zone.gate }
        if s.occupied == true || s.taken == true { return .red }
        if s.fits == false { return .gray }
        return .green
    }

    private func load() async {
        do {
            guard let data = try await model.connection.efb(.gates()) else { return }
            map = try JSONDecoder.localtc.decode(GateMap.self, from: data)
        } catch {
            problem = error.localizedDescription
        }
    }

    private func request(_ stand: GateMap.Stand) {
        Task {
            do {
                try await model.connection.efb(.gate(stand.label))
                await load()
            } catch {
                problem = error.localizedDescription
            }
        }
    }
}

private struct GateLegend: View {
    let assigned: String?

    var body: some View {
        HStack(spacing: 12) {
            dot(.green, "free"); dot(.red, "occupied"); dot(Zone.gate, assigned.map { "yours: \($0)" } ?? "yours"); dot(.gray, "too small")
        }
        .font(.caption)
        .padding(8)
    }

    private func dot(_ c: Color, _ label: String) -> some View {
        HStack(spacing: 4) { Circle().fill(c).frame(width: 9, height: 9); Text(label) }
    }
}

// MARK: - Airport Ops

/// The ramp: the turnaround's services (GSX's when it's running), de-icing, closures, the messages, notes to Ops; and
/// what ATC is working the flight to (a hold, conditions, reroutes).
struct OpsView: View {
    @Environment(AppModel.self) private var model
    @State private var note = ""
    @State private var problem: String?

    var body: some View {
        let efb = model.store.efb
        let ops = efb?.ops
        List {
            Section(ops?.icao.map { "Airport Ops · \($0)" } ?? "Airport Ops") {
                if let services = ops?.services, !services.isEmpty {
                    ForEach(services.keys.sorted(), id: \.self) { name in
                        LabeledContent(name.capitalized, value: services[name]?.state ?? "")
                    }
                    if ops?.gsx != true {
                        Text("GSX isn't running: boarding, catering and cleaning aren't tracked.").font(.caption).foregroundStyle(.secondary)
                    }
                } else {
                    Text(efb == nil ? "Starts with a flight in LocalTC." : "No services reported yet: fuel shows once it's flowing, the rest with GSX.")
                        .foregroundStyle(.secondary)
                }
                if let deice = ops?.deice, deice.required == true {
                    Label("De-icing: number \(deice.position ?? 1), about \(deice.waitMin ?? 0) min (\(deice.why ?? ""))", systemImage: "snowflake")
                }
                ForEach(ops?.closures ?? [], id: \.self) { Label($0, systemImage: "exclamationmark.triangle").font(.caption) }
            }
            if let atc = efb?.atc {
                Section("ATC") {
                    if let hold = atc.hold, hold.active { HoldSummary(hold: hold) }
                    ForEach((atc.conditions ?? []).filter { $0.state == "active" }, id: \.self) { c in
                        Text("\(c.kind.replacingOccurrences(of: "_", with: " ")) \(c.traffic ?? "") \(c.runway.map { "runway \($0)" } ?? "")")
                    }
                    ForEach((atc.routing ?? []).filter { $0.state == "active" }, id: \.self) { a in
                        Text("\(a.kind): \(a.fix ?? "") \(a.reason ?? "")")
                    }
                    if let min = atc.minimumAltitudeFt { LabeledContent("Minimum altitude here", value: "\(min.formatted()) ft") }
                }
            }
            Section("Messages") {
                ForEach((ops?.messages ?? []).reversed(), id: \.self) { m in
                    VStack(alignment: .leading) {
                        Text(m.text)
                        Text(Date(timeIntervalSince1970: m.t), style: .time).font(.caption).foregroundStyle(.secondary)
                    }
                }
                HStack {
                    TextField("A note to Ops (maintenance, dispatch)", text: $note)
                    Button("Send") { send() }.disabled(note.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                ProblemText(problem: problem)
            }
        }
        .navigationTitle("Airport Ops")
    }

    private func send() {
        let text = note
        Task {
            do { try await model.connection.efb(.opsNote(text: text)); note = ""; problem = nil } catch { problem = error.localizedDescription }
        }
    }
}

// MARK: - Scratchpad

/// The notes, the same as the app's EFB: written here or there, by flight; what looks like a clearance picked out,
/// marked as the pilot's note, not the sim's.
struct ScratchpadView: View {
    @Environment(AppModel.self) private var model
    @State private var draft = ""
    @State private var problem: String?

    var body: some View {
        let notes = (model.store.efb?.scratchpad ?? []).sorted { ($0.pinned == true ? 1 : 0, $0.t ?? 0) > ($1.pinned == true ? 1 : 0, $1.t ?? 0) }
        List {
            Section {
                TextField("A clearance, the ATIS, a frequency ...", text: $draft, axis: .vertical)
                Button("Add") { add() }.disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                ProblemText(problem: problem)
            }
            ForEach(notes) { note in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        if note.pinned == true { Image(systemName: "pin.fill").foregroundStyle(.yellow) }
                        Text(note.kind ?? "note").font(.caption2.bold()).padding(.horizontal, 5).background(.secondary.opacity(0.2), in: Capsule())
                        Spacer()
                        if let t = note.t { Text(Date(timeIntervalSince1970: t), style: .time).font(.caption).foregroundStyle(.secondary) }
                    }
                    Text(note.text).font(.body.monospaced())
                    if let picked = note.picked, !picked.isEmpty {
                        Text(picked.keys.sorted().map { "\($0) \(picked[$0] ?? "")" }.joined(separator: " · "))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                .swipeActions {
                    Button(role: .destructive) { send(.deleteNote(id: note.id)) } label: { Label("Delete", systemImage: "trash") }
                    Button { send(.editNote(id: note.id, pinned: note.pinned != true)) } label: {
                        Label(note.pinned == true ? "Unpin" : "Pin", systemImage: "pin")
                    }
                    .tint(.yellow)
                }
            }
        }
        .navigationTitle("Scratchpad")
    }

    private func add() {
        let text = draft
        Task {
            do { try await model.connection.efb(.addNote(text: text)); draft = ""; problem = nil } catch { problem = error.localizedDescription }
        }
    }

    private func send(_ command: EfbCommand) {
        Task {
            do { try await model.connection.efb(command) } catch { problem = error.localizedDescription }
        }
    }
}

// MARK: - Achievements

/// What's been earned, what met it, and the progress towards the milestones (the full list on the PC's Wi-Fi; else
/// the count and the newest).
struct AchievementsView: View {
    @Environment(AppModel.self) private var model
    @State private var list: AchievementList?

    var body: some View {
        let summary = model.store.efb?.achievements
        List {
            if let summary {
                Section {
                    LabeledContent("Earned", value: "\(summary.earned ?? 0) of \(summary.total ?? 0)")
                    ForEach(summary.new ?? [], id: \.self) { n in
                        Label("\(n.title): \(n.detail ?? "")", systemImage: "rosette")
                    }
                }
            }
            if let list {
                ForEach(list.achievements) { a in
                    VStack(alignment: .leading, spacing: 3) {
                        HStack {
                            Image(systemName: a.earned ? "rosette" : "circle.dashed").foregroundStyle(a.earned ? .yellow : .secondary)
                            Text(a.title).bold()
                        }
                        Text(a.description ?? "").font(.caption)
                        if a.earned, let detail = a.detail { Text(detail).font(.caption).foregroundStyle(.secondary) }
                        if !a.earned, let p = a.progress, p.count == 2, p[1] > 0 {
                            ProgressView(value: Double(p[0]), total: Double(p[1]))
                        }
                    }
                    .opacity(a.earned ? 1 : 0.6)
                }
            }
        }
        .task {
            if let data = try? await model.connection.efb(.achievements) {
                list = try? JSONDecoder.localtc.decode(AchievementList.self, from: data)
            }
        }
        .navigationTitle("Achievements")
    }
}
