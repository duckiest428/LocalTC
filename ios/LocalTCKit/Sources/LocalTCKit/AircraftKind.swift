import Foundation

/// What an aircraft is drawn as on the map: an airliner, a widebody, a regional or business jet, a turboprop, a light
/// aircraft or a helicopter, from its ICAO type ("C172", "A359"), else its ADS-B category ("A1": light). The desktop's
/// Live Map draws the same kinds (atcmap.js `kindOf`): a Cessna was drawn as an airliner.
public enum AircraftKind: String, Sendable, CaseIterable {
    case jet, heavy, regional, turboprop, light, heli

    public static func of(type: String?, category: String? = nil) -> AircraftKind {
        let t = (type ?? "").uppercased()
        guard !t.isEmpty else {
            switch (category ?? "").uppercased() {
            case "A1": return .light
            case "A2": return .turboprop
            case "A5": return .heavy
            case "A7": return .heli
            default: return .jet
            }
        }
        if matches(t, heliTypes) { return .heli }
        if matches(t, lightTypes) { return .light }
        if matches(t, turbopropTypes) { return .turboprop }
        if matches(t, smallJetTypes) { return .regional }
        if matches(t, heavyTypes) && !matches(t, "^A31[89]") { return .heavy }
        return .jet
    }

    private static let heavyTypes = "^(A30|A31[0-7]|A33|A34|A35|A38|B74|B76|B77|B78|MD1|DC10|IL76|A124|C17|C5|KC10|K35R|A400|B52)"
    private static let heliTypes = "^(H[0-9]|EC[0-9]|AS3|AS5|AS6|R22|R44|R66|B06|B40|B41|B42|B43|S76|S92|A109|A119|A1[3-8]9|AW[0-9]|BK17|MI8|UH|CH4|EH10|NH90)"
    private static let lightTypes = "^(C1[0-9][0-9]|C2[0-1][0-79]|C3[0-9][0-9]|C4[0-9][0-9]|PA[0-9]|P28|P32|P46|SR2|DA[0-9]|BE3|BE5|M20|TB[0-9]|RV[0-9]|AA5|DV2|GLID|J3|C42|EV97|PC6|TBM|KODI|M600|GA8|PIVI|ULAC)"
    private static let turbopropTypes = "^(AT4|AT7|ATR|DH8|DHC|SF34|SB20|E120|B190|BE20|BE30|BE9|BE99|JS3|JS4|SW4|D328|F50|PC12|C208|L410|C130|AN2|AN3|Y12|MA60|BN2|C212|CN35|P180)"
    private static let smallJetTypes = "^(CRJ|E1[34]5|E35|E17|E19|E75|E29|F70|F100|B712|C25|C5[0-9]|C56|C68|C700|C750|CL30|CL35|CL60|GLF|GL5|GL7|GLEX|FA[0-9]|F2TH|F900|LJ|E55P|E50P|PC24|HDJT|BE40|H25|SF50)"

    private static func matches(_ text: String, _ pattern: String) -> Bool {
        text.range(of: pattern, options: .regularExpression) != nil
    }

    /// Its outline, nose up, in a 32 by 32 box: the desktop's silhouettes as straight lines (a heli's is its own).
    public var outline: [(Double, Double)] {
        switch self {
        case .jet:
            return [(16, 2), (18, 5), (18, 12), (29, 18), (29, 21), (18, 18), (18, 24), (21, 26), (21, 28.5), (16, 27),
                    (11, 28.5), (11, 26), (14, 24), (14, 18), (3, 21), (3, 18), (14, 12), (14, 5)]
        case .heavy:
            return [(16, 0.8), (18.3, 4.3), (18.3, 11.2), (31.2, 18.4), (31.2, 21.7), (18.3, 18.1), (18.3, 24.7),
                    (21.9, 27.1), (21.9, 29.6), (16, 28), (10.1, 29.6), (10.1, 27.1), (13.7, 24.7), (13.7, 18.1),
                    (0.8, 21.7), (0.8, 18.4), (13.7, 11.2), (13.7, 4.3)]
        case .regional:
            return [(16, 4), (17.5, 6.6), (17.5, 14.2), (26, 18.4), (26, 20.7), (17.5, 18.4), (17.5, 22.8), (20.6, 23.8),
                    (21.8, 26.1), (17.4, 25), (16, 27), (14.6, 25), (10.2, 26.1), (11.4, 23.8), (14.5, 22.8),
                    (14.5, 18.4), (6, 20.7), (6, 18.4), (14.5, 14.2), (14.5, 6.6)]
        case .turboprop:
            return [(16, 2), (17, 3.5), (17, 11), (28.5, 11), (28.5, 14), (17, 14), (17, 23.5), (21.5, 23.5), (21.5, 26),
                    (10.5, 26), (10.5, 23.5), (15, 23.5), (15, 14), (3.5, 14), (3.5, 11), (15, 11), (15, 3.5)]
        case .light:
            return [(16, 3.5), (16.9, 5.2), (16.9, 11.8), (29.3, 11.8), (29.3, 14.6), (16.9, 14.6), (16.9, 22.8),
                    (20.7, 22.8), (20.7, 25.2), (11.3, 25.2), (11.3, 22.8), (15.1, 22.8), (15.1, 14.6), (2.7, 14.6),
                    (2.7, 11.8), (15.1, 11.8), (15.1, 5.2)]
        case .heli:
            // The body, the tail boom and its fin (the rotor is drawn across it, ``rotor``)
            return [(16, 9.5), (19, 10.7), (20.3, 14.9), (19, 19.1), (16.7, 20.2), (16.7, 27.4), (19.2, 27.4), (19.2, 28.8),
                    (12.8, 28.8), (12.8, 27.4), (15.3, 27.4), (15.3, 20.2), (13, 19.1), (11.7, 14.9), (13, 10.7)]
        }
    }

    /// A helicopter's rotor blades, two lines across the box: (from, to) each.
    public var rotor: [((Double, Double), (Double, Double))] {
        self == .heli ? [((2.5, 14.9), (29.5, 14.9)), ((16, 1.5), (16, 28.5))] : []
    }

    /// How big it's drawn, for a traffic marker of ``size`` points (a widebody a little bigger, a Cessna smaller).
    public func scaled(_ size: Double) -> Double {
        switch self {
        case .heavy: return size * 1.2
        case .light, .heli: return size * 0.8
        case .regional, .turboprop: return size * 0.9
        case .jet: return size
        }
    }
}
