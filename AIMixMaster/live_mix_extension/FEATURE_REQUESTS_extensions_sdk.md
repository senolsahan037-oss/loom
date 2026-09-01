# Extensions SDK capability gaps — feature requests (not bugs)

Collected organically while building `live_mix_extension` (a live AI-copiloted mixing assistant) against `@ableton-extensions/sdk` 1.0.0-beta.0. Ableton has indicated these should be submitted as feature suggestions rather than bug reports, and that an improved SDK is coming soon — this list is held pending that release; re-check each item against the new SDK before submitting anything still missing.

## 1. Routing is not exposed at all

`Track` (and `AudioTrack`/`MidiTrack`) expose `name`, `mute`, `solo`, `arm`, `devices`, `mixer`, `clipSlots`, etc., but nothing about audio/MIDI input or output routing, or which track/device feeds a device's sidechain input. We confirmed this concretely: a Glue Compressor's `S/C On`, `S/C Gain`, etc. are readable as normal `DeviceParameter`s, but *which track* is feeding the sidechain (visible in Live's UI as a "Pre FX / Post FX / Post Mixer" chooser on a routing source) is not.

**Request:** Expose routing state (input/output routing type + channel, sidechain source track + tap point) as readable/writable properties on `Track`, and sidechain source as a property on devices that have one.

## 2. No live audio metering

`DeviceParameter.getValue()` reads a device's control-value parameters, but there is no way to read a track or device's actual audio level (peak/RMS/gain-reduction) in real time. This makes true gain-staging and dynamics-processing verification (e.g. "is this compressor actually reducing ~3dB right now") impossible without visually watching Live's own meters.

**Request:** A live-metering API — peak/RMS per track (and ideally gain-reduction per dynamics device) as a readable, continuously-updating value or subscribable stream.

## 3. No human-readable parameter display string

`DeviceParameter` returns only a raw internal numeric value (`min`/`max`/`value`, typically normalized 0–1 for non-linear controls like frequency). There's no equivalent of the classic Live API's parameter display string (e.g. `"1.2 kHz"`, `"-6.0 dB"`). For EQ Eight's per-band Frequency parameters specifically, we had to fall back to an unverified, community-sourced logarithmic conversion formula (`freq_hz = 20 * 2^(10 * normalized_value)`) and ask the human to visually confirm the resulting Hz against Live's own UI, since there's no way to read Live's own formatted value.

**Request:** Expose each `DeviceParameter`'s display string (whatever Live itself would show in its UI) alongside the raw value.

## 4. No true post-processing audio render

`context.resources.renderPreFxAudio` is explicitly pre-effects only (confirmed via the SDK's own API docs, and its 12.4.5b11 bugfix note about normalization only reinforces this framing). There's no way to render/measure a track's actual output signal (post-devices, post-fader, post any parallel/sidechain interaction) — which is what's actually needed for gain-staging measurement, since a track's audible loudness is determined by its full processing chain, not its raw source audio.

**Request:** A `renderPostFxAudio` (or `renderTrackOutput`) equivalent, capturing the track's real output signal.

---
Context: Ableton has stated (2026-08-12) that Developer Mode is not currently functioning correctly on their end (confirming our own repeated `extensions-cli run` handshake-timeout troubleshooting was not a local misconfiguration), and that an improved SDK release is coming. Hold this list until that release, verify each item is still missing, then submit.
