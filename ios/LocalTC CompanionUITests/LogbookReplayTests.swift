import XCTest

/// A flight's replay on the phone, against a real account server holding one (uploaded from the desktop app):
/// the Logbook, the flight, then playing, scrubbing to a call, and the transcript in step.
///
/// Needs TEST_RUNNER_LOCALTC_REPLAY (the flight's id) as well as the variables SignInFlowTests uses; without
/// it, it's skipped.
@MainActor
final class LogbookReplayTests: XCTestCase {
    var env: [String: String] { ProcessInfo.processInfo.environment }

    override func setUp() async throws {
        continueAfterFailure = false
    }

    func testReplayAFlightFromTheLogbook() throws {
        guard let flight = env["LOCALTC_REPLAY"] else { throw XCTSkip("set TEST_RUNNER_LOCALTC_REPLAY to a flight with a replay") }
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
        let row = app.buttons["replay-\(flight)"]
        XCTAssertTrue(row.waitForExistence(timeout: 15))
        attach(app, "1 logbook")
        row.tap()

        let play = app.buttons["replayPlay"]
        XCTAssertTrue(play.waitForExistence(timeout: 15))
        XCTAssertTrue(app.staticTexts["+0:00:00"].exists || app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH '+0:00:00'")).firstMatch.exists)
        attach(app, "2 replay")

        app.buttons["Next call"].tap()
        app.buttons["Next call"].tap()
        let time = app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH '+0:0' AND NOT (label BEGINSWITH '+0:00:00')")).firstMatch
        XCTAssertTrue(time.waitForExistence(timeout: 5), "the clock moved to a call")
        attach(app, "3 second call")

        play.tap()
        XCTAssertTrue(app.buttons["Pause"].waitForExistence(timeout: 5))
        Thread.sleep(forTimeInterval: 3)
        attach(app, "4 playing")
        app.buttons["Pause"].tap()

        let slider = app.sliders.firstMatch
        slider.adjust(toNormalizedSliderPosition: 0.96)
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS[c] 'runway 16L'")).firstMatch.waitForExistence(timeout: 5),
                      "near the end, the transcript is at the landing")
        attach(app, "5 landing")
    }
}
