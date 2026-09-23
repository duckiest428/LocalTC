import XCTest

/// The whole path, against a real account server and a real LocalTC on this Mac:
/// sign in by emailed code, see the flight arrive, look at the greyed sections, sign out.
///
/// Run by ios/run-e2e.sh, which starts `wrangler dev` (emails are printed to its log, where this test reads
/// the code) and the desktop app replaying a recorded flight. The paths come in TEST_RUNNER_ variables.
@MainActor
final class SignInFlowTests: XCTestCase {
    var env: [String: String] { ProcessInfo.processInfo.environment }

    override func setUp() async throws {
        continueAfterFailure = false
    }

    func testSignInSeeTheFlightAndSignOut() throws {
        let api = try XCTUnwrap(env["LOCALTC_API"], "run through ios/run-e2e.sh")
        let log = try XCTUnwrap(env["LOCALTC_MAIL_LOG"])
        let email = try XCTUnwrap(env["LOCALTC_EMAIL"])
        let expect = env["LOCALTC_EXPECT"] ?? "Same Wi-Fi"

        let app = XCUIApplication()
        app.launchEnvironment["LOCALTC_API"] = api
        app.launch()
        signOutIfNeeded(app)

        let field = app.textFields["email"]
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        field.tap()
        field.typeText(email)
        app.buttons["start"].tap()

        let codeField = app.textFields["code"]
        XCTAssertTrue(codeField.waitForExistence(timeout: 10))
        let code = try waitForCode(in: log, to: email)
        codeField.tap()
        codeField.typeText(code)
        attach(app, "1 code entered")
        app.buttons["finish"].tap()

        // Signed in: the tabs, and the connection the harness set up.
        XCTAssertTrue(app.tabBars.buttons["Map"].waitForExistence(timeout: 15))
        let badge = app.descendants(matching: .any)["connection"].firstMatch
        let connected = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label CONTAINS %@", expect), object: badge)
        XCTAssertEqual(XCTWaiter.wait(for: [connected], timeout: 45), .completed, "connection badge: \(badge.label)")
        attach(app, "2 map")

        // The replayed flight is IFR, so the map opens on IFR; VFR (terrain) is a tap away.
        let mapMode = app.segmentedControls["mapMode"]
        XCTAssertTrue(mapMode.waitForExistence(timeout: 10))
        XCTAssertTrue(mapMode.buttons["IFR"].isSelected)
        mapMode.buttons["VFR"].tap()
        let vfr = XCTNSPredicateExpectation(predicate: NSPredicate(format: "isSelected == true"), object: mapMode.buttons["VFR"])
        XCTAssertEqual(XCTWaiter.wait(for: [vfr], timeout: 5), .completed, "the VFR map")
        attach(app, "2b vfr map")
        mapMode.buttons["IFR"].tap()

        app.tabBars.buttons["Radio"].tap()
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS[c] 'Frontier'")).firstMatch.waitForExistence(timeout: 30))
        attach(app, "3 radio")

        app.tabBars.buttons["Flight"].tap()
        // Whatever the sim calls the aircraft, the row is filled in (not "—").
        let callsign = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'Callsign, ' AND NOT (label ENDSWITH '—')")).firstMatch
        XCTAssertTrue(callsign.waitForExistence(timeout: 20))
        attach(app, "4 flight")

        app.tabBars.buttons["Cockpit"].tap()
        XCTAssertTrue(app.staticTexts["Coming soon"].waitForExistence(timeout: 5))
        attach(app, "5 cockpit")
        app.tabBars.buttons["Flight Bag"].tap()
        XCTAssertTrue(app.staticTexts["Coming soon"].waitForExistence(timeout: 5))

        try XCTUnwrap(visible(app.buttons.matching(identifier: "settings"))).coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", email)).firstMatch.waitForExistence(timeout: 10))
        attach(app, "6 settings")
        app.buttons["Sign out"].firstMatch.tap()
        XCTAssertTrue(app.textFields["email"].waitForExistence(timeout: 10))
    }

    private func signOutIfNeeded(_ app: XCUIApplication) {
        guard app.buttons["settings"].firstMatch.waitForExistence(timeout: 3),
              let settings = visible(app.buttons.matching(identifier: "settings")) else { return }
        settings.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        app.buttons["Sign out"].firstMatch.tap()
    }

    /// Each tab has its own toolbar button; the shown tab's is the one on screen. Toolbar buttons don't
    /// always report themselves hittable, so it's tapped where it is.
    private func visible(_ query: XCUIElementQuery) -> XCUIElement? {
        let screen = XCUIApplication().frame
        return query.allElementsBoundByIndex.first { !$0.frame.isEmpty && screen.contains(CGPoint(x: $0.frame.midX, y: $0.frame.midY)) }
    }

    /// The newest sign-in code sent to `email`, from the account server's log.
    private func waitForCode(in path: String, to email: String) throws -> String {
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

    private func attach(_ app: XCUIApplication, _ name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }
}
