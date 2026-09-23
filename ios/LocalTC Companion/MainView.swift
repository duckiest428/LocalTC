import SwiftUI

struct MainView: View {
    @Environment(AppModel.self) private var model
    @State private var showSettings = false
    @State private var banner: FlightAlert?

    var body: some View {
        TabView {
            Tab("Map", systemImage: "map") { screen { MapTab() } }
            Tab("Radio", systemImage: "waveform") { screen { RadioTab() } }
            Tab("Flight", systemImage: "airplane.departure") { screen { FlightTab() } }
            Tab("Cockpit", systemImage: "gauge.with.dots.needle.33percent") { screen { ComingSoonView(section: .cockpit) } }
            Tab("Flight Bag", systemImage: "book.closed") { screen { ComingSoonView(section: .flightBag) } }
        }
        .overlay(alignment: .top) {
            if let banner {
                // Below the navigation bar, so the connection badge and Settings stay reachable.
                AlertBanner(alert: banner) { withAnimation { self.banner = nil } }
                    .padding(.horizontal)
                    .padding(.top, 52)
                    .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .onChange(of: model.store.alerts) { _, _ in showNextAlert() }
        .sheet(isPresented: $showSettings) { SettingsView() }
    }

    private func screen<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        NavigationStack {
            content()
                .toolbar {
                    ToolbarItem(placement: .topBarLeading) { ConnectionBadge(state: model.connection.state) }
                    ToolbarItem(placement: .topBarTrailing) {
                        Button { showSettings = true } label: { Image(systemName: "gearshape") }
                            .accessibilityLabel("Settings")
                            .accessibilityIdentifier("settings")
                    }
                }
        }
    }

    private func showNextAlert() {
        guard banner == nil, !model.store.alerts.isEmpty else { return }
        let next = model.store.alerts.removeFirst()
        model.notifyIfBackground(next)
        withAnimation { banner = next }
        let id = next.id
        Task {
            try? await Task.sleep(for: next.kind == .emergency ? .seconds(12) : .seconds(6))
            if banner?.id == id {
                withAnimation { banner = nil }
                showNextAlert()
            }
        }
    }
}

struct ConnectionBadge: View {
    let state: ConnectionManager.State

    var body: some View {
        Label(state.label, systemImage: icon)
            .labelStyle(.titleAndIcon)
            .font(.caption.weight(.semibold))
            .foregroundStyle(color)
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(color.opacity(0.15), in: Capsule())
            .accessibilityLabel("Connection: \(state.label)")
            .accessibilityIdentifier("connection")
    }

    private var icon: String {
        switch state {
        case .wifi: "wifi"
        case .server: "cloud"
        case .connecting: "arrow.triangle.2.circlepath"
        case .offline: "wifi.slash"
        }
    }

    private var color: Color {
        switch state {
        case .wifi: .green
        case .server: .blue
        case .connecting: .orange
        case .offline: .secondary
        }
    }
}

struct AlertBanner: View {
    let alert: FlightAlert
    let dismiss: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon).font(.title3).foregroundStyle(.white)
            VStack(alignment: .leading, spacing: 2) {
                Text(alert.title).font(.subheadline.bold())
                Text(alert.body).font(.subheadline).lineLimit(3)
            }
            .foregroundStyle(.white)
            Spacer(minLength: 0)
        }
        .padding(14)
        .background(color.gradient, in: RoundedRectangle(cornerRadius: 16))
        .shadow(radius: 8, y: 4)
        .onTapGesture(perform: dismiss)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
    }

    private var icon: String {
        switch alert.kind {
        case .handoff: "arrow.left.arrow.right"
        case .clearance: "checkmark.seal"
        case .traffic: "airplane"
        case .emergency: "exclamationmark.triangle.fill"
        }
    }

    private var color: Color {
        switch alert.kind {
        case .handoff: .blue
        case .clearance: .green
        case .traffic: .orange
        case .emergency: .red
        }
    }
}
