import SwiftUI
import UIKit
import UserNotifications

/// The app's state: the account, the flight, and the connection to LocalTC on the PC.
@MainActor @Observable
final class AppModel {
    let api: APIClient
    let store = FlightStore()
    let connection: ConnectionManager
    private(set) var signedIn: Bool
    private(set) var email: String?
    var scenePhase: ScenePhase = .active

    /// Push notifications need the paid Apple Developer Program; until then alerts are in-app and local.
    static let pushEnabled = false

    /// The account server. `LOCALTC_API` points a development build at `wrangler dev`.
    static var apiURL: URL {
        #if DEBUG
        if let override = ProcessInfo.processInfo.environment["LOCALTC_API"] ?? UserDefaults.standard.string(forKey: "apiURL"),
           let url = URL(string: override) { return url }
        #endif
        return APIClient.production
    }

    /// The website, whose sharecard.js draws the cards: the one beside the account server
    /// (`LOCALTC_SITE` for a development build, else wrangler dev's site on port 8000).
    static var siteURL: URL {
        #if DEBUG
        if let override = ProcessInfo.processInfo.environment["LOCALTC_SITE"], let url = URL(string: override) { return url }
        if apiURL.host == "localhost" || apiURL.host == "127.0.0.1" {
            return URL(string: "http://\(apiURL.host!):8000/")!
        }
        #endif
        return URL(string: "https://localtc.tech/")!
    }

    init() {
        let api = APIClient(baseURL: Self.apiURL, tokens: KeychainTokenStore())
        self.api = api
        connection = ConnectionManager(api: api, store: store, feeds: NetworkFeeds(api: api))
        if let saved = UserDefaults.standard.string(forKey: "connectionMode"), let mode = ConnectionMode(rawValue: saved) {
            connection.mode = mode
        }
        signedIn = api.signedIn
        email = api.email
        if signedIn { connection.start() }
    }

    var deviceName: String { UIDevice.current.name }

    func didSignIn() {
        signedIn = true
        email = api.email
        connection.start()
        Task { _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) }
    }

    func signOut() async {
        connection.stop()
        await api.logout()
        store.reset()
        signedIn = false
        email = nil
    }

    /// Signed out elsewhere (the website's device list, an expired sign-in): follow suit here.
    func checkSession() {
        if signedIn && !api.signedIn {
            connection.stop()
            store.reset()
            signedIn = false
        }
    }

    func setMode(_ mode: ConnectionMode) {
        connection.mode = mode
        UserDefaults.standard.set(mode.rawValue, forKey: "connectionMode")
        connection.restart()
    }

    /// While the app is in the background, an alert also goes to Notification Center.
    func notifyIfBackground(_ alert: FlightAlert) {
        guard scenePhase != .active else { return }
        let content = UNMutableNotificationContent()
        content.title = alert.title
        content.body = alert.body
        content.sound = alert.kind == .emergency ? .defaultCritical : .default
        content.threadIdentifier = "flight"
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: alert.id.uuidString, content: content, trigger: nil))
    }
}
