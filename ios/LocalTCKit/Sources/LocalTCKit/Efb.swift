import Foundation

// The EFB as LocalTC on the PC has it for the flight (src/localtc/ui/efb_api.py ``state``), the "efb" message:
// the checklist being read, Airport Ops, what ATC is working the flight to (a hold, conditions, reroutes), the
// scratchpad, the achievements, ChartFox, the airports' briefings and chart lists, and the hazards around. Every
// field is optional: an older or newer LocalTC never breaks the app.

public struct EfbState: Codable, Sendable, Equatable {
    public var checklist: EfbChecklist?
    public var ops: AirportOpsState?
    public var atc: AtcOperations?
    public var scratchpad: [ScratchNote]?
    public var achievements: AchievementSummary?
    public var chartfox: ChartFoxInfo?
    public var briefings: [String: Briefing]?
    public var charts: [String: [String: [ChartItem]]]?
    public var book: ChecklistBook?
    public var hazards: [Hazard]?

    public init() {}
}

public struct EfbChecklist: Codable, Sendable, Equatable {
    public var id: String
    public var name: String?
    public var status: String?
    public var index: Int?
    public var source: String?
    public var lines: [Line]?

    public struct Line: Codable, Sendable, Equatable, Hashable {
        public var challenge: String
        public var response: String?
        public var result: String?  // "ok", "wrong", "read", "skipped", "" (not yet)
        public var why: String?
    }

    /// Being read now (not finished or stopped).
    public var running: Bool { !["completed", "stopped"].contains(status ?? "") }
}

public struct ChecklistBook: Codable, Sendable, Equatable {
    public var name: String?
    public var source: String?
    public var checklists: [Item]

    public struct Item: Codable, Sendable, Equatable, Identifiable, Hashable {
        public var id: String
        public var name: String
        public var phase: String?
    }
}

public struct AirportOpsState: Codable, Sendable, Equatable {
    public var icao: String?
    public var role: String?
    public var services: [String: Service]?
    public var gsx: Bool?
    public var deice: Deice?
    public var messages: [Message]?
    public var requests: [GateRequest]?
    public var closures: [String]?

    public struct Service: Codable, Sendable, Equatable {
        public var state: String
        public var source: String?
    }

    public struct Deice: Codable, Sendable, Equatable {
        public var required: Bool?
        public var position: Int?
        public var waitMin: Int?
        public var why: String?
        public var state: String?
    }

    public struct Message: Codable, Sendable, Equatable, Hashable {
        public var t: Double
        public var kind: String?
        public var text: String
    }

    public struct GateRequest: Codable, Sendable, Equatable, Hashable {
        public var gate: String
        public var state: String
        public var why: String?
    }
}

public struct AtcOperations: Codable, Sendable, Equatable {
    public var hold: Hold?
    public var routing: [Amendment]?
    public var conditions: [Condition]?
    public var minimumAltitudeFt: Int?

    public struct Hold: Codable, Sendable, Equatable {
        public var fix: String
        public var lat: Double
        public var lon: Double
        public var inbound: Int?
        public var turn: String?
        public var legMin: Double?
        public var altitudeFt: Int?
        public var reason: String?
        public var efc: String?
        public var position: Int?
        public var delayMin: Int?
        public var entry: String?
        public var state: String?
        public var laps: Int?
        public var racetrack: [[Double]]?

        /// Being flown, or about to be.
        public var active: Bool { ["cleared", "entering", "holding", "leaving"].contains(state ?? "") }
    }

    public struct Amendment: Codable, Sendable, Equatable, Hashable {
        public var kind: String  // shortcut, deviation, reroute
        public var fix: String?
        public var heading: Int?
        public var reason: String?
        public var state: String?
        public var line: [[Double]]?
    }

    public struct Condition: Codable, Sendable, Equatable, Hashable {
        public var kind: String
        public var traffic: String?
        public var runway: String?
        public var state: String?
        public var detail: String?
    }
}

public struct ScratchNote: Codable, Sendable, Equatable, Identifiable {
    public var id: String
    public var text: String
    public var kind: String?
    public var t: Double?
    public var pinned: Bool?
    public var phase: String?
    public var source: String?
    public var picked: [String: String]?
}

public struct AchievementSummary: Codable, Sendable, Equatable {
    public var earned: Int?
    public var total: Int?
    public var new: [New]?

    public struct New: Codable, Sendable, Equatable, Hashable {
        public var id: String
        public var title: String
        public var detail: String?
    }
}

public struct ChartFoxInfo: Codable, Sendable, Equatable {
    public var connected: Bool?
    public var credit: Credit?

    public struct Credit: Codable, Sendable, Equatable {
        public var text: String?
        public var url: String?
        public var privacy: String?
        public var disclaimer: String?
    }
}

public struct Briefing: Codable, Sendable, Equatable {
    public var icao: String?
    public var metar: String?
    public var taf: Taf?
    public var notams: Notams?
    public var atis: Atis?

    public struct Taf: Codable, Sendable, Equatable {
        public var raw: String?
        public var available: Bool?
        public var source: String?
        public var why: String?
    }

    public struct Notams: Codable, Sendable, Equatable {
        public var items: [Notam]?
        public var available: Bool?
        public var why: String?
    }

    public struct Notam: Codable, Sendable, Equatable, Hashable {
        public var id: String?
        public var text: String?
        public var worksTo: [[String]]?
        public var inForce: Bool?

        /// ATC works to it (a runway or taxiway closed, an ILS out ...).
        public var forAtc: Bool { !(worksTo ?? []).isEmpty }
    }

    public struct Atis: Codable, Sendable, Equatable {
        public var letter: String?
        public var runway: String?
        public var approach: String?
        public var source: String?
    }
}

public struct ChartItem: Codable, Sendable, Equatable, Identifiable, Hashable {
    public var id: String
    public var name: String
    public var code: String?
    public var type: String?
    public var hasGeoref: Bool?
}

public struct Hazard: Codable, Sendable, Equatable, Hashable {
    public var id: String?
    public var kind: String  // sigmet, airmet, tfr, restricted, prohibited, danger
    public var name: String?
    public var hazard: String?
    public var floorFt: Int?
    public var ceilingFt: Int?
    public var polygon: [[Double]]
}

/// A gate map (asked for on the PC's Wi-Fi: ``Connection/efb``): the airport's stands and the one ATC gave.
public struct GateMap: Codable, Sendable, Equatable {
    public var icao: String
    public var assigned: Int?
    public var assignedName: String?
    public var operator_: String?
    public var stands: [Stand]
    public var requests: [AirportOpsState.GateRequest]?

    enum CodingKeys: String, CodingKey { case icao, assigned, assignedName, operator_ = "operator", stands, requests }

    public struct Stand: Codable, Sendable, Equatable, Identifiable, Hashable {
        public var index: Int
        public var name: String
        public var label: String
        public var lat: Double
        public var lon: Double
        public var kind: String?
        public var international: Bool?
        public var occupied: Bool?
        public var taken: Bool?
        public var fits: Bool?
        public var airline: String?
        public var id: Int { index }
    }
}

/// One chart to view (on the PC's Wi-Fi): its link (it expires), and where the aircraft is on it, if georeferenced.
public struct ChartView: Codable, Sendable, Equatable {
    public var id: String
    public var name: String
    public var code: String?
    public var viewUrl: String?
    public var hasGeoref: Bool?
    public var own: [Double]?
    public var credit: ChartFoxInfo.Credit?
}

/// What the phone's EFB asks of LocalTC. On the PC's Wi-Fi any of them, with an answer; through the account only the
/// ones that change something (the answer then comes as the next "efb" message).
public enum EfbCommand: Sendable {
    case checklist(action: String, id: String? = nil)  // run (with the id), pause, resume, skip, stop, check
    case addNote(text: String, kind: String? = nil, pinned: Bool = false)
    case editNote(id: String, pinned: Bool)
    case deleteNote(id: String)
    case gate(String)
    case opsNote(text: String, kind: String = "maintenance")
    case gates(icao: String? = nil)
    case chart(id: String, height: Int)
    case achievements

    /// Whether it can go through the account's relay (it changes something; no answer needed).
    public var relayable: Bool {
        switch self {
        case .gates, .chart, .achievements: false
        default: true
        }
    }

    public var json: [String: Any] {
        switch self {
        case .checklist(let action, let id):
            return ["action": "checklist", "do": action, "id": id ?? ""]
        case .addNote(let text, let kind, let pinned):
            return ["action": "scratchpad", "op": "add", "text": text, "kind": kind ?? "", "pinned": pinned]
        case .editNote(let id, let pinned):
            return ["action": "scratchpad", "op": "edit", "id": id, "pinned": pinned]
        case .deleteNote(let id):
            return ["action": "scratchpad", "op": "delete", "id": id]
        case .gate(let label):
            return ["action": "gate", "gate": label]
        case .opsNote(let text, let kind):
            return ["action": "ops_note", "text": text, "kind": kind]
        case .gates(let icao):
            return ["action": "gates", "icao": icao ?? ""]
        case .chart(let id, let height):
            return ["action": "chart", "id": id, "height": height]
        case .achievements:
            return ["action": "achievements"]
        }
    }
}

/// Every achievement with whether it's earned, what met it, and the progress towards a milestone (on the PC's Wi-Fi).
public struct AchievementList: Codable, Sendable, Equatable {
    public var achievements: [Item]
    public var earned: Int?
    public var total: Int?

    public struct Item: Codable, Sendable, Equatable, Identifiable, Hashable {
        public var id: String
        public var title: String
        public var description: String?
        public var kind: String?
        public var earned: Bool
        public var detail: String?
        public var progress: [Int]?
    }
}

extension JSONDecoder {
    /// The desktop's JSON (snake_case keys), as the live messages are read.
    public static var localtc: JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }
}
