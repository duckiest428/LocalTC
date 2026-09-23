import Foundation
import Security

/// Where the sign-in lives: the Keychain on the phone, memory in tests.
public protocol TokenStore: AnyObject, Sendable {
    var token: String? { get }
    var email: String? { get }
    func save(token: String, email: String)
    func clear()
}

public final class MemoryTokenStore: TokenStore, @unchecked Sendable {
    private let lock = NSLock()
    private var values: (String?, String?) = (nil, nil)

    public init() {}

    public var token: String? { lock.withLock { values.0 } }
    public var email: String? { lock.withLock { values.1 } }
    public func save(token: String, email: String) { lock.withLock { values = (token, email) } }
    public func clear() { lock.withLock { values = (nil, nil) } }
}

/// The token in the Keychain, readable only on this device, after the first unlock.
public final class KeychainTokenStore: TokenStore, @unchecked Sendable {
    private let service: String

    public init(service: String = "tech.localtc.companion") { self.service = service }

    public var token: String? { read("token") }
    public var email: String? { read("email") }

    public func save(token: String, email: String) {
        write("token", token)
        write("email", email)
    }

    public func clear() {
        for account in ["token", "email"] {
            SecItemDelete(query(account) as CFDictionary)
        }
    }

    private func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
    }

    private func read(_ account: String) -> String? {
        var q = query(account)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private func write(_ account: String, _ value: String) {
        SecItemDelete(query(account) as CFDictionary)
        var q = query(account)
        q[kSecValueData as String] = Data(value.utf8)
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(q as CFDictionary, nil)
    }
}
