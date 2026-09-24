import XCTest

/// Signing in and out, and screenshots, for the UI tests.
@MainActor
extension XCTestCase {
    func signOutIfNeeded(_ app: XCUIApplication) {
        guard app.buttons["settings"].firstMatch.waitForExistence(timeout: 3),
              let settings = visible(app.buttons.matching(identifier: "settings")) else { return }
        settings.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        app.buttons["Sign out"].firstMatch.tap()
    }

    /// Each tab has its own toolbar button; the shown tab's is the one on screen. Toolbar buttons don't
    /// always report themselves hittable, so it's tapped where it is.
    func visible(_ query: XCUIElementQuery) -> XCUIElement? {
        let screen = XCUIApplication().frame
        return query.allElementsBoundByIndex.first { !$0.frame.isEmpty && screen.contains(CGPoint(x: $0.frame.midX, y: $0.frame.midY)) }
    }

    /// The newest sign-in code sent to `email`, from the account server's log.
    func waitForCode(in path: String, to email: String) throws -> String {
        let pattern = try NSRegularExpression(pattern: "\\[email to \(NSRegularExpression.escapedPattern(for: email))\\] (\\d{6}) is your")
        for _ in 0..<40 {
            if let text = try? String(contentsOfFile: path, encoding: .utf8) {
                let matches = pattern.matches(in: text, range: NSRange(text.startIndex..., in: text))
                if let last = matches.last, let range = Range(last.range(at: 1), in: text) { return String(text[range]) }
            }
            Thread.sleep(forTimeInterval: 0.5)
        }
        throw XCTSkip("no sign-in code for \(email) in \(path)")
    }

    func attach(_ app: XCUIApplication, _ name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }
}
