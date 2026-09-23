// swift-tools-version: 6.0
// The companion app's logic, with no UIKit: the account API, the live protocol, the connection (local
// network first, the relay otherwise) and the flight state. The app target compiles these same files
// (a synchronized folder in the Xcode project); this package exists so `swift test` runs them on a Mac.
import PackageDescription

let package = Package(
    name: "LocalTCKit",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "LocalTCKit", targets: ["LocalTCKit"])],
    targets: [
        .target(name: "LocalTCKit"),
        .testTarget(name: "LocalTCKitTests", dependencies: ["LocalTCKit"], resources: [.copy("Fixtures")]),
    ]
)
