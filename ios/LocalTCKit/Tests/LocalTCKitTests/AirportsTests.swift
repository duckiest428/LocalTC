import Foundation
import Testing
@testable import LocalTCKit

@Suite("The flight's airports, and which COM a call was on")
@MainActor
struct AirportsTests {
    let json = #"{"type":"airports","data":[{"icao":"KPHX","name":"Phoenix Sky Harbor","role":"arrival","lat":33.43,"lon":-112.01,"elev_ft":1135,"atis":"D","frequencies":[{"label":"TWR","kind":"tower","mhz":118.7,"name":"Phoenix Tower"}],"runways":[{"name":"08/26","length_ft":11489,"heading_mag":76,"ils":["26 (IPHX)"]}]},{"icao":"KSAN"}]}"#

    @Test("Decoded and kept until the flight ends")
    func airports() throws {
        let store = FlightStore()
        store.apply(.status(FlightStatus(active: true)))
        store.apply(try #require(try LiveMessage.decodeEnvelope(json)))
        #expect(store.airports.map(\.icao) == ["KPHX", "KSAN"])
        let phx = store.airports[0]
        #expect(phx.elevFt == 1135 && phx.atis == "D")
        #expect(phx.frequencies.first?.display == "118.700")
        #expect(phx.runways.first?.ils == ["26 (IPHX)"])
        #expect(store.airports[1].frequencies.isEmpty)  // an airport without its lists still decodes
        store.apply(.status(FlightStatus(active: false)))
        #expect(store.airports.isEmpty)
    }

    @Test("A call is on COM2 when it says so, or when its frequency is COM2's and not COM1's")
    func com() throws {
        let store = FlightStore()
        let own = try LiveMessage.decodeEnvelope(#"{"type":"own","data":{"lat":33,"lon":-112,"com1":133.65,"com2":121.5}}"#)
        store.apply(try #require(own))
        #expect(store.com(of: RadioLine(kind: "atc", text: "x", station: "Albuquerque Center", mhz: 133.65)) == 1)
        #expect(store.com(of: RadioLine(kind: "atc", text: "x", station: "Guard", mhz: 121.5)) == 2)
        var line = RadioLine(kind: "pilot", text: "x")
        line.radio = 2
        #expect(store.com(of: line) == 2)
        #expect(store.com(of: RadioLine(kind: "pilot", text: "x")) == 1)
    }
}
