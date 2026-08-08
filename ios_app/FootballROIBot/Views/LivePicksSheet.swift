import SwiftUI

struct LivePicksSheet: View {
    let picks: [PickResponse]
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ZStack {
                CyberGridBackground()

                if picks.isEmpty {
                    VStack(spacing: 12) {
                        Image(systemName: "scope")
                            .font(.system(size: 36))
                            .foregroundStyle(CyberColors.skyBlue.opacity(0.7))
                            .shadow(color: CyberColors.skyBlue.opacity(0.5), radius: 10)
                        Text("Nema aktivnih pickova danas")
                            .font(.headline)
                            .foregroundStyle(CyberColors.textSecondary)
                    }
                } else {
                    ScrollView(showsIndicators: false) {
                        VStack(spacing: 10) {
                            ForEach(picks) { pick in
                                LivePickCard(pick: pick)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .top)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 12)
                    }
                }
            }
            .navigationTitle("LIVE PICKS")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Zatvori") { dismiss() }
                        .foregroundStyle(CyberColors.cyan)
                }
            }
        }
        .preferredColorScheme(.dark)
    }
}

struct LivePickCard: View {
    let pick: PickResponse

    private var tipText: String {
        PickFormatter.serbianTip(market: pick.market, selection: pick.selection)
    }

    private var kickoffText: String? {
        PickFormatter.serbianKickoffText(pick.kickoff)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 10) {
                Text("#\(pick.rank)")
                    .font(.system(size: 14, weight: .black, design: .monospaced))
                    .foregroundStyle(CyberColors.skyBlue)
                    .shadow(color: CyberColors.skyBlue.opacity(0.7), radius: 6)

                VStack(alignment: .leading, spacing: 4) {
                    Text(pick.match)
                        .font(.system(size: 17, weight: .bold))
                        .foregroundStyle(.white)
                        .shadow(color: CyberColors.skyBlue.opacity(0.2), radius: 4)
                        .fixedSize(horizontal: false, vertical: true)

                    if let kickoffText {
                        Text(kickoffText)
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(CyberColors.skyBlue.opacity(0.9))
                    }
                }
            }

            Text(tipText)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(CyberColors.cyan)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 0) {
                statBlock(label: "KVOTA", value: String(format: "@%.2f", pick.odds), color: .white)
                Spacer(minLength: 8)
                statBlock(
                    label: "EV",
                    value: String(format: "%+.0f%%", pick.expectedValue * 100),
                    color: CyberColors.green
                )
                Spacer(minLength: 8)
                statBlock(
                    label: "CONF",
                    value: String(format: "%.0f%%", pick.confidence * 100),
                    color: CyberColors.purple
                )
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .fixedSize(horizontal: false, vertical: true)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(CyberColors.panelFill)
                .background(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .fill(.ultraThinMaterial.opacity(0.35))
                )
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(CyberColors.skyBlue.opacity(0.85), lineWidth: 1)
        )
        .shadow(color: CyberColors.skyBlue.opacity(0.4), radius: 6, x: 0, y: 0)
        .shadow(color: CyberColors.skyBlue.opacity(0.15), radius: 10, x: 0, y: 2)
    }

    private func statBlock(label: String, value: String, color: Color) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.system(size: 9, weight: .bold))
                .foregroundStyle(CyberColors.textSecondary)
            Text(value)
                .font(.system(size: 15, weight: .bold, design: .monospaced))
                .foregroundStyle(color)
        }
        .frame(minWidth: 72, alignment: .leading)
    }
}

#Preview {
    LivePicksSheet(picks: [
        PickResponse(
            rank: 1,
            match: "KVC Westerlo vs Union St. Gilloise",
            market: "match_winner",
            selection: "Home",
            odds: 4.28,
            probability: 0.31,
            expectedValue: 0.12,
            confidence: 0.68,
            roiScore: 0.45,
            stakeUnits: 1.5,
            reasoning: [],
            kickoff: Date()
        ),
        PickResponse(
            rank: 2,
            match: "Team A vs Team B",
            market: "over_under",
            selection: "Under 2.5",
            odds: 2.05,
            probability: 0.55,
            expectedValue: 0.08,
            confidence: 0.62,
            roiScore: 0.3,
            stakeUnits: 1.2,
            reasoning: [],
            kickoff: nil
        ),
    ])
}
