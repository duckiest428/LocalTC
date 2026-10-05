import SwiftUI

@main
struct CompanionApp: App {
    @State private var model = AppModel()
    @AppStorage("theme") private var themeName = AppTheme.system.rawValue

    var body: some Scene {
        WindowGroup {
            let theme = AppTheme(rawValue: themeName) ?? .system
            RootView()
                .environment(model)
                .environment(\.appTheme, theme)
                .tint(theme.accent)
                .preferredColorScheme(theme.scheme)
        }
    }
}

private struct AppThemeKey: EnvironmentKey {
    static let defaultValue = AppTheme.system
}

extension EnvironmentValues {
    var appTheme: AppTheme {
        get { self[AppThemeKey.self] }
        set { self[AppThemeKey.self] = newValue }
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
