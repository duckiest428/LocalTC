import SwiftUI

/// What's new: once after each update, the companion's notes for its version (the same version as LocalTC), from the
/// website's changelog (site/changelog.json, built from CHANGELOG-companion.md). Not on a new install, and not seen
/// until it was shown: offline, it's tried again next time.
@MainActor @Observable
final class WhatsNew {
    struct Release: Decodable {
        let version: String
        let date: String
        let upcoming: Bool
        let notes: String
    }

    var shown: Release?

    static var version: String { Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "" }
    private let seenKey = "notesSeen"

    /// ``returning``: someone who used the app before this version (signed in), not a new install.
    func check(returning: Bool, site: URL) async {
        let defaults = UserDefaults.standard
        let version = Self.version
        guard !version.isEmpty, defaults.string(forKey: seenKey) != version else { return }
        guard returning || defaults.string(forKey: seenKey) != nil else {
            defaults.set(version, forKey: seenKey)  // a new install: nothing to catch up on
            return
        }
        guard let url = URL(string: "changelog.json", relativeTo: site),
              let (data, response) = try? await URLSession.shared.data(from: url),
              (response as? HTTPURLResponse)?.statusCode == 200,
              let all = try? JSONDecoder().decode([String: [Release]].self, from: data),
              let release = all["companion"]?.first(where: { $0.version == version && !$0.upcoming }) else { return }
        shown = release
    }

    func dismiss() {
        if let shown { UserDefaults.standard.set(shown.version, forKey: seenKey) }
        shown = nil
    }
}

struct WhatsNewSheet: View {
    let release: WhatsNew.Release
    let done: () -> Void

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                        switch block {
                        case .heading(let text):
                            Text(text.uppercased()).font(.caption.weight(.semibold)).foregroundStyle(.secondary).padding(.top, 8)
                        case .item(let text):
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Text("•").foregroundStyle(.secondary)
                                Text(markdown(text))
                            }
                        case .paragraph(let text):
                            Text(markdown(text))
                        }
                    }
                    Link("Every version's notes", destination: URL(string: "https://localtc.tech/changelog#companion")!)
                        .font(.footnote).padding(.top, 12)
                }
                .padding()
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle("What's new in \(release.version)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Got it", action: done) } }
        }
        .accessibilityIdentifier("whatsNew")
    }

    enum Block { case heading(String), item(String), paragraph(String) }

    /// The notes' Markdown: "### " headings, "- " items (continued on indented lines) and paragraphs.
    var blocks: [Block] {
        var out: [Block] = []
        var item: String?
        func flush() { if let i = item { out.append(.item(i)) }; item = nil }
        for line in release.notes.components(separatedBy: "\n") {
            if line.hasPrefix("### ") { flush(); out.append(.heading(String(line.dropFirst(4)))) }
            else if line.hasPrefix("- ") { flush(); item = String(line.dropFirst(2)).trimmingCharacters(in: .whitespaces) }
            else if line.hasPrefix("  "), let i = item { item = i + " " + line.trimmingCharacters(in: .whitespaces) }
            else if !line.trimmingCharacters(in: .whitespaces).isEmpty { flush(); out.append(.paragraph(line.trimmingCharacters(in: .whitespaces))) }
            else { flush() }
        }
        flush()
        return out
    }

    private func markdown(_ text: String) -> AttributedString {
        (try? AttributedString(markdown: text)) ?? AttributedString(text)
    }
}
