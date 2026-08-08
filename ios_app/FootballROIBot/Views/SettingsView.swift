import SwiftUI

struct SettingsView: View {
    @AppStorage(AppConfig.baseURLKey) private var baseURL = AppConfig.defaultBaseURL
    @AppStorage(AppConfig.pollIntervalKey) private var pollIntervalSeconds = AppConfig.defaultPollInterval

    @Environment(\.dismiss) private var dismiss

    @State private var isTesting = false
    @State private var connectionOK: Bool?
    @State private var testMessage = ""
    @State private var botStatus: BotStatusResponse?
    @State private var config: ConfigResponse?
    @State private var isLoadingStatus = false

    var body: some View {
        NavigationStack {
            ZStack {
                CyberGridBackground()

                ScrollView {
                    VStack(spacing: 20) {
                        urlSection
                        connectionIndicator
                        pollSection
                        if let s = botStatus { statusSection(s) }
                        if let c = config { configSection(c) }
                    }
                    .padding()
                }
            }
            .navigationTitle("STATUS BOTA")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Sačuvaj") {
                        baseURL = AppConfig.normalizeURL(baseURL)
                        AppState.shared.startPolling()
                        dismiss()
                    }
                    .foregroundStyle(CyberColors.cyan)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Zatvori") { dismiss() }
                        .foregroundStyle(CyberColors.purple)
                }
            }
            .task { await refreshBotMetadata() }
        }
        .preferredColorScheme(.dark)
    }

    // MARK: - Sections

    private var urlSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            NeonText(text: "TAILSCALE SERVER URL", color: CyberColors.cyan, size: 12)

            TextField("http://100.122.226.3:8001/api/v1", text: $baseURL)
                .font(.system(size: 14, design: .monospaced))
                .foregroundStyle(.white)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .padding(12)
                .glassPanel(border: CyberColors.cyan, glow: 8)

            Text("Primer: http://100.122.226.3:8001/api/v1 (port 8001 — PrelaziBot koristi 8000)")
                .font(.caption)
                .foregroundStyle(CyberColors.textSecondary)

            Button(action: { Task { await testConnection() } }) {
                HStack {
                    if isTesting {
                        ProgressView().tint(.white)
                    } else {
                        Image(systemName: "antenna.radiowaves.left.and.right")
                    }
                    Text("Testiraj Konekciju")
                        .font(.system(size: 14, weight: .bold))
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
                .glassPanel(border: CyberColors.green, glow: 10)
            }
            .buttonStyle(.plain)
            .disabled(isTesting)
        }
    }

    @ViewBuilder
    private var connectionIndicator: some View {
        if let ok = connectionOK {
            HStack(spacing: 10) {
                Circle()
                    .fill(ok ? CyberColors.green : CyberColors.red)
                    .frame(width: 10, height: 10)
                    .shadow(color: (ok ? CyberColors.green : CyberColors.red).opacity(0.9), radius: 8)

                Text(testMessage)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(ok ? CyberColors.green : CyberColors.red)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
            .glassPanel(border: ok ? CyberColors.green : CyberColors.red, glow: 8)
        }
    }

    private var pollSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            NeonText(text: "POLL INTERVAL", color: CyberColors.purple, size: 12)
            Stepper(
                "Osvežavanje: \(Int(pollIntervalSeconds))s",
                value: $pollIntervalSeconds,
                in: 10...60,
                step: 5
            )
            .foregroundStyle(.white)
            .padding(12)
            .glassPanel(border: CyberColors.purple, glow: 6)
        }
    }

    private func statusSection(_ s: BotStatusResponse) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            NeonText(text: "BOT STATUS", color: CyberColors.green, size: 12)
            statusRow("Verzija", s.version)
            statusRow("DC Engine", s.dcEngine)
            statusRow("API key", s.apiConfigured ? "Configured" : "Missing")
            statusRow("Lige", "\(s.leagueCount)")
            statusRow("Open fallback", "\(s.maxOpenFixtures)")
            statusRow("Calibrated CONF", s.useCalibratedConfidence ? "ON" : "OFF")
        }
        .padding(12)
        .glassPanel(border: CyberColors.green, glow: 6)
    }

    private func configSection(_ c: ConfigResponse) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            NeonText(text: "KONFIGURACIJA", color: CyberColors.cyan, size: 12)
            statusRow("Min EV", String(format: "%.1f%%", c.minEvThreshold * 100))
            statusRow("Min CONF", String(format: "%.0f%%", c.minConfidenceThreshold * 100))
            statusRow("Max picks/dan", "\(c.maxDailyPicks)")
        }
        .padding(12)
        .glassPanel(border: CyberColors.cyan, glow: 6)
    }

    private func statusRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label)
                .font(.caption)
                .foregroundStyle(CyberColors.textSecondary)
            Spacer()
            Text(value)
                .font(.caption.bold())
                .foregroundStyle(.white)
        }
    }

    // MARK: - Actions

    private func testConnection() async {
        isTesting = true
        connectionOK = nil
        defer { isTesting = false }

        baseURL = AppConfig.normalizeURL(baseURL)

        do {
            let health = try await APIClient.shared.health()
            connectionOK = health.status == "ok"
            testMessage = connectionOK == true
                ? "Tailscale OK — server v\(health.version)"
                : "Server odgovara ali status nije OK"
            if connectionOK == true {
                await refreshBotMetadata()
                AppState.shared.startPolling()
            }
        } catch {
            connectionOK = false
            testMessage = error.localizedDescription
        }
    }

    private func refreshBotMetadata() async {
        isLoadingStatus = true
        defer { isLoadingStatus = false }
        do {
            botStatus = try await APIClient.shared.botStatus()
            config = try await APIClient.shared.config()
        } catch {
            botStatus = nil
            config = nil
        }
    }
}

#Preview {
    SettingsView()
}
