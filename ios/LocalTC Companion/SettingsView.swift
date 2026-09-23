import SwiftUI

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var me: Me?
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Account") {
                    LabeledContent("Signed in as", value: model.email ?? "—")
                    Button("Sign out", role: .destructive) {
                        Task {
                            await model.signOut()
                            dismiss()
                        }
                    }
                }
                Section {
                    Picker("Connect", selection: Binding(get: { model.connection.mode }, set: { model.setMode($0) })) {
                        ForEach(ConnectionMode.allCases, id: \.self) { Text($0.label).tag($0) }
                    }
                    LabeledContent("Now", value: model.connection.state.label)
                    if let problem = model.connection.lastError {
                        Text(problem).font(.footnote).foregroundStyle(.secondary)
                    }
                } header: {
                    Text("Connection")
                } footer: {
                    Text("On the same Wi-Fi the phone talks to your PC directly. Elsewhere the map and radio come through the LocalTC server, held in memory there and never stored (the PC's Quick Settings → Account can turn that off).")
                }
                Section("Signed-in devices") {
                    if let me {
                        ForEach(me.sessions) { device in
                            HStack {
                                VStack(alignment: .leading) {
                                    Text(kind(device.kind)).font(.subheadline.bold())
                                    Text(device.device + (device.current == true ? " · this phone" : "")).font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                if device.current != true {
                                    Button("Sign out") { Task { await revoke(device) } }.font(.caption)
                                }
                            }
                        }
                    } else if let error {
                        Text(error).foregroundStyle(.secondary)
                    } else {
                        ProgressView()
                    }
                }
                Section("About") {
                    LabeledContent("Version", value: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")
                    Link("Your logbook on localtc.tech", destination: URL(string: "https://localtc.tech/dashboard.html")!)
                    Link("Privacy policy", destination: URL(string: "https://localtc.tech/privacy.html#account")!)
                    Link("Terms", destination: URL(string: "https://localtc.tech/terms.html#account")!)
                }
            }
            .navigationTitle("Settings")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .task { await load() }
        }
    }

    private func kind(_ k: String) -> String {
        ["desktop": "LocalTC on a PC", "ios": "iPhone or iPad", "web": "Website"][k] ?? k
    }

    private func load() async {
        do {
            me = try await model.api.me()
        } catch {
            self.error = error.localizedDescription
            model.checkSession()
        }
    }

    private func revoke(_ device: Device) async {
        try? await model.api.revoke(device: device.id)
        await load()
    }
}
