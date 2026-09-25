import SwiftUI
import WebKit

/// Draws shared cards with the website's own sharecard.js, in a web view: the same picture as localtc.tech
/// and the desktop app, the preview on screen and the PNG that goes up with a share (a chat app shows it
/// when the link is pasted). The page is a few lines here; the scripts, fonts and map come from the site.
@MainActor
final class CardRenderer: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
    let webView: WKWebView
    private var ready: CheckedContinuation<Void, Never>?
    private var loaded = false
    private var pending: [CheckedContinuation<Data, Error>] = []

    init(site: URL) {
        let config = WKWebViewConfiguration()
        let controller = WKUserContentController()
        config.userContentController = controller
        webView = WKWebView(frame: CGRect(x: 0, y: 0, width: 600, height: 315), configuration: config)
        webView.isOpaque = false
        webView.backgroundColor = .clear
        webView.scrollView.isScrollEnabled = false
        super.init()
        controller.add(self, name: "localtcCard")
        webView.navigationDelegate = self
        webView.loadHTMLString(Self.page, baseURL: site)
    }

    private static let page = """
    <!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="styles.css"><link rel="stylesheet" href="sharecard.css">
    <style>html,body{margin:0;background:transparent}.share-card{border-radius:14px;box-shadow:none}</style></head>
    <body><div id="card" class="share-card"></div>
    <script src="sharecard.js"></script><script src="wrapped.js"></script>
    <script>
    let model = null;
    const el = document.getElementById("card");
    const post = (v) => window.webkit.messageHandlers.localtcCard.postMessage(v);
    const dataUrl = (blob) => new Promise((ok) => { const r = new FileReader(); r.onload = () => ok(r.result); r.readAsDataURL(blob); });
    async function card(spec) {
      if (spec.flight) {
        model = model || await import("./cardmodel.js");
        return model.flightCard(spec.flight, { quote: spec.quote || null, names: spec.names || {} });
      }
      if (spec.slide) return spec.slide.kind === "summary" ? { ...spec.recap.card, name: "summary" } : WrappedView.slideCard(spec.slide, spec.recap);
      return spec.card;
    }
    window.showCard = async (spec) => { try { await ShareCard.mount(el, await card(spec), { base: "" }); } catch (e) { post("error:" + e.message); } };
    window.renderCard = async (spec) => {
      try { post(await dataUrl(await ShareCard.png(await card(spec), { base: "" }))); } catch (e) { post("error:" + e.message); }
    };
    post("ready");
    </script></body></html>
    """

    private func whenReady() async {
        if loaded { return }
        await withCheckedContinuation { ready = $0 }
    }

    /// Show a card: {"flight": <logbook line>, "quote": ..., "names": ...}, {"card": <snapshot>},
    /// or {"slide": ..., "recap": ...}.
    func show(_ spec: [String: Any]) async {
        await whenReady()
        _ = try? await webView.callAsyncJavaScript("await showCard(spec)", arguments: ["spec": spec], contentWorld: .page)
    }

    /// The card as a PNG, 1200 x 630.
    func png(_ spec: [String: Any]) async throws -> Data {
        await whenReady()
        return try await withCheckedThrowingContinuation { continuation in
            pending.append(continuation)
            webView.callAsyncJavaScript("renderCard(spec)", arguments: ["spec": spec], in: nil, in: .page) { _ in }
        }
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let text = message.body as? String else { return }
        if text == "ready" {
            loaded = true
            ready?.resume()
            ready = nil
            return
        }
        guard !pending.isEmpty else { return }
        let continuation = pending.removeFirst()
        if text.hasPrefix("error:") {
            continuation.resume(throwing: APIError(status: 0, message: "The card didn't draw: \(text.dropFirst(6))"))
        } else if let comma = text.firstIndex(of: ","), let data = Data(base64Encoded: String(text[text.index(after: comma)...])) {
            continuation.resume(returning: data)
        } else {
            continuation.resume(throwing: APIError(status: 0, message: "The card didn't draw."))
        }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        loaded = true  // nothing will come: let the waiting go, and the calls fail on their own
        ready?.resume()
        ready = nil
    }
}

/// The renderer's web view, on screen: the card's preview.
struct CardPreview: UIViewRepresentable {
    let renderer: CardRenderer

    func makeUIView(context: Context) -> WKWebView { renderer.webView }
    func updateUIView(_ view: WKWebView, context: Context) {}
}

extension JSONSerialization {
    /// A JSON object as a dictionary, for handing to the web view.
    static func dictionary(_ data: Data) -> [String: Any] {
        (try? jsonObject(with: data) as? [String: Any]) ?? [:]
    }
}
