import Foundation
import Combine

// MARK: - API Client

enum APIError: LocalizedError {
    case invalidURL
    case httpStatus(Int, String)
    case decoding(Error)
    case network(Error)

    var errorDescription: String? {
        switch self {
        case .invalidURL: return "Nevalidan API URL"
        case .httpStatus(let code, let body): return "HTTP \(code): \(body)"
        case .decoding(let err): return "Decode greška: \(err.localizedDescription)"
        case .network(let err): return err.localizedDescription
        }
    }
}

final class APIClient {
    static let shared = APIClient()

    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let str = try container.decode(String.self)
            let formats = [
                "yyyy-MM-dd'T'HH:mm:ss.SSSSSS",
                "yyyy-MM-dd'T'HH:mm:ss",
                "yyyy-MM-dd'T'HH:mm:ssZ",
                "yyyy-MM-dd'T'HH:mm:ss.SSSSSSZ",
            ]
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.timeZone = TimeZone(secondsFromGMT: 0)
            for f in formats {
                formatter.dateFormat = f
                if let date = formatter.date(from: str) { return date }
            }
            if str.hasSuffix("Z") {
                formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSSSS"
                if let date = formatter.date(from: String(str.dropLast())) { return date }
            }
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Bad date: \(str)")
        }
        return d
    }()

    /// Always reads the latest `@AppStorage("baseURL")` value from UserDefaults.
    private var baseURL: String { AppConfig.normalizedBaseURL }

    @MainActor
    func health() async throws -> HealthResponse {
        try await get("/health")
    }

    @MainActor
    func botStatus() async throws -> BotStatusResponse {
        try await get("/status")
    }

    @MainActor
    func config() async throws -> ConfigResponse {
        try await get("/config")
    }

    @MainActor
    func todayPicks() async throws -> [PickResponse] {
        try await get("/picks/today")
    }

    @MainActor
    func recentPicks(limit: Int = 10) async throws -> [SettledPickResponse] {
        try await get("/picks/recent?limit=\(limit)")
    }

    @MainActor
    func oddsTracker(limit: Int = 6) async throws -> [OddsTrackerRow] {
        try await get("/odds/tracker?limit=\(limit)")
    }

    @MainActor
    func paperEvaluate(days: Int = 30) async throws -> PaperEvaluateResponse {
        try await get("/paper/evaluate?days=\(days)")
    }

    @MainActor
    func paperSettle() async throws -> SettleResponse {
        try await post("/paper/settle")
    }

    @MainActor
    func triggerIngest() async throws -> IngestResponse {
        try await post("/ingest")
    }

    @MainActor
    func triggerPredict() async throws -> PredictResponse {
        try await post("/predict")
    }

    // MARK: - HTTP helpers

    @MainActor
    private func get<T: Decodable>(_ path: String) async throws -> T {
        guard let url = URL(string: baseURL + path) else { throw APIError.invalidURL }
        do {
            let (data, response) = try await URLSession.shared.data(from: url)
            try validate(response: response, data: data)
            do {
                return try decoder.decode(T.self, from: data)
            } catch {
                throw APIError.decoding(error)
            }
        } catch let err as APIError {
            throw err
        } catch {
            throw APIError.network(error)
        }
    }

    @MainActor
    private func post<T: Decodable>(_ path: String) async throws -> T {
        guard let url = URL(string: baseURL + path) else { throw APIError.invalidURL }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            try validate(response: response, data: data)
            do {
                return try decoder.decode(T.self, from: data)
            } catch {
                throw APIError.decoding(error)
            }
        } catch let err as APIError {
            throw err
        } catch {
            throw APIError.network(error)
        }
    }

    private func validate(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200...299).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw APIError.httpStatus(http.statusCode, body)
        }
    }
}

// MARK: - App State + Polling

@MainActor
final class AppState: ObservableObject {
    static let shared = AppState()

    @Published var isOnline = false
    @Published var botVersion = "3.1"
    @Published var oddsRows: [OddsTrackerRow] = []
    @Published var isLoadingOdds = false
    @Published var lastError: String?
    @Published var toastMessage: String?
    @Published var toastSuccess = true

    @Published var showROI = false
    @Published var showLivePicks = false
    @Published var showRecent = false
    @Published var showSettings = false

    @Published var roiData: PaperEvaluateResponse?
    @Published var livePicks: [PickResponse] = []
    @Published var recentPicks: [SettledPickResponse] = []
    @Published var botStatus: BotStatusResponse?
    @Published var config: ConfigResponse?

    private var pollTask: Task<Void, Never>?

    func startPolling() {
        pollTask?.cancel()
        pollTask = Task {
            while !Task.isCancelled {
                await refreshDashboard()
                let interval = AppConfig.pollIntervalSeconds
                try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
            }
        }
    }

    func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }

    func refreshDashboard() async {
        isLoadingOdds = true
        defer { isLoadingOdds = false }

        do {
            let health = try await APIClient.shared.health()
            isOnline = health.status == "ok"
            botVersion = health.version

            if let status = try? await APIClient.shared.botStatus() {
                botStatus = status
                isOnline = status.status == "ok"
            }

            oddsRows = try await APIClient.shared.oddsTracker(limit: 6)
            lastError = nil
        } catch {
            isOnline = false
            lastError = error.localizedDescription
        }
    }

    func loadROI() async {
        do {
            roiData = try await APIClient.shared.paperEvaluate(days: 30)
            showROI = true
        } catch {
            showToast(error.localizedDescription, success: false)
        }
    }

    func loadLivePicks() async {
        do {
            livePicks = try await APIClient.shared.todayPicks()
            showLivePicks = true
        } catch {
            showToast(error.localizedDescription, success: false)
        }
    }

    func loadRecentResults() async {
        do {
            recentPicks = try await APIClient.shared.recentPicks(limit: 10)
            showRecent = true
        } catch {
            showToast(error.localizedDescription, success: false)
        }
    }

    func settleNow() async {
        do {
            let result = try await APIClient.shared.paperSettle()
            showToast("Settle OK — rešeno \(result.settled) tipova", success: true)
            await refreshDashboard()
        } catch {
            showToast(error.localizedDescription, success: false)
        }
    }

    func restartRun() async {
        do {
            _ = try await APIClient.shared.triggerIngest()
            let predict = try await APIClient.shared.triggerPredict()
            showToast("Restart OK — \(predict.picksCount) pickova", success: true)
            await refreshDashboard()
        } catch {
            showToast(error.localizedDescription, success: false)
        }
    }

    func openSettings() {
        showSettings = true
        Task {
            do {
                botStatus = try await APIClient.shared.botStatus()
                config = try await APIClient.shared.config()
            } catch {
                // Settings sheet opens even when server is unreachable.
            }
        }
    }

    func showToast(_ message: String, success: Bool) {
        toastMessage = message
        toastSuccess = success
        Task {
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            if toastMessage == message { toastMessage = nil }
        }
    }
}
