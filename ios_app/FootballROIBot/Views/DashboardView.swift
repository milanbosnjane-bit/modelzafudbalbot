import SwiftUI

struct DashboardView: View {
    @StateObject private var appState = AppState.shared

    var body: some View {
        ZStack {
            CyberGridBackground()

            VStack(spacing: 12) {
                HeaderView(isOnline: appState.isOnline, version: appState.botVersion)
                    .padding(.horizontal, 16)

                OddsTrackerPanel(rows: appState.oddsRows, isLoading: appState.isLoadingOdds)
                    .padding(.horizontal, 16)
                    .frame(maxHeight: .infinity, alignment: .top)

                Spacer(minLength: 0)

                ActionGridView()
                    .padding(.horizontal, 16)
                    .padding(.bottom, 8)

                if let err = appState.lastError {
                    Text(err)
                        .font(.caption)
                        .foregroundStyle(CyberColors.red.opacity(0.9))
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 16)
                        .padding(.bottom, 4)
                }
            }
            .padding(.top, 8)

            if let toast = appState.toastMessage {
                VStack {
                    Spacer()
                    ToastView(message: toast, success: appState.toastSuccess)
                        .padding(.bottom, 24)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }
                .animation(.spring(), value: appState.toastMessage)
            }
        }
        .preferredColorScheme(.dark)
        .onAppear { appState.startPolling() }
        .onDisappear { appState.stopPolling() }
        .sheet(isPresented: $appState.showROI) { ROIStatsSheet(data: appState.roiData) }
        .sheet(isPresented: $appState.showLivePicks) { LivePicksSheet(picks: appState.livePicks) }
        .sheet(isPresented: $appState.showRecent) { RecentResultsSheet(picks: appState.recentPicks) }
        .sheet(isPresented: $appState.showSettings) {
            SettingsView()
        }
    }
}

// MARK: - Header

struct HeaderView: View {
    let isOnline: Bool
    let version: String

    var body: some View {
        VStack(spacing: 14) {
            HStack {
                ZStack {
                    Circle()
                        .fill(CyberColors.cyan.opacity(0.15))
                        .frame(width: 44, height: 44)
                    Image(systemName: "soccerball")
                        .font(.system(size: 22))
                        .foregroundStyle(CyberColors.cyan)
                        .shadow(color: CyberColors.cyan.opacity(0.8), radius: 8)
                }

                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text("FOOTBALL ROI BOT")
                            .font(.system(size: 16, weight: .black, design: .rounded))
                            .foregroundStyle(.white)
                        Text("v\(version)")
                            .font(.system(size: 12, weight: .bold, design: .monospaced))
                            .foregroundStyle(CyberColors.purple)
                    }
                }

                Spacer()

                Button {
                    AppState.shared.openSettings()
                } label: {
                    Image(systemName: "line.3.horizontal")
                        .font(.system(size: 20, weight: .semibold))
                        .foregroundStyle(CyberColors.purple)
                        .shadow(color: CyberColors.purple.opacity(0.6), radius: 6)
                }
            }

            PulsingOnlineBadge(isOnline: isOnline)
        }
    }
}

// MARK: - Odds Tracker

struct OddsTrackerPanel: View {
    let rows: [OddsTrackerRow]
    let isLoading: Bool

    private let rowHeight: CGFloat = 72
    private let visibleRows: Int = 3

    private var panelHeight: CGFloat {
        rowHeight * CGFloat(visibleRows)
    }

    var body: some View {
        VStack(spacing: 0) {
            NeonText(text: "RILTAJM PRATIOC KVOTA", color: CyberColors.cyan, size: 14)
                .padding(.top, 14)
                .padding(.bottom, 12)

            Group {
                if isLoading && rows.isEmpty {
                    ProgressView()
                        .tint(CyberColors.cyan)
                        .frame(maxWidth: .infinity, minHeight: panelHeight)
                } else if rows.isEmpty {
                    Text("Nema aktivnih kvota")
                        .font(.subheadline)
                        .foregroundStyle(CyberColors.textSecondary)
                        .frame(maxWidth: .infinity, minHeight: panelHeight)
                } else {
                    ScrollView(.vertical, showsIndicators: true) {
                        LazyVStack(spacing: 0) {
                            ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                                OddsTrackerRowView(row: row, rowHeight: rowHeight)
                                if index < rows.count - 1 {
                                    Divider().background(CyberColors.cyan.opacity(0.12))
                                }
                            }
                        }
                    }
                    .frame(height: panelHeight, alignment: .top)
                }
            }
            .padding(.horizontal, 14)
            .padding(.bottom, 14)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .glassPanel(border: CyberColors.cyan, glow: 18, dualNeon: true)
    }
}

struct OddsTrackerRowView: View {
    let row: OddsTrackerRow
    var rowHeight: CGFloat = 72

    private var changeColor: Color {
        if row.oddsChangePct > 0.004 {
            return CyberColors.green
        }
        if row.oddsChangePct < -0.004 {
            return CyberColors.red
        }
        return CyberColors.textSecondary
    }

    private var changeText: String {
        let pct = row.oddsChangePct * 100
        let sign = pct > 0 ? "+" : ""
        return String(format: "%@%.1f%%", sign, pct)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(row.matchTitle)
                .font(.system(size: 14, weight: .bold))
                .foregroundStyle(CyberColors.textPrimary)
                .lineLimit(1)
                .minimumScaleFactor(0.85)

            Text("🎯 Tip: \(row.pickSelection)")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(CyberColors.cyan)
                .lineLimit(1)

            HStack(spacing: 6) {
                Text(String(format: "%.2f", row.initialOdds))
                    .font(.system(size: 15, weight: .bold, design: .monospaced))
                    .foregroundStyle(CyberColors.textSecondary)
                Text("➔")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(CyberColors.textSecondary)
                Text(String(format: "%.2f", row.currentOdds))
                    .font(.system(size: 15, weight: .bold, design: .monospaced))
                    .foregroundStyle(changeColor)
                Text("(\(changeText))")
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(changeColor)
            }
        }
        .frame(maxWidth: .infinity, minHeight: rowHeight, alignment: .leading)
        .padding(.vertical, 4)
    }
}

// MARK: - Action Grid

struct ActionGridView: View {
    @StateObject private var appState = AppState.shared

    private let columns = [
        GridItem(.flexible(), spacing: 12),
        GridItem(.flexible(), spacing: 12),
    ]

    var body: some View {
        LazyVGrid(columns: columns, spacing: 10) {
            NeonActionButton(title: "ROI\nSTATISTIKA", icon: "chart.bar.fill", accent: CyberColors.cyan) {
                Task { await appState.loadROI() }
            }
            NeonActionButton(title: "LIVE\nPICKS", icon: "scope", accent: CyberColors.purple) {
                Task { await appState.loadLivePicks() }
            }
            NeonActionButton(title: "POSLEDNJI\nREZULTATI", icon: "chart.line.downtrend.xyaxis", accent: CyberColors.green) {
                Task { await appState.loadRecentResults() }
            }
            NeonActionButton(title: "SETTLE\nNOW", icon: "checkmark.circle.fill", accent: CyberColors.green) {
                Task { await appState.settleNow() }
            }
            NeonActionButton(title: "RESTART\nRUN", icon: "arrow.triangle.2.circlepath", accent: CyberColors.cyan) {
                Task { await appState.restartRun() }
            }
            NeonActionButton(title: "STATUS\nBOTA", icon: "gearshape.fill", accent: CyberColors.purple) {
                appState.openSettings()
            }
        }
    }
}

// MARK: - Sheets

struct ROIStatsSheet: View {
    let data: PaperEvaluateResponse?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ZStack {
                CyberGridBackground()
                if let d = data {
                    ScrollView {
                        VStack(spacing: 16) {
                            statCard("ROI", String(format: "%+.1f%%", d.roiPct), CyberColors.green)
                            statCard("Profit", String(format: "%+.2f u", d.profitUnits), CyberColors.cyan)
                            statCard("Winrate", String(format: "%.1f%%", d.winrate), CyberColors.purple)
                            if let clv = d.avgClv {
                                statCard("Avg CLV", String(format: "%+.2% ", clv), CyberColors.cyan)
                            }
                            HStack {
                                miniStat("W", "\(d.wins)", CyberColors.green)
                                miniStat("L", "\(d.losses)", CyberColors.red)
                                miniStat("P", "\(d.pushes)", CyberColors.textSecondary)
                                miniStat("N", "\(d.totalBets)", CyberColors.cyan)
                            }
                            if let verdict = d.verdict {
                                Text(verdict)
                                    .font(.caption)
                                    .foregroundStyle(CyberColors.textSecondary)
                                    .multilineTextAlignment(.center)
                                    .padding()
                                    .glassPanel(border: CyberColors.purple, glow: 6)
                            }
                        }
                        .padding()
                    }
                } else {
                    ProgressView().tint(CyberColors.cyan)
                }
            }
            .navigationTitle("ROI STATISTIKA")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Zatvori") { dismiss() }.foregroundStyle(CyberColors.cyan)
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private func statCard(_ title: String, _ value: String, _ color: Color) -> some View {
        VStack(spacing: 6) {
            Text(title).font(.caption).foregroundStyle(CyberColors.textSecondary)
            Text(value).font(.title2.bold()).foregroundStyle(color)
        }
        .frame(maxWidth: .infinity)
        .padding()
        .glassPanel(border: color, glow: 8)
    }

    private func miniStat(_ label: String, _ value: String, _ color: Color) -> some View {
        VStack {
            Text(label).font(.caption2).foregroundStyle(CyberColors.textSecondary)
            Text(value).font(.headline).foregroundStyle(color)
        }
        .frame(maxWidth: .infinity)
        .padding(8)
        .glassPanel(border: color.opacity(0.5), glow: 4)
    }
}

struct RecentResultsSheet: View {
    let picks: [SettledPickResponse]
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ZStack {
                CyberGridBackground()
                if picks.isEmpty {
                    Text("Još nema rešenih tipova")
                        .foregroundStyle(CyberColors.textSecondary)
                } else {
                    List(picks) { pick in
                        HStack(alignment: .top, spacing: 10) {
                            Text(outcomeIcon(pick.outcome))
                                .font(.title3)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(pick.match)
                                    .font(.headline)
                                    .foregroundStyle(.white)
                                if let score = pick.score {
                                    Text(score).font(.caption).foregroundStyle(CyberColors.cyan)
                                }
                                Text("\(pick.market) · \(pick.selection) @ \(String(format: "%.2f", pick.odds))")
                                    .font(.caption)
                                    .foregroundStyle(CyberColors.textSecondary)
                                if let profit = pick.profitUnits {
                                    Text(String(format: "%+.2f u", profit))
                                        .font(.caption.bold())
                                        .foregroundStyle(profit >= 0 ? CyberColors.green : CyberColors.red)
                                }
                            }
                        }
                        .listRowBackground(CyberColors.panelFill)
                    }
                    .scrollContentBackground(.hidden)
                }
            }
            .navigationTitle("POSLEDNJI REZULTATI")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Zatvori") { dismiss() }.foregroundStyle(CyberColors.cyan)
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private func outcomeIcon(_ outcome: String) -> String {
        switch outcome.lowercased() {
        case "win": return "✅"
        case "lose": return "❌"
        case "push": return "➖"
        default: return "•"
        }
    }
}

#Preview {
    DashboardView()
}
