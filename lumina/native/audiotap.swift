// lumina-audiotap: Lumina's one audio helper on macOS, so the system asks for each permission once and
// remembers it for this single program.
//
//   lumina-audiotap          this Mac's system audio (what you hear from Zoom, Meet, Teams...) on stdout,
//                            raw 32-bit float, mono, 48 kHz. Uses ScreenCaptureKit (macOS 13+): asks for the
//                            Screen & System Audio Recording permission. Lumina's own audio is excluded.
//   lumina-audiotap mic [id] the microphone on stdout, raw 32-bit float, mono, 16 kHz (default microphone
//                            or the device with that id). Asks for the Microphone permission once.
//                            Exit code 4 means the permission was refused.
//   lumina-audiotap devices  the microphones as JSON.
//   lumina-audiotap status   the two permissions as JSON (microphone: 0 not asked, 1 restricted, 2 refused, 3 allowed).
//
// Build: swiftc -O -o lumina-audiotap audiotap.swift -framework ScreenCaptureKit -framework CoreMedia -framework AVFoundation
import AVFoundation
import CoreGraphics
import CoreMedia
import Foundation
import ScreenCaptureKit

setvbuf(stdout, nil, _IOFBF, 1 << 16)

final class Tap: NSObject, SCStreamOutput, SCStreamDelegate {
    var stream: SCStream?
    let out = FileHandle.standardOutput

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        guard let display = content.displays.first else {
            FileHandle.standardError.write("no display found\n".data(using: .utf8)!)
            exit(2)
        }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = 48000
        config.channelCount = 1
        // video is required by the API; keep it tiny and slow
        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        config.queueDepth = 3
        let s = SCStream(filter: filter, configuration: config, delegate: self)
        try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: DispatchQueue(label: "audio"))
        try s.addStreamOutput(self, type: .screen, sampleHandlerQueue: DispatchQueue(label: "video"))
        try await s.startCapture()
        stream = s
        FileHandle.standardError.write("ready\n".data(using: .utf8)!)
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sb.isValid else { return }
        var blockBuffer: CMBlockBuffer?
        var abl = AudioBufferList()
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sb, bufferListSizeNeededOut: nil, bufferListOut: &abl,
            bufferListSize: MemoryLayout<AudioBufferList>.size, blockBufferAllocator: nil,
            blockBufferMemoryAllocator: nil, flags: 0, blockBufferOut: &blockBuffer)
        guard status == noErr else { return }
        let buffers = UnsafeMutableAudioBufferListPointer(&abl)
        if let first = buffers.first, let data = first.mData {
            out.write(Data(bytes: data, count: Int(first.mDataByteSize)))
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        FileHandle.standardError.write("stopped: \(error.localizedDescription)\n".data(using: .utf8)!)
        exit(3)
    }
}

final class MicTap: NSObject, AVCaptureAudioDataOutputSampleBufferDelegate {
    let session = AVCaptureSession()
    let out = FileHandle.standardOutput
    let queue = DispatchQueue(label: "mic")

    func authorize() {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            return
        case .notDetermined:
            let wait = DispatchSemaphore(value: 0)
            var ok = false
            AVCaptureDevice.requestAccess(for: .audio) { granted in ok = granted; wait.signal() }
            wait.wait()
            if !ok { refuse() }
        default:
            refuse()
        }
    }

    func refuse() -> Never {
        FileHandle.standardError.write("microphone permission refused\n".data(using: .utf8)!)
        exit(4)
    }

    func start(id: String?) {
        // the permission window is answered on another thread so the main one stays free to run its loop
        DispatchQueue.global().async {
            self.authorize()
            DispatchQueue.main.async { self.begin(id: id) }
        }
    }

    func begin(id: String?) {
        let device = id.flatMap { AVCaptureDevice(uniqueID: $0) } ?? AVCaptureDevice.default(for: .audio)
        guard let device = device, let input = try? AVCaptureDeviceInput(device: device), session.canAddInput(input) else {
            FileHandle.standardError.write("no microphone found\n".data(using: .utf8)!)
            exit(2)
        }
        session.addInput(input)
        let output = AVCaptureAudioDataOutput()
        output.audioSettings = [
            AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 16000, AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 32, AVLinearPCMIsFloatKey: true, AVLinearPCMIsBigEndianKey: false,
            AVLinearPCMIsNonInterleaved: false,
        ]
        output.setSampleBufferDelegate(self, queue: queue)
        guard session.canAddOutput(output) else { exit(2) }
        session.addOutput(output)
        session.startRunning()
        FileHandle.standardError.write("ready\n".data(using: .utf8)!)
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sb: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard sb.isValid else { return }
        var blockBuffer: CMBlockBuffer?
        var abl = AudioBufferList()
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sb, bufferListSizeNeededOut: nil, bufferListOut: &abl,
            bufferListSize: MemoryLayout<AudioBufferList>.size, blockBufferAllocator: nil,
            blockBufferMemoryAllocator: nil, flags: 0, blockBufferOut: &blockBuffer)
        guard status == noErr else { return }
        let buffers = UnsafeMutableAudioBufferListPointer(&abl)
        if let first = buffers.first, let data = first.mData {
            out.write(Data(bytes: data, count: Int(first.mDataByteSize)))
        }
    }
}

func listDevices() {
    let fallback = AVCaptureDevice.default(for: .audio)?.uniqueID
    var items: [[String: Any]] = []
    for d in AVCaptureDevice.devices(for: .audio) {
        items.append(["id": d.uniqueID, "name": d.localizedName, "default": d.uniqueID == fallback])
    }
    if let data = try? JSONSerialization.data(withJSONObject: items), let text = String(data: data, encoding: .utf8) {
        print(text)
    }
    exit(0)
}

signal(SIGPIPE, SIG_IGN)
let args = CommandLine.arguments
let mode = args.count > 1 ? args[1] : "system"
let micTap = MicTap()
let tap = Tap()
switch mode {
case "devices":
    listDevices()
case "status":
    // 0 not asked yet, 1 restricted, 2 refused, 3 allowed
    let mic = AVCaptureDevice.authorizationStatus(for: .audio).rawValue
    print("{\"microphone\": \(mic), \"screen\": \(CGPreflightScreenCaptureAccess())}")
    exit(0)
case "mic":
    micTap.start(id: args.count > 2 ? args[2] : nil)
default:
    Task {
        do {
            try await tap.start()
        } catch {
            FileHandle.standardError.write("error: \(error.localizedDescription)\n".data(using: .utf8)!)
            exit(1)
        }
    }
}
// stop when the parent closes our stdin or dies
DispatchQueue.global().async {
    while true {
        let d = FileHandle.standardInput.availableData
        if d.isEmpty { exit(0) }
    }
}
RunLoop.main.run()
