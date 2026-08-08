import SwiftUI

// MARK: - Cyber Neon Design System

enum CyberColors {
    static let background = Color(hex: "000000")
    static let backgroundDeep = Color(hex: "050508")
    static let backgroundMid = Color(hex: "0A0515")
    static let panelFill = Color(hex: "0A0A10").opacity(0.72)
    static let panelFillHighlight = Color(hex: "12121C").opacity(0.55)
    static let cyan = Color(hex: "00F0FF")
    static let green = Color(hex: "00FF66")
    static let red = Color(hex: "FF0055")
    static let purple = Color(hex: "9D00FF")
    static let violet = Color(hex: "7B2FFF")
    static let textPrimary = Color.white
    static let textSecondary = Color.white.opacity(0.65)
    static let gridLine = Color.white.opacity(0.05)
}

extension Color {
    init(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let r, g, b: Double
        switch hex.count {
        case 6:
            r = Double((int >> 16) & 0xFF) / 255
            g = Double((int >> 8) & 0xFF) / 255
            b = Double(int & 0xFF) / 255
        default:
            r = 1; g = 1; b = 1
        }
        self.init(red: r, green: g, blue: b)
    }
}

// MARK: - Glass Panel Modifier

struct GlassPanel: ViewModifier {
    var borderColor: Color
    var glowRadius: CGFloat = 12
    var dualNeonBorder: Bool = false

    func body(content: Content) -> some View {
        content
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(
                        LinearGradient(
                            colors: [
                                CyberColors.panelFillHighlight,
                                CyberColors.panelFill,
                            ],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .background(
                        RoundedRectangle(cornerRadius: 16, style: .continuous)
                            .fill(.ultraThinMaterial.opacity(0.45))
                    )
            )
            .overlay {
                if dualNeonBorder {
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .stroke(
                            LinearGradient(
                                colors: [
                                    CyberColors.cyan.opacity(0.95),
                                    CyberColors.violet.opacity(0.85),
                                    CyberColors.purple.opacity(0.9),
                                    CyberColors.cyan.opacity(0.75),
                                ],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            ),
                            lineWidth: 1.5
                        )
                } else {
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .stroke(borderColor.opacity(0.9), lineWidth: 1.5)
                }
            }
            .shadow(color: CyberColors.cyan.opacity(dualNeonBorder ? 0.35 : 0), radius: glowRadius * 0.6, x: 0, y: 0)
            .shadow(color: borderColor.opacity(0.5), radius: glowRadius, x: 0, y: 0)
            .shadow(color: CyberColors.purple.opacity(dualNeonBorder ? 0.3 : 0), radius: glowRadius * 0.8, x: 0, y: 2)
    }
}

extension View {
    func glassPanel(border: Color, glow: CGFloat = 12, dualNeon: Bool = false) -> some View {
        modifier(GlassPanel(borderColor: border, glowRadius: glow, dualNeonBorder: dualNeon))
    }
}

// MARK: - Neon Glow Text

struct NeonText: View {
    let text: String
    var color: Color = CyberColors.cyan
    var size: CGFloat = 14
    var weight: Font.Weight = .bold

    var body: some View {
        Text(text)
            .font(.system(size: size, weight: weight, design: .rounded))
            .foregroundStyle(color)
            .shadow(color: color.opacity(0.8), radius: 8, x: 0, y: 0)
    }
}

// MARK: - Odds Direction Indicator

struct NeonDirectionArrow: View {
    let direction: OddsDirection

    var body: some View {
        switch direction {
        case .up:
            Image(systemName: "arrow.up")
                .font(.system(size: 11, weight: .black))
                .foregroundStyle(CyberColors.green)
                .shadow(color: CyberColors.green.opacity(0.95), radius: 8, x: 0, y: 0)
                .shadow(color: CyberColors.green.opacity(0.5), radius: 3, x: 0, y: 0)
        case .down:
            Image(systemName: "arrow.down")
                .font(.system(size: 11, weight: .black))
                .foregroundStyle(CyberColors.red)
                .shadow(color: CyberColors.red.opacity(0.95), radius: 8, x: 0, y: 0)
                .shadow(color: CyberColors.red.opacity(0.5), radius: 3, x: 0, y: 0)
        case .flat:
            Image(systemName: "minus")
                .font(.system(size: 9, weight: .bold))
                .foregroundStyle(CyberColors.textSecondary.opacity(0.55))
        }
    }
}

// MARK: - Cyber Grid Background

struct CyberGridBackground: View {
    @State private var breathe = false

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [
                    CyberColors.backgroundDeep,
                    breathe ? CyberColors.backgroundMid : Color(hex: "080812"),
                    CyberColors.backgroundDeep,
                ],
                startPoint: breathe ? .topLeading : .top,
                endPoint: breathe ? .bottomTrailing : .bottom
            )
            .ignoresSafeArea()

            // Top-left cyan aura
            RadialGradient(
                colors: [CyberColors.cyan.opacity(0.18), CyberColors.cyan.opacity(0.04), .clear],
                center: .topLeading,
                startRadius: 20,
                endRadius: 320
            )
            .ignoresSafeArea()

            // Bottom-right purple aura
            RadialGradient(
                colors: [CyberColors.purple.opacity(0.16), CyberColors.violet.opacity(0.05), .clear],
                center: .bottomTrailing,
                startRadius: 30,
                endRadius: 340
            )
            .ignoresSafeArea()

            // Top-right subtle violet
            RadialGradient(
                colors: [CyberColors.violet.opacity(0.1), .clear],
                center: UnitPoint(x: 0.92, y: 0.08),
                startRadius: 10,
                endRadius: 220
            )
            .ignoresSafeArea()

            // Bottom-left subtle cyan
            RadialGradient(
                colors: [CyberColors.cyan.opacity(0.08), .clear],
                center: UnitPoint(x: 0.06, y: 0.88),
                startRadius: 10,
                endRadius: 200
            )
            .ignoresSafeArea()

            GeometryReader { geo in
                Path { path in
                    let spacing: CGFloat = 28
                    var x: CGFloat = 0
                    while x < geo.size.width {
                        path.move(to: CGPoint(x: x, y: 0))
                        path.addLine(to: CGPoint(x: x, y: geo.size.height))
                        x += spacing
                    }
                    var y: CGFloat = geo.size.height * 0.45
                    while y < geo.size.height {
                        path.move(to: CGPoint(x: 0, y: y))
                        path.addLine(to: CGPoint(x: geo.size.width, y: y))
                        y += spacing
                    }
                }
                .stroke(
                    LinearGradient(
                        colors: [
                            CyberColors.gridLine,
                            CyberColors.cyan.opacity(0.06),
                            CyberColors.gridLine,
                        ],
                        startPoint: .top,
                        endPoint: .bottom
                    ),
                    lineWidth: 0.5
                )
            }
            .ignoresSafeArea()
        }
        .onAppear {
            withAnimation(.easeInOut(duration: 5).repeatForever(autoreverses: true)) {
                breathe = true
            }
        }
    }
}

// MARK: - Pulsing Online Badge

struct PulsingOnlineBadge: View {
    let isOnline: Bool
    @State private var pulse = false

    var body: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(isOnline ? CyberColors.green : CyberColors.red)
                .frame(width: 8, height: 8)
                .shadow(color: (isOnline ? CyberColors.green : CyberColors.red).opacity(0.9), radius: pulse ? 10 : 4)
                .scaleEffect(pulse ? 1.2 : 0.9)

            Text(isOnline ? "DC ENGINE: ONLINE" : "DC ENGINE: OFFLINE")
                .font(.system(size: 11, weight: .bold, design: .monospaced))
                .foregroundStyle(isOnline ? CyberColors.green : CyberColors.red)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
        .glassPanel(border: isOnline ? CyberColors.green : CyberColors.red, glow: 8)
        .onAppear {
            withAnimation(.easeInOut(duration: 1.2).repeatForever(autoreverses: true)) {
                pulse = true
            }
        }
    }
}

// MARK: - Action Grid Button

struct NeonActionButton: View {
    let title: String
    let icon: String
    let accent: Color
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 10) {
                Image(systemName: icon)
                    .font(.system(size: 28, weight: .semibold))
                    .foregroundStyle(accent)
                    .shadow(color: accent.opacity(0.7), radius: 10)

                Text(title)
                    .font(.system(size: 10, weight: .bold, design: .rounded))
                    .foregroundStyle(CyberColors.textPrimary)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .minimumScaleFactor(0.8)
            }
            .frame(maxWidth: .infinity, minHeight: 96)
            .glassPanel(border: accent, glow: 10)
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Toast

struct ToastView: View {
    let message: String
    var success: Bool = true

    var body: some View {
        Text(message)
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(.white)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .glassPanel(border: success ? CyberColors.green : CyberColors.red, glow: 6)
    }
}
