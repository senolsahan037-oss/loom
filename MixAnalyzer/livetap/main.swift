// livetap -- record what one process (Ableton Live) is sending to the audio
// output, straight from Core Audio, into a WAV file. No virtual device, no
// routing change in Live, no render: macOS 14.2+ process taps
// (AudioHardwareCreateProcessTap) mirror the process's output stream.
//
//   livetap --pid 12345 --seconds 8 --out capture.wav [--rate 48000]
//
// The first run asks macOS for the "System Audio Recording" permission for the
// calling application (System Settings > Privacy & Security > Screen & System
// Audio Recording); until it is granted the tap delivers silence and this tool
// says so on stderr and exits 3.
import AVFoundation
import CoreAudio
import Foundation

struct Args {
    var pid: pid_t = 0
    var seconds: Double = 8
    var out = "capture.wav"
    var rate: Double = 48_000
}

func parse() -> Args {
    var a = Args()
    var it = CommandLine.arguments.dropFirst().makeIterator()
    while let flag = it.next() {
        switch flag {
        case "--pid": a.pid = pid_t(Int32(it.next() ?? "0") ?? 0)
        case "--seconds": a.seconds = Double(it.next() ?? "8") ?? 8
        case "--out": a.out = it.next() ?? a.out
        case "--rate": a.rate = Double(it.next() ?? "48000") ?? 48_000
        default:
            FileHandle.standardError.write("unknown flag \(flag)\n".data(using: .utf8)!)
            exit(2)
        }
    }
    if a.pid == 0 {
        FileHandle.standardError.write("--pid is required\n".data(using: .utf8)!)
        exit(2)
    }
    return a
}

func check(_ status: OSStatus, _ what: String) {
    if status != noErr {
        FileHandle.standardError.write("\(what) failed: OSStatus \(status)\n".data(using: .utf8)!)
        exit(1)
    }
}

// Translate a Unix pid into the AudioObjectID Core Audio uses for processes.
func audioProcessObject(for pid: pid_t) -> AudioObjectID {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyTranslatePIDToProcessObject,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var input = pid
    var object = AudioObjectID(kAudioObjectUnknown)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    let status = withUnsafePointer(to: &input) { ptr in
        AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address,
                                   UInt32(MemoryLayout<pid_t>.size), ptr, &size, &object)
    }
    check(status, "translate pid \(pid) to process object")
    if object == kAudioObjectUnknown {
        FileHandle.standardError.write("pid \(pid) has no audio process object (is it playing audio?)\n".data(using: .utf8)!)
        exit(1)
    }
    return object
}

let args = parse()
let processObject = audioProcessObject(for: args.pid)

// 1) The tap: stereo mixdown of everything this process outputs, muted=false
//    so the user keeps hearing Live.
let description = CATapDescription(stereoMixdownOfProcesses: [processObject])
description.name = "loom-livetap"
description.muteBehavior = .unmuted
description.isPrivate = true
var tapID = AudioObjectID(kAudioObjectUnknown)
check(AudioHardwareCreateProcessTap(description, &tapID), "create process tap")

// 2) An aggregate device that contains only the tap, so we can run an IO proc on it.
let aggregateDescription: [String: Any] = [
    kAudioAggregateDeviceNameKey: "loom-livetap-aggregate",
    kAudioAggregateDeviceUIDKey: "com.subverselab.loom.livetap.\(getpid())",
    kAudioAggregateDeviceIsPrivateKey: true,
    kAudioAggregateDeviceTapAutoStartKey: true,
    kAudioAggregateDeviceTapListKey: [[
        kAudioSubTapUIDKey: description.uuid.uuidString,
        kAudioSubTapDriftCompensationKey: true,
    ]],
]
var aggregateID = AudioObjectID(kAudioObjectUnknown)
check(AudioHardwareCreateAggregateDevice(aggregateDescription as CFDictionary, &aggregateID), "create aggregate device")

// 3) The tap's own stream format tells us channels and sample rate.
var formatAddress = AudioObjectPropertyAddress(
    mSelector: kAudioTapPropertyFormat,
    mScope: kAudioObjectPropertyScopeGlobal,
    mElement: kAudioObjectPropertyElementMain)
var tapFormat = AudioStreamBasicDescription()
var formatSize = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
check(AudioObjectGetPropertyData(tapID, &formatAddress, 0, nil, &formatSize, &tapFormat), "read tap format")
let channels = Int(tapFormat.mChannelsPerFrame)
let sampleRate = tapFormat.mSampleRate
guard let inFormat = AVAudioFormat(streamDescription: &tapFormat) else {
    FileHandle.standardError.write("unsupported tap format\n".data(using: .utf8)!)
    exit(1)
}

// 4) Output file: float32 WAV at the tap's rate (no resampling, no gain).
let url = URL(fileURLWithPath: args.out)
let outSettings: [String: Any] = [
    AVFormatIDKey: kAudioFormatLinearPCM,
    AVSampleRateKey: sampleRate,
    AVNumberOfChannelsKey: channels,
    AVLinearPCMBitDepthKey: 32,
    AVLinearPCMIsFloatKey: true,
    AVLinearPCMIsNonInterleaved: !inFormat.isInterleaved,
]
let file: AVAudioFile
do {
    file = try AVAudioFile(forWriting: url, settings: outSettings, commonFormat: .pcmFormatFloat32, interleaved: inFormat.isInterleaved)
} catch {
    FileHandle.standardError.write("cannot open \(args.out): \(error)\n".data(using: .utf8)!)
    exit(1)
}

var framesWritten: Int64 = 0
var peak: Float = 0
let wanted = Int64(args.seconds * sampleRate)
let done = DispatchSemaphore(value: 0)
var procID: AudioDeviceIOProcID?

let ioBlock: AudioDeviceIOBlock = { _, inData, _, _, _ in
    let bufferList = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: inData))
    guard let first = bufferList.first, first.mDataByteSize > 0 else { return }
    let frames = AVAudioFrameCount(Int(first.mDataByteSize) / Int(tapFormat.mBytesPerFrame))
    guard let pcm = AVAudioPCMBuffer(pcmFormat: inFormat, bufferListNoCopy: inData, deallocator: nil) else { return }
    pcm.frameLength = frames
    if let ch = pcm.floatChannelData {
        for c in 0..<Int(pcm.format.channelCount) {
            let n = Int(pcm.frameLength)
            for i in 0..<n { let v = abs(ch[c][i]); if v > peak { peak = v } }
        }
    }
    do { try file.write(from: pcm) } catch { return }
    framesWritten += Int64(frames)
    if framesWritten >= wanted { done.signal() }
}
check(AudioDeviceCreateIOProcIDWithBlock(&procID, aggregateID, nil, ioBlock), "create IO proc")
check(AudioDeviceStart(aggregateID, procID), "start aggregate device")

let deadline = DispatchTime.now() + args.seconds + 5
_ = done.wait(timeout: deadline)

AudioDeviceStop(aggregateID, procID)
if let procID = procID { AudioDeviceDestroyIOProcID(aggregateID, procID) }
AudioHardwareDestroyAggregateDevice(aggregateID)
AudioHardwareDestroyProcessTap(tapID)

let seconds = Double(framesWritten) / sampleRate
let report: [String: Any] = [
    "path": args.out, "pid": Int(args.pid), "sample_rate": sampleRate, "channels": channels,
    "frames": framesWritten, "seconds": seconds, "peak": peak,
    "permission_hint": peak == 0 ? "silence captured: either nothing played or System Audio Recording permission is not granted for this app" : "",
]
if let data = try? JSONSerialization.data(withJSONObject: report), let text = String(data: data, encoding: .utf8) {
    print(text)
}
if framesWritten == 0 {
    FileHandle.standardError.write("no frames captured\n".data(using: .utf8)!)
    exit(3)
}
