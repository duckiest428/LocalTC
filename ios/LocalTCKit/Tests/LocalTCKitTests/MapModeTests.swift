import Foundation
import Testing
@testable import LocalTCKit

@Suite("IFR or VFR map")
struct MapModeTests {
    @Test("Opens on the flight's rules, keeps the pilot's pick, and follows the rules when they change")
    func followsTheRules() {
        var mode = MapMode()
        mode.follow(rules: nil)
        #expect(mode.kind == .ifr)
        mode.follow(rules: "VFR")
        #expect(mode.kind == .vfr)
        mode.pick(.ifr)
        mode.follow(rules: "VFR")  // the same flight, the same rules: the pick holds
        #expect(mode.kind == .ifr)
        mode.follow(rules: "IFR")
        #expect(mode.kind == .ifr)
        mode.follow(rules: "vfr")  // a new VFR flight
        #expect(mode.kind == .vfr)
    }

    @Test("The status carries the rules")
    func statusDecodesRules() throws {
        let json = #"{"type":"status","data":{"active":true,"callsign":"N172LT","rules":"VFR"}}"#
        guard case .status(let status) = try LiveMessage.decodeEnvelope(json) else {
            Issue.record("not a status"); return
        }
        #expect(status.rules == "VFR")
    }
}
