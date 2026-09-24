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
        XCTAssertTrue(app.tabBars.buttons["My Flight"].waitForExistence(timeout: 15))
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

        // The flight card under the map opens the details. Whatever the sim calls the aircraft, the row is filled in.
        let card = app.buttons["flightCard"]
        XCTAssertTrue(card.waitForExistence(timeout: 20))
        card.tap()
        let callsign = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'Callsign, ' AND NOT (label ENDSWITH '—')")).firstMatch
        XCTAssertTrue(callsign.waitForExistence(timeout: 20))
        attach(app, "3 flight details")

        app.tabBars.buttons["Comms"].tap()
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS[c] 'Frontier'")).firstMatch.waitForExistence(timeout: 30))
        XCTAssertTrue(app.segmentedControls["comFilter"].exists)
        let message = app.textFields["message"]
        XCTAssertTrue(message.exists)
        XCTAssertEqual(message.isEnabled, expect == "Same Wi-Fi", "typing to ATC is for the same Wi-Fi")
        attach(app, "4 comms")

        app.tabBars.buttons["Frequencies"].tap()
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH 'KSAN · '")).firstMatch.waitForExistence(timeout: 20))
        XCTAssertTrue(app.staticTexts["Los Angeles Center"].exists || app.staticTexts["Tuned"].exists)
        attach(app, "5 frequencies")

        app.tabBars.buttons["Airports"].tap()
        XCTAssertTrue(app.staticTexts["KSAN"].waitForExistence(timeout: 10))
        attach(app, "6 airports")

        // A fresh account has no flights yet: the logbook says so.
        app.tabBars.buttons["Logbook"].tap()
        XCTAssertTrue(app.staticTexts["No flights yet"].waitForExistence(timeout: 15))
        attach(app, "6b logbook")

        XCTAssertFalse(app.tabBars.buttons["More"].exists)  // five tabs, all on the bar

        try XCTUnwrap(visible(app.buttons.matching(identifier: "settings"))).coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", email)).firstMatch.waitForExistence(timeout: 10))
        attach(app, "7 settings")
        // The EFB preview, greyed, from Settings.
        app.buttons["efb"].tap()
        XCTAssertTrue(app.staticTexts["Coming soon"].waitForExistence(timeout: 5))
        app.navigationBars.buttons.firstMatch.tap()
        app.buttons["Sign out"].firstMatch.tap()
        XCTAssertTrue(app.textFields["email"].waitForExistence(timeout: 10))
    }
}
