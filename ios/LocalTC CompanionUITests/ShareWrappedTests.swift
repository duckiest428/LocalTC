import XCTest

/// Sharing a flight's card and ATC Wrapped on the phone, against a real account server holding the flight
/// and the website's files beside it (the card is drawn by the site's sharecard.js): swipe a flight, Share,
/// the link and its picture go up; then Wrapped's slides.
///
/// Needs TEST_RUNNER_LOCALTC_SHARE (a flight's id) as well as the variables SignInFlowTests uses; without
/// it, it's skipped.
@MainActor
final class ShareWrappedTests: XCTestCase {
    var env: [String: String] { ProcessInfo.processInfo.environment }

    override func setUp() async throws {
        continueAfterFailure = false
    }

    func testShareAFlightThenWatchWrapped() throws {
        guard let flight = env["LOCALTC_SHARE"] else { throw XCTSkip("set TEST_RUNNER_LOCALTC_SHARE to a flight in the account") }
        let api = try XCTUnwrap(env["LOCALTC_API"])
        let log = try XCTUnwrap(env["LOCALTC_MAIL_LOG"])
        let email = try XCTUnwrap(env["LOCALTC_EMAIL"])

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
        app.buttons["finish"].tap()

        XCTAssertTrue(app.tabBars.buttons["Logbook"].waitForExistence(timeout: 15))
        app.tabBars.buttons["Logbook"].tap()
        let share = app.buttons["share-\(flight)"]
        XCTAssertTrue(share.waitForExistence(timeout: 15), "each flight has its Share flight button")
        share.tap()

        let go = app.buttons["share-go"]
        XCTAssertTrue(go.waitForExistence(timeout: 10))
        Thread.sleep(forTimeInterval: 3)  // the card draws in its web view
        attach(app, "1 share sheet")
        go.tap()
        let shared = app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH 'Shared.'")).firstMatch
        XCTAssertTrue(shared.waitForExistence(timeout: 30), "the link made and its picture uploaded")
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS '/f/'")).firstMatch.exists, "the link is shown")
        attach(app, "2 shared")
        app.buttons["Done"].tap()

        let wrapped = app.buttons["wrapped"].firstMatch
        XCTAssertTrue(wrapped.waitForExistence(timeout: 5))
        wrapped.tap()
        // Last month: the one there is to see.
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "identifier BEGINSWITH 'wrapped-slide-'")).firstMatch.waitForExistence(timeout: 15)
            || app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH 'No flights'")).firstMatch.waitForExistence(timeout: 5))
        attach(app, "3 wrapped last month")
        if app.buttons["Image"].exists {
            app.buttons["Image"].tap()
            XCTAssertTrue(app.buttons["Share image"].waitForExistence(timeout: 20), "the slide drawn as a picture")
        }
        // This month: locked, counting down.
        app.buttons["The period after"].tap()
        XCTAssertTrue(app.staticTexts["wrapped-countdown"].waitForExistence(timeout: 5), "the month still going is locked")
        attach(app, "4 wrapped locked")
    }
}
