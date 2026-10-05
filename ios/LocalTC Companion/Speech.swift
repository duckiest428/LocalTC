@preconcurrency import AVFoundation
import Speech
import SwiftUI

/// Talking instead of typing on the Comms tab: Apple's speech recognition, on the phone itself where it can be (no
/// audio leaves it then), the words going into the message box as they're heard. Tap the microphone to start, tap
/// again (or stop talking) to finish; the words are sent like typed ones, after a look.
@MainActor @Observable
final class Dictation {
    private(set) var listening = false
    var problem: String?
    private let engine = AudioTap()
    private var task: SFSpeechRecognitionTask?
    private var silence: Task<Void, Never>?

    /// The words of the radio and the cockpit, so "squawk", "flaps" and the stations come out right.
    static let vocabulary = ["squawk", "flight level", "niner", "readback", "roger", "wilco", "affirm", "negative",
                             "cleared", "runway", "taxi", "hold short", "line up and wait", "approach", "tower", "ground",
                             "center", "departure", "clearance", "flaps", "gear", "checklist", "brief", "spoilers",
                             "autobrake", "localizer", "glideslope", "ILS", "RNAV", "QNH", "altimeter", "heading"]

    func toggle(hints: [String], onText: @escaping (String) -> Void) {
        if listening { stop() } else { Task { await start(hints: hints, onText: onText) } }
    }

    func start(hints: [String], onText: @escaping (String) -> Void) async {
        problem = nil
        guard await Self.allowed() else {
            problem = "Allow Speech Recognition and the Microphone for LocalTC in the Settings app to talk instead of typing."
            return
        }
        guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US")), recognizer.isAvailable else {
            problem = "Speech recognition isn't available right now."
            return
        }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.taskHint = .dictation
        request.contextualStrings = Array((hints + Self.vocabulary).prefix(100))
        if recognizer.supportsOnDeviceRecognition { request.requiresOnDeviceRecognition = true }
        do {
            try engine.start(feeding: request)
        } catch {
            problem = "The microphone didn't start: \(error.localizedDescription)"
            return
        }
        listening = true
        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            let text = result?.bestTranscription.formattedString
            let done = error != nil || result?.isFinal == true
            Task { @MainActor in
                guard let self else { return }
                if let text, !text.isEmpty {
                    onText(text)
                    self.quietSoon()
                }
                if done { self.stop() }
            }
        }
        quietSoon(after: 6)
    }

    /// Finished talking: a moment of quiet ends it, as letting go of push-to-talk would.
    private func quietSoon(after seconds: Double = 1.6) {
        silence?.cancel()
        silence = Task { [weak self] in
            try? await Task.sleep(for: .seconds(seconds))
            if !Task.isCancelled { self?.stop() }
        }
    }

    func stop() {
        silence?.cancel()
        guard listening else { return }
        listening = false
        engine.stop()
        task?.finish()
        task = nil
    }

    private static func allowed() async -> Bool {
        let speech = await withCheckedContinuation { (done: CheckedContinuation<Bool, Never>) in
            SFSpeechRecognizer.requestAuthorization { done.resume(returning: $0 == .authorized) }
        }
        guard speech else { return false }
        return await AVAudioApplication.requestRecordPermission()
    }
}

/// The microphone feeding a recognition request (on the audio thread, away from the main actor).
private final class AudioTap: @unchecked Sendable {
    private let engine = AVAudioEngine()

    func start(feeding request: SFSpeechAudioBufferRecognitionRequest) throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .measurement, options: [.duckOthers, .defaultToSpeaker, .allowBluetooth])
        try session.setActive(true, options: .notifyOthersOnDeactivation)
        let input = engine.inputNode
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { buffer, _ in
            request.append(buffer)
        }
        engine.prepare()
        try engine.start()
    }

    func stop() {
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}

/// The play buttons on the Comms tab: a transmission as it was heard, fetched from LocalTC when it's tapped. Nothing
/// plays by itself.
@MainActor @Observable
final class ClipPlayer: NSObject, AVAudioPlayerDelegate {
    private(set) var playing: String?
    private(set) var loading: String?
    private(set) var missing: Set<String> = []
    private var player: AVAudioPlayer?

    func toggle(_ id: String, from connection: ConnectionManager) {
        if playing == id || loading == id { stop(); return }
        stop()
        loading = id
        Task {
            do {
                let data = try await connection.clip(id)
                guard loading == id else { return }
                try AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio)
                try AVAudioSession.sharedInstance().setActive(true)
                let player = try AVAudioPlayer(data: data)
                player.delegate = self
                player.play()
                self.player = player
                playing = id
            } catch {
                missing.insert(id)
            }
            if loading == id { loading = nil }
        }
    }

    func stop() {
        player?.stop()
        player = nil
        playing = nil
        loading = nil
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in self.playing = nil }
    }
}
