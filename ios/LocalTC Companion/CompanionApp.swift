import SwiftUI

@main
struct CompanionApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .tint(.green)
        }
    }
}

/// Signed in: the flight. Signed out: the email sign-in. LocalTC on the PC works without any of this.
struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        Group {
            if model.signedIn {
                MainView()
            } else {
                SignInView()
            }
        }
        .onChange(of: scenePhase) { _, phase in
            model.scenePhase = phase
            if phase == .active { model.checkSession() }
        }
    }
}
