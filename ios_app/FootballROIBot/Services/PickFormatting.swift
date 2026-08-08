import Foundation

/// Formats raw API market/selection pairs into readable Serbian tip text.
enum PickFormatter {
    static func serbianTip(market: String, selection: String) -> String {
        let m = normalizeMarket(market)
        let sel = selection.trimmingCharacters(in: .whitespacesAndNewlines)
        let sl = sel.lowercased()

        switch m {
        case "match_winner", "1x2":
            return "TIP: \(matchWinnerTip(sl, raw: sel))"

        case "over_under", "ou", "totals":
            return "TIP: \(overUnderTip(sl, raw: sel))"

        case "btts", "both_teams_score", "both_teams_to_score":
            return "TIP: \(bttsTip(sl))"

        case "double_chance":
            return "TIP: \(doubleChanceTip(sl, raw: sel))"

        case "asian_handicap", "handicap":
            return "TIP: \(asianHandicapTip(sl, raw: sel))"

        default:
            return "TIP: \(humanizeMarket(m)) — \(humanizeSelection(sel))"
        }
    }

    // MARK: - Markets

    private static func matchWinnerTip(_ sl: String, raw: String) -> String {
        switch sl {
        case "home", "1", "h": return "Pobeda Domaćina (1)"
        case "away", "2", "a": return "Pobeda Gosta (2)"
        case "draw", "x", "d": return "Nerešeno (X)"
        default: return humanizeSelection(raw)
        }
    }

    private static func overUnderTip(_ sl: String, raw: String) -> String {
        let line = parseLine(from: sl) ?? parseLine(from: raw) ?? "2.5"
        if sl.contains("under") {
            let maxGoals = underMaxGoals(line)
            return "Manje od \(line) gola (\(maxGoals))"
        }
        if sl.contains("over") {
            let minGoals = overMinGoals(line)
            return "Više od \(line) gola (\(minGoals))"
        }
        return humanizeSelection(raw)
    }

    private static func bttsTip(_ sl: String) -> String {
        switch sl {
        case "yes", "gg": return "Oba tima daju gol (GG)"
        case "no", "ng": return "Bar jedan tim ne daje gol (NG)"
        default: return humanizeSelection(sl)
        }
    }

    private static func doubleChanceTip(_ sl: String, raw: String) -> String {
        let compact = sl.replacingOccurrences(of: " ", with: "")
        switch compact {
        case "home/draw", "1x", "1/x": return "Dvostruka šansa 1X (Domaćin ili nerešeno)"
        case "draw/away", "x2", "x/2": return "Dvostruka šansa X2 (Nerešeno ili gost)"
        case "home/away", "12", "1/2": return "Dvostruka šansa 12 (Domaćin ili gost)"
        default: return "Dvostruka šansa — \(humanizeSelection(raw))"
        }
    }

    private static func asianHandicapTip(_ sl: String, raw: String) -> String {
        let handicap = parseSignedLine(from: raw) ?? parseSignedLine(from: sl)
        let handicapText = handicap.map { formatHandicap($0) } ?? raw
        if sl.hasPrefix("home") || sl == "1" || sl == "h" {
            return "Hendikep domaćin \(handicapText)"
        }
        if sl.hasPrefix("away") || sl == "2" || sl == "a" {
            return "Hendikep gost \(handicapText)"
        }
        return "Hendikep — \(humanizeSelection(raw))"
    }

    // MARK: - Helpers

    private static func normalizeMarket(_ market: String) -> String {
        market.lowercased().trimmingCharacters(in: .whitespacesAndNewlines).replacingOccurrences(of: "-", with: "_")
    }

    private static func humanizeMarket(_ market: String) -> String {
        switch market {
        case "match_winner": return "Konačan ishod"
        case "over_under": return "Ukupno golova"
        case "btts": return "Oba daju gol"
        case "double_chance": return "Dvostruka šansa"
        case "asian_handicap": return "Azijski hendikep"
        default: return market.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private static func humanizeSelection(_ selection: String) -> String {
        selection
            .replacingOccurrences(of: "_", with: " ")
            .replacingOccurrences(of: "/", with: " / ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .capitalized
    }

    private static func parseLine(from text: String) -> String? {
        let pattern = #"\d+(?:\.\d+)?"#
        guard let range = text.range(of: pattern, options: .regularExpression) else { return nil }
        return String(text[range])
    }

    private static func parseSignedLine(from text: String) -> Double? {
        let pattern = #"[+-]?\d+(?:\.\d+)?"#
        guard let range = text.range(of: pattern, options: .regularExpression) else { return nil }
        return Double(text[range])
    }

    private static func underMaxGoals(_ line: String) -> String {
        guard let value = Double(line) else { return "ispod granice" }
        let maxGoal = Int(value.rounded(.down))
        return maxGoal == 0 ? "0 golova" : "0-\(maxGoal) gola"
    }

    private static func overMinGoals(_ line: String) -> String {
        guard let value = Double(line) else { return "iznad granice" }
        let minGoal = Int(value) + 1
        return "\(minGoal)+ gola"
    }

    private static func formatHandicap(_ value: Double) -> String {
        if value > 0 { return "+\(formatNumber(value))" }
        return formatNumber(value)
    }

    private static func formatNumber(_ value: Double) -> String {
        value.truncatingRemainder(dividingBy: 1) == 0
            ? String(format: "%.0f", value)
            : String(format: "%.1f", value)
    }

    /// Kickoff u srpskom vremenu (Europe/Belgrade), npr. "🕒 POČETAK: 21:00 (srpsko vreme)".
    static func serbianKickoffText(_ date: Date?) -> String? {
        guard let date else { return nil }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "sr_RS")
        formatter.timeZone = TimeZone(identifier: "Europe/Belgrade")
        formatter.dateFormat = "HH:mm"
        return "🕒 POČETAK: \(formatter.string(from: date)) (srpsko vreme)"
    }
}
