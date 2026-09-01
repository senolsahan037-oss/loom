# Sensei Custom Prompt Terminology Specification

This document defines the mapping of Ableton vocabulary, local/global musical terms, genres, and producer stylistic typologies to deterministic parameter profiles in the Sensei Local Agent.

---

## 1. Ableton Terminology

Standard Ableton concepts map directly to parameters in the local generation and assembly layers:
*   **`clip` / `clipslot`**: Represents the container slot where MIDI note data is written.
*   **`rack` / `drum rack`**: The target instrument device. Defines available pad configurations and choking.
*   **`pad`**: The physical key zone matching a MIDI note.
*   **`choke` / `choke group`**: Overlapping notes belonging to the same choke group cut each other off (e.g. open/closed hi-hats).
*   **`groove` / `swing`**: The micro-timing grid shift and velocity scaling derived from Ableton Groove Presets (.agr).

---

## 2. Genre Mappings

Determines the kick/snare offset maps and hi-hat subdivisions:
*   **`boom bap` / `hiphop` / `boombap` / `rap`**: Kick on 1 and 2.5; snare on 2 and 4.
*   **`trap`**: Kick on 1 with optional 2.75 syncopation; snare/clap on 3.
*   **`house` / `techno` / `dance`**: Four-on-the-floor kick pattern (1, 2, 3, 4); snare/clap on 2 and 4.
*   **`breakbeat` / `break`**: Syncopated break patterns.

---

## 3. Turkish & English Musical Command Mappings

Local and global musical terms map deterministically to parameter profiles:

| Command Term (Turkish) | Command Term (English) | Target Parameter / Mutation |
| :--- | :--- | :--- |
| `sakin`, `boşluklu`, `sade` | `sade`, `simplify`, `space` | **`complexity=low`**, **`reduce_density`** |
| `yoğun`, `sert`, `hareketli` | `complex`, `energy up` | **`complexity=complex`**, **`increase_density`** |
| `karanlık` | `dark` | **`energy=medium`**, **`complexity=medium`** |
| `atak ekle`, `dolgu` | `snare roll`, `fill` | **`fill_requested=True`** |
| `süsleme`, `hayalet nota` | `ghost note` | **`ghost_note_requested=True`** |
| `insan`, `swing`, `humanize` | `humanize`, `swing` | **`groove_requested=True`** |

---

## 4. Producer Stylistic Typologies

Stylistic profiles define deterministic baseline parameter profiles without style cloning:

### `j dilla` (Loose Swing Profile)
*   **`groove`**: `swing_loose` (loose micro-timing swing shifts)
*   **`timing`**: Late micro-adjustments on snare/hi-hats
*   **`velocity`**: High humanization range
*   **`genre`**: `boom_bap`

### `metro boomin` / `metro` (Dark Trap Profile)
*   **`genre`**: `trap`
*   **`energy`**: `high`
*   **`complexity`**: `complex`
*   **`sub_kick`**: `True` (uses sub kick layer)

### `dr dre` (Tight Boom Bap Profile)
*   **`genre`**: `boom_bap`
*   **`groove`**: `swing_tight` (aligned, tight micro-timing)
*   **`snare`**: Dry, high velocity
*   **`density`**: `medium`

### `daft punk` (Dance Groove Profile)
*   **`genre`**: `house`
*   **`groove`**: `swing_house`
*   **`energy`**: `high`
*   **`density`**: `high`

---

## 5. Remote Script Trigger Vocabulary

Vocabulary used to auto-configure clip slots and write MIDI notes:
*   **`BPM` / `hız` / `hiz`**: Trigger tempo matching (e.g. `95 bpm` maps to `bpm=95.0`).
*   **`bar` / `ölçü` / `olcu`**: Trigger loop length (e.g. `4 bar` maps to `bars=4`).
