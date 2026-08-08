import SwiftUI

// MARK: - Cyber Neon Design System

enum CyberColors {
    static let background = Color(hex: "000000")
    static let panelFill = Color(hex: "0A0A10").opacity(0.85)
    static let cyan = Color(hex: "00F0FF")
    static let green = Color(hex: "00FF66")
    static let red = Color(hex: "FF0055")
    static let purple = Color(hex: "9D00FF")
    static let textPrimary = Color.white
    static let textSecondary = Color.white.opacity(0.65)
    static let gridLine = Color.white.opacity(0.04)
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

    func body(content: Content) -> some View {
        content
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(CyberColors.panelFill)
                    .background(
                        RoundedRectangle(cornerRadius: 16, style: .continuous)
                            .fill(.ultraThinMaterial.opacity(0.3))
                    )
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(borderColor.opacity(0.9), lineWidth: 1.5)
            )
            .shadow(color: borderColor.opacity(0.45), radius: glowRadius, x: 0, y: 0)
    }
}

extension View {
    func glassPanel(border: Color, glow: CGFloat = 12) -> some View {
        modifier(GlassPanel(borderColor: border, glowRadius: glow))
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

// MARK: - Cyber Grid Background

struct CyberGridBackground: View {
    var body: some View {
        ZStack {
            CyberColors.background.ignoresSafeArea()
            GeometryReader { geo in
                Path { path in
                    let spacing: CGFloat = 28
                    var x: CGFloat = 0
                    while x < geo.size.width {
                        path.move(to: CGPoint(x: x, y: 0))
                        path.addLine(to: CGPoint(x: x, y: geo.size.height))
                        x += spacing
                    }
                    var y: CGFloat = geo.size.height * 0.55
                    while y < geo.size.height {
                        path.move(to: CGPoint(x: 0, y: y))
                        path.addLine(to: CGPoint(x: geo.size.width, y: y))
                        y += spacing
                    }
                }
                .stroke(CyberColors.gridLine, lineWidth: 0.5)
            }
            .ignoresSafeArea()
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
            .frame(maxWidth: .infinity, minHeight: 100)
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
