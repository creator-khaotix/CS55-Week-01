// claude-voice-audio: echo-cancelled mic + speech playback for claude-voice.
//
// Uses Apple's Voice Processing I/O, the echo cancellation FaceTime uses, so
// the MacBook's speakers and mic work together without headphones. Echo
// cancellation only removes sound played through this same audio engine, so
// replies are synthesised and played here (AVSpeechSynthesizer, the same
// voices as `say`) rather than by a separate process.
//
// Protocol with the Python side:
//   stdout  raw mic audio: 16 kHz mono signed 16-bit little-endian PCM
//   stdin   one JSON command per line:
//             {"cmd":"speak","id":1,"text":"Hello","voice":"Samantha","rate":1.0}
//             {"cmd":"stop"}
//   stderr  "EVT ready" | "EVT done <id>" | "EVT error <message>"; other lines are logs

import AVFoundation
import Foundation

func emit(_ line: String) {
    FileHandle.standardError.write(("EVT " + line + "\n").data(using: .utf8)!)
}

/// Convert one buffer, keeping the converter's state for the next call.
func convert(_ buffer: AVAudioPCMBuffer, _ converter: AVAudioConverter) -> AVAudioPCMBuffer? {
    let ratio = converter.outputFormat.sampleRate / buffer.format.sampleRate
    let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 1024
    guard let out = AVAudioPCMBuffer(pcmFormat: converter.outputFormat, frameCapacity: capacity) else {
        return nil
    }
    var consumed = false
    var error: NSError?
    let status = converter.convert(to: out, error: &error) { _, inputStatus in
        if consumed {
            inputStatus.pointee = .noDataNow
            return nil
        }
        consumed = true
        inputStatus.pointee = .haveData
        return buffer
    }
    return status == .error ? nil : out
}

final class AudioHelper {
    let engine = AVAudioEngine()
    let player = AVAudioPlayerNode()
    let synth = AVSpeechSynthesizer()
    let micFormat = AVAudioFormat(
        commonFormat: .pcmFormatInt16, sampleRate: 16_000, channels: 1, interleaved: true)!
    let playFormat = AVAudioFormat(standardFormatWithSampleRate: 48_000, channels: 1)!
    var micConverter: AVAudioConverter?
    var speechConverter: AVAudioConverter?
    var generation = 0  // bumped by speak/stop so stale callbacks are ignored
    let micOut = FileHandle.standardOutput

    func start() throws {
        let input = engine.inputNode
        try input.setVoiceProcessingEnabled(true)  // also enables it on the output side

        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: playFormat)

        let inFormat = input.outputFormat(forBus: 0)
        guard let conv = AVAudioConverter(from: inFormat, to: micFormat) else {
            throw NSError(domain: "claude-voice-audio", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "can't convert mic format \(inFormat)",
            ])
        }
        conv.downmix = true  // voice processing can report several input channels
        micConverter = conv
        input.installTap(onBus: 0, bufferSize: 4800, format: inFormat) { [weak self] buffer, _ in
            self?.onMic(buffer)
        }

        engine.prepare()
        try engine.start()
        player.play()
    }

    func onMic(_ buffer: AVAudioPCMBuffer) {  // audio thread
        guard let conv = micConverter, let pcm = convert(buffer, conv), pcm.frameLength > 0,
            let samples = pcm.int16ChannelData
        else { return }
        micOut.write(Data(bytes: samples[0], count: Int(pcm.frameLength) * 2))
    }

    func speak(id: Int, text: String, voice: String?, rate: Double?) {
        generation += 1
        let gen = generation
        speechConverter?.reset()

        let utterance = AVSpeechUtterance(string: text)
        if let voice = voice,
            let match = AVSpeechSynthesisVoice.speechVoices().first(where: {
                $0.name == voice || $0.identifier == voice
            })
        {
            utterance.voice = match
        }
        if let rate = rate {
            let r = Double(AVSpeechUtteranceDefaultSpeechRate) * rate
            utterance.rate = Float(
                min(max(r, Double(AVSpeechUtteranceMinimumSpeechRate)),
                    Double(AVSpeechUtteranceMaximumSpeechRate)))
        }
        synth.write(utterance) { [weak self] buffer in
            DispatchQueue.main.async { self?.onSpeech(buffer, id: id, gen: gen) }
        }
    }

    func onSpeech(_ buffer: AVAudioBuffer, id: Int, gen: Int) {
        guard gen == generation, let pcm = buffer as? AVAudioPCMBuffer else { return }
        if pcm.frameLength == 0 {
            // End of the utterance: report "done" once everything queued has played.
            let marker = AVAudioPCMBuffer(pcmFormat: playFormat, frameCapacity: 1)!
            marker.frameLength = 1
            player.scheduleBuffer(marker, completionCallbackType: .dataPlayedBack) { _ in
                DispatchQueue.main.async {
                    if gen == self.generation { emit("done \(id)") }
                }
            }
            return
        }
        if speechConverter == nil || speechConverter!.inputFormat != pcm.format {
            speechConverter = AVAudioConverter(from: pcm.format, to: playFormat)
        }
        guard let conv = speechConverter, let converted = convert(pcm, conv) else { return }
        player.scheduleBuffer(converted, completionHandler: nil)
    }

    func stop() {
        generation += 1
        synth.stopSpeaking(at: .immediate)
        player.stop()  // drops everything scheduled
        player.play()
    }
}

let helper = AudioHelper()
do {
    try helper.start()
    emit("ready")
} catch {
    emit("error \(error.localizedDescription)")
    exit(1)
}

Thread {
    while let line = readLine() {
        guard let data = line.data(using: .utf8),
            let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let cmd = obj["cmd"] as? String
        else { continue }
        let id = obj["id"] as? Int ?? 0
        let text = obj["text"] as? String ?? ""
        let voice = obj["voice"] as? String
        let rate = obj["rate"] as? Double
        DispatchQueue.main.async {
            switch cmd {
            case "speak": helper.speak(id: id, text: text, voice: voice, rate: rate)
            case "stop": helper.stop()
            default: break
            }
        }
    }
    exit(0)  // stdin closed: claude-voice has quit
}.start()

dispatchMain()
