/// Which map the phone shows: IFR (a plain map for enroute) or VFR (terrain, like a chart).
///
/// It follows the flight's rules, and changes with them; in between, the pilot's own pick holds. The same
/// rule as the desktop's Live Map.
public struct MapMode: Sendable, Equatable {
    public enum Kind: String, Sendable, CaseIterable {
        case ifr = "IFR"
        case vfr = "VFR"
    }

    public private(set) var kind: Kind = .ifr
    private var rulesSeen: Kind?

    public init() {}

    /// The flight's rules, as the status carries them ("IFR", "VFR", or nothing before a flight).
    public mutating func follow(rules: String?) {
        let wanted: Kind = rules?.uppercased() == "VFR" ? .vfr : .ifr
        if wanted != rulesSeen {
            rulesSeen = wanted
            kind = wanted
        }
    }

    public mutating func pick(_ kind: Kind) {
        self.kind = kind
    }
}
