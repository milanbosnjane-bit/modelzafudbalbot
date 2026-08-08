import Foundation

/// Shared config keys — `@AppStorage("baseURL")` reads/writes the same UserDefaults key.
enum AppConfig {
    static let baseURLKey = "baseURL"
    static let pollIntervalKey = "pollIntervalSeconds"
    static let defaultBaseURL = "http://100.122.226.3:8001/api/v1"
    static let defaultPollInterval: Double = 20

    static var normalizedBaseURL: String {
        normalizeURL(
            UserDefaults.standard.string(forKey: baseURLKey) ?? defaultBaseURL
        )
    }

    static var pollIntervalSeconds: Double {
        let stored = UserDefaults.standard.object(forKey: pollIntervalKey) as? Double
        guard let stored, stored >= 10 else { return defaultPollInterval }
        return stored
    }

    static func normalizeURL(_ raw: String) -> String {
        var url = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        while url.hasSuffix("/") { url.removeLast() }
        return url
    }
}
