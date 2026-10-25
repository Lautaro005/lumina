// lumina-audiotap: Lumina's one audio helper on macOS. It owns the two privacy permissions, so macOS asks once
// (in Lumina's first-run walkthrough or in Settings → Permissions) and remembers the answer.
//
//   lumina-audiotap                this Mac's audio (Zoom, Meet, Teams...) on stdout, raw 32-bit float, mono, 16 kHz.
//                                  macOS 14.2+: a Core Audio process tap (System Audio Recording permission only).
//                                  macOS 13 - 14.1: ScreenCaptureKit (Screen & System Audio Recording permission).
//   lumina-audiotap mic [id]       the microphone on stdout, raw 32-bit float, mono, 16 kHz (default microphone or
//                                  the device with that id).
//   lumina-audiotap devices        the microphones as JSON.
//   lumina-audiotap status         the two permissions as JSON, without asking.
//   lumina-audiotap request mic    ask for the microphone permission (shows the macOS window once) and print the status.
//   lumina-audiotap request audio  ask for the system audio permission and print the status.
//
// Recording never asks: if a permission was not given, `mic` and the system mode exit at once with
// code 4 (refused) or 5 (not asked yet), and Lumina tells the user where to allow it.
//
// Why the permissions survive updates: macOS keeps a privacy decision for the *responsible* program, the app
// that started the process. Lumina.app is replaced (and signed ad hoc again) on every update, and macOS sees
// an ad hoc signature as a new program each time, so it asked again. This helper starts itself as its own
// responsible program (`responsibility_spawnattrs_setdisclaim`, the call Chromium and terminal apps use), and
// it is rebuilt only when this file changes, so the decision stays attached to it.
//
// Build: swiftc -O -o lumina-audiotap audiotap.swift -framework ScreenCaptureKit -framework CoreMedia \
//          -framework AVFoundation -framework CoreAudio
import AVFoundation
import CoreAudio
import CoreGraphics
import CoreMedia
import Darwin
import Foundation
import ScreenCaptureKit

setvbuf(stdout, nil, _IOFBF, 1 << 16)
signal(SIGPIPE, SIG_IGN)

func say(_ text: String) {
    FileHandle.standardError.write("\(text)\n".data(using: .utf8)!)
}

// MARK: - run as our own responsible program

var disclaimedChild: pid_t = 0

func runAsResponsible() {
    let env = ProcessInfo.processInfo.environment
    if env["LUMINA_TAP_CHILD"] != nil { return }
    guard let sym = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "responsibility_spawnattrs_setdisclaim"),
          let path = Bundle.main.executablePath else { return }
    typealias Disclaim = @convention(c) (UnsafeMutablePointer<posix_spawnattr_t?>, Int32) -> Int32
    var attr: posix_spawnattr_t? = nil
    guard posix_spawnattr_init(&attr) == 0 else { return }
    defer { posix_spawnattr_destroy(&attr) }
    guard unsafeBitCast(sym, to: Disclaim.self)(&attr, 1) == 0 else { return }
    var vars = env
    vars["LUMINA_TAP_CHILD"] = "1"
    let argv: [UnsafeMutablePointer<CChar>?] = CommandLine.arguments.map { strdup($0) } + [nil]
    let envp: [UnsafeMutablePointer<CChar>?] = vars.map { strdup("\($0.key)=\($0.value)") } + [nil]
    var pid: pid_t = 0
    guard posix_spawn(&pid, path, nil, &attr, argv, envp) == 0 else { return }  // run here instead
    disclaimedChild = pid
    // stdin, stdout and stderr are shared with the child; pass a stop on to it
    for sig in [SIGTERM, SIGINT, SIGHUP] {
        signal(sig) { s in
            if disclaimedChild > 0 { kill(disclaimedChild, s) }
            exit(0)
        }
    }
    var status: Int32 = 0
    while waitpid(pid, &status, 0) < 0 && errno == EINTR {}
    if status & 0x7f == 0 { exit((status >> 8) & 0xff) }
    exit(1)
}

// MARK: - permissions

// The System Audio Recording permission (Core Audio taps) has no public status call; TCC's own functions are
// what Apple's tools use. If they are missing, Lumina just starts the tap.
typealias TCCPreflight = @convention(c) (CFString, CFDictionary?) -> Int
typealias TCCRequest = @convention(c) (CFString, CFDictionary?, @escaping @convention(block) (Bool) -> Void) -> Void
let tcc = dlopen("/System/Library/PrivateFrameworks/TCC.framework/Versions/A/TCC", RTLD_NOW)

func tapAvailable() -> Bool {
    if #available(macOS 14.2, *) { return true }
    return false
}

func micState() -> String {
    switch AVCaptureDevice.authorizationStatus(for: .audio) {
    case .authorized: return "granted"
    case .notDetermined: return "not_asked"
    case .restricted: return "restricted"
    default: return "denied"
    }
}

func audioState() -> String {
    if tapAvailable() {
        guard let tcc = tcc, let sym = dlsym(tcc, "TCCAccessPreflight") else { return "unknown" }
        switch unsafeBitCast(sym, to: TCCPreflight.self)("kTCCServiceAudioCapture" as CFString, nil) {
        case 0: return "granted"
        case 1: return "denied"
        default: return "not_asked"
        }
    }
    return CGPreflightScreenCaptureAccess() ? "granted" : "not_asked"
}

func printStatus() {
    let method = tapAvailable() ? "tap" : "screen"
    print("{\"microphone\": \"\(micState())\", \"system_audio\": \"\(audioState())\", \"method\": \"\(method)\"}")
    fflush(stdout)
}

func request(_ what: String) {
    if what == "mic" {
        if AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined {
            AVCaptureDevice.requestAccess(for: .audio) { _ in
                printStatus()
                exit(0)
            }
            return
        }
        printStatus()
        exit(0)
    }
    if tapAvailable() {
        if audioState() != "granted", let tcc = tcc, let sym = dlsym(tcc, "TCCAccessRequest") {
            unsafeBitCast(sym, to: TCCRequest.self)("kTCCServiceAudioCapture" as CFString, nil) { _ in
                printStatus()
                exit(0)
            }
            return
        }
        if #available(macOS 14.2, *), audioState() == "unknown" {
            // no TCC functions: starting a tap for a moment shows the window
            let probe = ProcessTap()
            try? probe.start(write: false)
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                probe.stop()
                printStatus()
                exit(0)
            }
            return
        }
    } else if !CGPreflightScreenCaptureAccess() {
        _ = CGRequestScreenCaptureAccess()
    }
    printStatus()
    exit(0)
}

// MARK: - 16 kHz output

/// Linear resampler to 16 kHz mono float, kept across buffers.
final class To16k {
    var step = 1.0
    var phase = 0.0
    var prev: Float = 0
    let out = FileHandle.standardOutput

    func push(_ x: UnsafePointer<Float>, count n: Int, stride: Int, rate: Double) {
        guard n > 0 else { return }
        if rate == 16000 && stride == 1 {
            out.write(Data(bytes: x, count: n * 4))
            return
        }
        step = rate / 16000
        var y = [Float]()
        y.reserveCapacity(Int(Double(n) / step) + 2)
        func s(_ i: Int) -> Float { i < 0 ? prev : x[i * stride] }
        while phase <= Double(n - 1) {
            let i = Int(floor(phase))
            let f = Float(phase - Double(i))
            let a = s(i)
            let b = i + 1 <= n - 1 ? s(i + 1) : a
            y.append(a + (b - a) * f)
            phase += step
        }
        phase -= Double(n)
        prev = s(n - 1)
        y.withUnsafeBytes { out.write(Data($0)) }
    }
}

// MARK: - system audio: Core Audio process tap (macOS 14.2+)

@available(macOS 14.2, *)
final class ProcessTap {
    var tapID = AudioObjectID(kAudioObjectUnknown)
    var aggregateID = AudioObjectID(kAudioObjectUnknown)
    var procID: AudioDeviceIOProcID?
    let resampler = To16k()
    let queue = DispatchQueue(label: "tap")

    func property<T>(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector, _ value: inout T) -> OSStatus {
        var address = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                                                 mElement: kAudioObjectPropertyElementMain)
        var size = UInt32(MemoryLayout<T>.size)
        return AudioObjectGetPropertyData(object, &address, 0, nil, &size, &value)
    }

    func check(_ status: OSStatus, _ what: String) throws {
        if status != noErr { throw NSError(domain: "lumina-audiotap", code: Int(status), userInfo: [NSLocalizedDescriptionKey: "\(what) (\(status))"]) }
    }

    func start(write: Bool = true) throws {
        let description = CATapDescription(monoGlobalTapButExcludeProcesses: [])
        description.uuid = UUID()
        description.isPrivate = true
        description.muteBehavior = .unmuted
        try check(AudioHardwareCreateProcessTap(description, &tapID), "cannot create the audio tap")

        var outputID = AudioObjectID(kAudioObjectUnknown)
        try check(property(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDefaultSystemOutputDevice, &outputID),
                  "no output device")
        var outputUID: CFString = "" as CFString
        try check(property(outputID, kAudioDevicePropertyDeviceUID, &outputUID), "no output device id")

        let aggregate: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Lumina system audio",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceMainSubDeviceKey: outputUID as String,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outputUID as String]],
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapDriftCompensationKey: true,
                                               kAudioSubTapUIDKey: description.uuid.uuidString]],
        ]
        try check(AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &aggregateID), "cannot create the aggregate device")

        var format = AudioStreamBasicDescription()
        try check(property(tapID, kAudioTapPropertyFormat, &format), "no tap format")
        let rate = format.mSampleRate > 0 ? format.mSampleRate : 48000
        let resampler = self.resampler
        let block: AudioDeviceIOBlock = { _, input, _, _, _ in
            guard write else { return }
            let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input))
            guard let first = buffers.first, let data = first.mData else { return }
            // a mono tap gives one channel; if the device mixes more, take the first
            let stride = max(Int(first.mNumberChannels), 1)
            let frames = Int(first.mDataByteSize) / 4 / stride
            resampler.push(data.assumingMemoryBound(to: Float.self), count: frames, stride: stride, rate: rate)
        }
        try check(AudioDeviceCreateIOProcIDWithBlock(&procID, aggregateID, queue, block), "cannot read the aggregate device")
        try check(AudioDeviceStart(aggregateID, procID), "cannot start the aggregate device")
    }

    func stop() {
        if let procID = procID {
            AudioDeviceStop(aggregateID, procID)
            AudioDeviceDestroyIOProcID(aggregateID, procID)
        }
        if aggregateID != kAudioObjectUnknown { AudioHardwareDestroyAggregateDevice(aggregateID) }
        if tapID != kAudioObjectUnknown { AudioHardwareDestroyProcessTap(tapID) }
        procID = nil
        aggregateID = AudioObjectID(kAudioObjectUnknown)
        tapID = AudioObjectID(kAudioObjectUnknown)
    }
}

// MARK: - system audio: ScreenCaptureKit (macOS 13 - 14.1)

final class ScreenTap: NSObject, SCStreamOutput, SCStreamDelegate {
    var stream: SCStream?
    let out = FileHandle.standardOutput

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        guard let display = content.displays.first else {
            say("no display found")
            exit(2)
        }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = 16000
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
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio else { return }
        writeSamples(sb, to: out)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        say("stopped: \(error.localizedDescription)")
        exit(3)
    }
}

func writeSamples(_ sb: CMSampleBuffer, to out: FileHandle) {
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

// MARK: - microphone

final class MicTap: NSObject, AVCaptureAudioDataOutputSampleBufferDelegate {
    let session = AVCaptureSession()
    let out = FileHandle.standardOutput
    let queue = DispatchQueue(label: "mic")

    func start(id: String?) {
        let device = id.flatMap { AVCaptureDevice(uniqueID: $0) } ?? AVCaptureDevice.default(for: .audio)
        guard let device = device, let input = try? AVCaptureDeviceInput(device: device), session.canAddInput(input) else {
            say("no microphone found")
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
        say("ready")
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sb: CMSampleBuffer, from connection: AVCaptureConnection) {
        writeSamples(sb, to: out)
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

// MARK: - main

let args = CommandLine.arguments
let mode = args.count > 1 ? args[1] : "system"
if mode != "devices" { runAsResponsible() }

let micTap = MicTap()
let screenTap = ScreenTap()
var processTap: AnyObject?

switch mode {
case "devices":
    listDevices()
case "status":
    printStatus()
    exit(0)
case "request":
    request(args.count > 2 ? args[2] : "mic")
case "mic":
    // recording never shows a permission window: that happens once, in the walkthrough or in Settings
    switch micState() {
    case "granted": micTap.start(id: args.count > 2 ? args[2] : nil)
    case "not_asked": say("microphone permission not asked yet"); exit(5)
    default: say("microphone permission refused"); exit(4)
    }
default:
    switch audioState() {
    case "denied": say("system audio permission refused"); exit(4)
    case "not_asked": say("system audio permission not asked yet"); exit(5)
    default: break
    }
    if #available(macOS 14.2, *) {
        let tap = ProcessTap()
        do {
            try tap.start()
            processTap = tap
            say("ready")
        } catch {
            say("error: \(error.localizedDescription)")
            exit(1)
        }
    } else {
        Task {
            do {
                try await screenTap.start()
                say("ready")
            } catch {
                say("error: \(error.localizedDescription)")
                exit(1)
            }
        }
    }
}

// while recording, stop when the parent closes our stdin or dies
if mode != "request" {
    DispatchQueue.global().async {
        while true {
            let d = FileHandle.standardInput.availableData
            if d.isEmpty {
                if #available(macOS 14.2, *) { (processTap as? ProcessTap)?.stop() }
                exit(0)
            }
        }
    }
}
RunLoop.main.run()
