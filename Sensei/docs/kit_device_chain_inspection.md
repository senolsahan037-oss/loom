# Sensei Drum Kit Device Chain Inspection Report

This report documents our analysis of the XML structure inside Ableton Drum Racks (`.adg` presets and `.alc` clip tracks). It outlines the readable device parameters, safety classifications, and recommends a roadmap for verifying MIDI writing compatibility.

---

## 1. Available XML Fields & Structure

Ableton Drum Racks display pad settings and device parameters inside individual branch elements.

### For ADG Presets (`.adg`)
-   **Chains**: Represented by `<DrumBranchPreset>` tags.
-   **Device Chain**:
    `DrumBranchPreset` -> `DevicePresets` -> `AbletonDevicePreset` -> `Device` -> device element tags (e.g., `<OriginalSimpler>`, `<MultiSampler>`, `<StereoGain>`).
-   **Pads & Routing**:
    -   `<ReceivingNote Value="note_num"/>` (pad input MIDI note)
    -   `<SendingNote Value="note_num"/>` (MIDI note sent to internal instrument)
    -   `<ChokeGroup Value="choke_id"/>` (choke group ID, `0` if none)
    -   `<Name Value="label"/>` or `<UserName Value="label"/>` (pad labels)

### For ALC Clip Tracks (`.alc` / Embedded Kits)
-   **Chains**: Represented by `<DrumBranch>` tags.
-   **Device Chain**:
    `DrumBranch` -> `DeviceChain` -> `MidiToAudioDeviceChain` -> `Devices` -> device element tags (e.g., `<OriginalSimpler>`, `<MultiSampler>`, `<StereoGain>`).
-   **Pads & Routing**: Same structure as presets, nested inside the branch layout.

---

## 2. Parsed Device & Chain Data

Based on our XML structural analysis, the following device characteristics can be reliably extracted:

1.  **Simpler Presence**: Check for the tag `<OriginalSimpler>` in the chain.
2.  **Sampler Presence**: Check for the tag `<MultiSampler>` in the chain.
3.  **Audio Effects**: Any devices nested in the chain after the sample playback device (e.g. `<StereoGain>`, `<Saturator>`, `<Limiter>`, `<Eq8>`, `<Compressor>`, `<AutoFilter>`).
4.  **Nested Racks (Instrument Racks)**: Mapped when `<InstrumentGroupDevice>` is found inside the chain, representing layered sound engines.
5.  **Velocity & Key Zones (Sampler)**:
    -   Key range limits parsed from `<KeyRangeMin Value="x"/>` and `<KeyRangeMax Value="x"/>`.
    -   Velocity range limits parsed from `<VelocityRangeMin Value="y"/>` and `<VelocityRangeMax Value="y"/>`.
6.  **Macro Controls**:
    -   Count visible macro dials from `<NumVisibleMacroControls Value="x"/>`.
    -   Display names and configured values parsed from `<MacroDisplayNames.0>` to `<MacroDisplayNames.15>`.

---

## 3. Write Safety Classification Rules

To determine if a kit is safe for MIDI writing, we define three levels of write safety:

-   **`safe`**: Valid core drum pads (`drum_core` semantic group with labels matching Kick, Snare, Hihat, etc.) are present and readable, and device chain complexity is low or medium (no nested Instrument Racks).
-   **`caution`**: The device chain contains nested Instrument Racks (`InstrumentGroupDevice`) or multiple audio effects (medium/high complexity), or there are more performance/unknown pads than core drum pads.
-   **`unsafe`**: Core drum pads representing essential roles (like Kick and Snare) are missing, or device chains cannot be parsed correctly.

---

## 4. What Should Not Be Touched Yet

-   **Do not generate or write devices**: Rebuilding or creating Simpler/Sampler device chains inside `.adg` or `.alc` XML is highly complex due to proprietary class checksums and nested parameters. We should not attempt to write device configs.
-   **Do not modify Ableton preset files directly**: Modifying binary preset XML packages risks corrupting preset signatures.

---

## 5. Recommended Next Patch: "Kit Device Chain Metadata Enrichment" (v1)

We recommend adding a helper function `inspect_kit_device_chains(path: Path) -> dict` in `ableton/inspector/profile_exporter.py` that computes:
-   `has_sampler` (bool)
-   `has_simpler` (bool)
-   `effect_count` (int)
-   `macro_count` (int)
-   `chain_complexity` ("low" | "medium" | "high")
-   `kit_write_safety` ("safe" | "caution" | "unsafe")
-   `device_chain_summary` (dict mapping pad notes to their device tags and parameters)

This metadata will enrich `inspect_alc_embedded_kit` and `build_kit_profile` outputs, giving the AI Producer client clear boundaries on whether the selected Ableton track or preset is safe for writing MIDI.
