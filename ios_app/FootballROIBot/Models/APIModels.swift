import Foundation

// MARK: - API Response Models

struct HealthResponse: Codable {
    let status: String
    let version: String
}

struct BotStatusResponse: Codable {
    let status: String
    let version: String
    let dcEngine: String
    let apiConfigured: Bool
    let maxDailyPicks: Int
    let leagueCount: Int
    let useCalibratedConfidence: Bool
    let maxOpenFixtures: Int

    enum CodingKeys: String, CodingKey {
        case status, version
        case dcEngine = "dc_engine"
        case apiConfigured = "api_configured"
        case maxDailyPicks = "max_daily_picks"
        case leagueCount = "league_count"
        case useCalibratedConfidence = "use_calibrated_confidence"
        case maxOpenFixtures = "max_open_fixtures"
    }
}

struct PickResponse: Codable, Identifiable {
    let id: Int
    let rank: Int
    let match: String
    let market: String
    let selection: String
    let odds: Double
    let probability: Double
    let expectedValue: Double
    let confidence: Double
    let roiScore: Double
    let stakeUnits: Double
    let reasoning: [String]
    let kickoff: Date?
    let status: String?

    var isLive: Bool { status?.uppercased() == "LIVE" }

    enum CodingKeys: String, CodingKey {
        case id, rank, match, market, selection, odds, probability, reasoning, confidence, kickoff, status
        case expectedValue = "expected_value"
        case roiScore = "roi_score"
        case stakeUnits = "stake_units"
    }
}

struct SettledPickResponse: Codable, Identifiable {
    var id: String { "\(rank)-\(pickDate.timeIntervalSince1970)" }
    let rank: Int
    let match: String
    let homeAbbr: String
    let awayAbbr: String
    let market: String
    let selection: String
    let odds: Double
    let outcome: String
    let profitUnits: Double?
    let clv: Double?
    let pickDate: Date
    let score: String?

    enum CodingKeys: String, CodingKey {
        case rank, match, market, selection, odds, outcome, clv, score
        case homeAbbr = "home_abbr"
        case awayAbbr = "away_abbr"
        case profitUnits = "profit_units"
        case pickDate = "pick_date"
    }
}

struct OddsSelectionResponse: Codable {
    let odds: Double
    let direction: String
}

struct OddsTrackerRow: Codable, Identifiable {
    var id: Int { fixtureId }
    let fixtureId: Int
    let match: String
    let homeAbbr: String
    let awayAbbr: String
    let homeLogo: String?
    let awayLogo: String?
    let home: OddsSelectionResponse
    let draw: OddsSelectionResponse
    let away: OddsSelectionResponse
    let kickoff: Date?

    enum CodingKeys: String, CodingKey {
        case match, home, draw, away, kickoff
        case fixtureId = "fixture_id"
        case homeAbbr = "home_abbr"
        case awayAbbr = "away_abbr"
        case homeLogo = "home_logo"
        case awayLogo = "away_logo"
    }
}

struct PaperEvaluateResponse: Codable {
    let periodDays: Int?
    let allTime: Bool?
    let totalBets: Int
    let wins: Int
    let losses: Int
    let pushes: Int
    let winrate: Double
    let profitUnits: Double
    let stakedUnits: Double
    let roiPct: Double
    let avgClv: Double?
    let avgEdgeCapture: Double?
    let clvCoverage: Double?
    let goLiveReady: Bool?
    let verdict: String?

    enum CodingKeys: String, CodingKey {
        case wins, losses, pushes, winrate, verdict
        case periodDays = "period_days"
        case allTime = "all_time"
        case totalBets = "total_bets"
        case profitUnits = "profit_units"
        case stakedUnits = "staked_units"
        case roiPct = "roi_pct"
        case avgClv = "avg_clv"
        case avgEdgeCapture = "avg_edge_capture"
        case clvCoverage = "clv_coverage"
        case goLiveReady = "go_live_ready"
    }
}

struct SettleResponse: Codable {
    let settled: Int
}

struct ConfigResponse: Codable {
    let minEvThreshold: Double
    let minConfidenceThreshold: Double
    let maxDailyPicks: Int
    let kellyFraction: Double
    let supportedMarkets: [String]
    let leagueIds: [Int]

    enum CodingKeys: String, CodingKey {
        case kellyFraction = "kelly_fraction"
        case minEvThreshold = "min_ev_threshold"
        case minConfidenceThreshold = "min_confidence_threshold"
        case maxDailyPicks = "max_daily_picks"
        case supportedMarkets = "supported_markets"
        case leagueIds = "league_ids"
    }
}

struct PredictResponse: Codable {
    let picksCount: Int
    let picks: [PickResponse]

    enum CodingKeys: String, CodingKey {
        case picks
        case picksCount = "picks_count"
    }
}

struct IngestResponse: Codable {
    let status: String
}

enum OddsDirection {
    case up, down, flat

    init(_ raw: String) {
        switch raw.lowercased() {
        case "up": self = .up
        case "down": self = .down
        default: self = .flat
        }
    }
}
