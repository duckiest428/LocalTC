import Testing
@testable import LocalTCKit

@Suite("What each aircraft is drawn as on the map")
struct AircraftKindTests {
    @Test("A Cessna is a light aircraft, not an airliner, and the rest are what they are")
    func kinds() {
        #expect(AircraftKind.of(type: "C172") == .light)
        #expect(AircraftKind.of(type: "A320") == .jet)
        #expect(AircraftKind.of(type: "A319") == .jet)
        #expect(AircraftKind.of(type: "B77W") == .heavy)
        #expect(AircraftKind.of(type: "DH8D") == .turboprop)
        #expect(AircraftKind.of(type: "C208") == .turboprop)
        #expect(AircraftKind.of(type: "CRJ9") == .regional)
        #expect(AircraftKind.of(type: "R44") == .heli)
        #expect(AircraftKind.of(type: nil, category: "A1") == .light)
        #expect(AircraftKind.of(type: nil) == .jet)
    }

    @Test("Every outline fits its box")
    func outlines() {
        for kind in AircraftKind.allCases {
            #expect(kind.outline.count >= 3)
            #expect(kind.outline.allSatisfy { $0.0 >= 0 && $0.0 <= 32 && $0.1 >= 0 && $0.1 <= 32 })
        }
    }
}
