// lumina-audiotap: writes this Mac's system audio (what you hear from Zoom, Meet, Teams...) to stdout
// as raw 32-bit float, mono, 48 kHz. Uses ScreenCaptureKit (macOS 13+). The first run asks for the
// Screen & System Audio Recording permission. Lumina's own audio is excluded.
//
// Build: swiftc -O -o lumina-audiotap audiotap.swift -framework ScreenCaptureKit -framework CoreMedia
import AVFoundation
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

let tap = Tap()
signal(SIGPIPE, SIG_IGN)
Task {
    do {
        try await tap.start()
    } catch {
        FileHandle.standardError.write("error: \(error.localizedDescription)\n".data(using: .utf8)!)
        exit(1)
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
