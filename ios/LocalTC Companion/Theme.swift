import SwiftUI

/// The companion's look, picked in Settings → Appearance: the same themes as the desktop app's Quick Settings, plus
/// following the phone's own light or dark mode.
enum AppTheme: String, CaseIterable, Identifiable {
    case system, radio, midnight, oled, amber, slate, daylight

    var id: String { rawValue }

    var label: String {
        switch self {
        case .system: "Follow the phone"
        case .radio: "Radio panel"
        case .midnight: "Midnight blue"
        case .oled: "Black (OLED)"
        case .amber: "Amber avionics"
        case .slate: "Slate"
        case .daylight: "Daylight"
        }
    }

    /// Light or dark: nil follows the phone.
    var scheme: ColorScheme? {
        switch self {
        case .system: nil
        case .daylight: .light
        default: .dark
        }
    }

    /// Buttons, selections, the own aircraft's highlights.
    var accent: Color {
        switch self {
        case .system, .radio, .oled: Color(red: 0.37, green: 0.82, blue: 0.41)
        case .midnight: Color(red: 0.42, green: 0.79, blue: 0.94)
        case .amber: Color(red: 1.0, green: 0.70, blue: 0.28)
        case .slate: Color(red: 0.31, green: 0.76, blue: 0.97)
        case .daylight: Color(red: 0.11, green: 0.54, blue: 0.22)
        }
    }

    /// Behind the screens and lists; nil keeps the system's.
    var background: Color? {
        switch self {
        case .system, .daylight: nil
        case .radio: Color(red: 0.106, green: 0.118, blue: 0.133)
        case .midnight: Color(red: 0.055, green: 0.082, blue: 0.141)
        case .oled: .black
        case .amber: Color(red: 0.082, green: 0.063, blue: 0.039)
        case .slate: Color(red: 0.125, green: 0.149, blue: 0.176)
        }
    }

    /// Three colors for its swatch in Settings.
    var swatch: [Color] {
        [background ?? (self == .daylight ? Color(white: 0.93) : Color(.systemBackground)),
         (background ?? Color(.secondarySystemBackground)).opacity(0.7), accent]
    }
}

extension View {
    /// The theme's background behind a screen (and a form's rows on top of it), where the theme has one.
    @ViewBuilder func themed(_ theme: AppTheme) -> some View {
        if let background = theme.background {
            self.scrollContentBackground(.hidden).background(background.ignoresSafeArea())
        } else {
            self
        }
    }
}

struct ThemePicker: View {
    @AppStorage("theme") private var theme = AppTheme.system.rawValue

    var body: some View {
        ForEach(AppTheme.allCases) { option in
            Button { theme = option.rawValue } label: {
                HStack(spacing: 12) {
                    HStack(spacing: 0) {
                        ForEach(Array(option.swatch.enumerated()), id: \.offset) { _, color in color }
                    }
                    .frame(width: 54, height: 22)
                    .clipShape(RoundedRectangle(cornerRadius: 5))
                    .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(.secondary.opacity(0.4)))
                    Text(option.label).foregroundStyle(.primary)
                    Spacer()
                    if option.rawValue == theme { Image(systemName: "checkmark").foregroundStyle(.tint) }
                }
            }
            .accessibilityAddTraits(option.rawValue == theme ? .isSelected : [])
        }
    }
}
