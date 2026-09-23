import SwiftUI

/// No password: an emailed 6-digit code signs the phone in. The first sign-in creates the account.
struct SignInView: View {
    @Environment(AppModel.self) private var model
    @State private var email = ""
    @State private var code = ""
    @State private var codeSent = false
    @State private var busy = false
    @State private var message: String?
    @State private var failed = false
    @FocusState private var codeFocused: Bool

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    VStack(alignment: .leading, spacing: 8) {
                        Image(systemName: "airplane.circle.fill").font(.system(size: 44)).foregroundStyle(.green)
                        Text("LocalTC Companion").font(.title2.bold())
                        Text("Follow your flight from LocalTC on your PC: the map, the radio, and ATC's calls.")
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 6)
                }
                Section {
                    TextField("Email", text: $email)
                        .keyboardType(.emailAddress)
                        .textContentType(.emailAddress)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .disabled(codeSent)
                        .accessibilityIdentifier("email")
                    if codeSent {
                        TextField("6-digit code", text: $code)
                            .keyboardType(.numberPad)
                            .textContentType(.oneTimeCode)
                            .focused($codeFocused)
                            .font(.title3.monospacedDigit())
                            .accessibilityIdentifier("code")
                    }
                } footer: {
                    Text(codeSent ? "We emailed you a code. It works once, for 15 minutes."
                                  : "No password: we email you a code. The first sign-in creates your account.")
                }
                Section {
                    if codeSent {
                        Button(action: finish) { label("Sign in") }.disabled(busy || code.filter(\.isNumber).count != 6)
                            .accessibilityIdentifier("finish")
                        Button("Use a different email or send a new code") { codeSent = false; code = ""; message = nil }
                            .font(.footnote)
                    } else {
                        Button(action: start) { label("Email me a code") }.disabled(busy || !email.contains("@"))
                            .accessibilityIdentifier("start")
                    }
                }
                if let message {
                    Section { Text(message).foregroundStyle(failed ? .red : .secondary) }
                }
                Section {
                    Text("Creating an account means agreeing to the [terms](https://localtc.tech/terms.html#account) and [privacy policy](https://localtc.tech/privacy.html#account). You need to be 16 or over. LocalTC on the PC works without an account.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Sign in")
        }
    }

    private func label(_ text: String) -> some View {
        HStack { Spacer(); if busy { ProgressView() } else { Text(text).bold() }; Spacer() }
    }

    private func start() {
        busy = true
        Task {
            defer { busy = false }
            do {
                message = try await model.api.start(email: email.trimmingCharacters(in: .whitespaces))
                failed = false
                codeSent = true
                codeFocused = true
            } catch {
                message = error.localizedDescription
                failed = true
            }
        }
    }

    private func finish() {
        busy = true
        Task {
            defer { busy = false }
            do {
                try await model.api.finish(email: email.trimmingCharacters(in: .whitespaces), code: code, device: model.deviceName)
                model.didSignIn()
            } catch {
                message = error.localizedDescription
                failed = true
            }
        }
    }
}
