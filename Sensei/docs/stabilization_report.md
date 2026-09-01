# Sensei Drum Stabilization Report

## 1. Current Architecture Summary

The Sensei Drum project consists of the following core modules:
-   **Library Scanner & Index Provider**:
    -   `ableton_index_provider.py` connects to the local SQLite database of Ableton Live (Live-files-*.db) to extract file metadata.
    -   `library_scanner.py` recursively indexes Ableton directories, mapping file tags, metadata, and packs.
    -   `library_protocol.py` merges database results with filesystem-scanned results, executing user search queries and resolving reference clips/kits.
-   **Dataset Manager**:
    -   `ableton_dataset.py` processes raw clips, detects scopes, classifies musical roles, energy, and complexity. It builds `ableton_dataset_index.json`.
-   **Rack Layout & Pad Role Resolver**:
    -   Analyzes Simpler rack structures, classifies pad roles into `drum_core`, `performance_pad`, or `unknown_pad`, and parses choke group parameters.
-   **Groove Presets Engine**:
    -   `groove_library.py` scans user/factory `.agr` files, decompresses XML-based presets, extracts timing/velocity offsets, and applies them non-destructively. Includes built-in swing fallbacks.
-   **Preview Engine**:
    -   `preview_builder.py` resolves kit contexts, extracts note events, applies grooves, and builds payload structures for client visualization.
-   **UI / Routing Layer**:
    -   `app.py` serves as a thin Flask API exposing `/scan-library`, `/build-preview`, `/apply-groove`, and UI HTML rendering.

---

## 2. Canonical Data Flows

-   **Library Scan & Query**:
    `scan_ableton_library` + `query_library_items` merge database results with local files.
-   **Dataset Query**:
    `AbletonDatasetManager.query_dataset` handles metadata query operations.
-   **Candidate Selection**:
    `AbletonDatasetManager.select_candidates` performs top, diverse, or random-seeded selection.
-   **Drum Safety**:
    Pads mapped using `pad_semantic_group`, filtering out `performance_pad` and `unknown_pad` roles, followed by `apply_choke_groups`.
-   **Preview Building**:
    `preview.preview_builder.generate_preview_data` processes the preview payload, incorporating resolved kit context and groove timing shifts.
-   **Groove Application**:
    `preview.groove_library.scan_grooves` + `preview_apply_groove` indexes `.agr` files and applies swing/humanization to note events.

---

## 3. Duplicate Logic Found & Actions Taken

-   **Kit Context Resolution & Profiling**:
    -   *Duplicate*: `app.py` `/build-preview` route previously resolved embedded kits and fallback kits manually, copying logic from `library_protocol.resolve_kit_context`.
    -   *Action*: Removed duplicate logic. Created `generate_preview_data` inside `preview_builder.py` that delegates to `resolve_kit_context` and handles the mapping cleanly.
-   **Diagnostics Generation**:
    -   *Duplicate*: `_preview_diagnostics` helper in `app.py` duplicated general matching calculations.
    -   *Action*: Moved to `preview_builder.py` as `_generate_diagnostics` and deleted the helper from `app.py`.
-   **Local Dataset Index Git Tracking**:
    -   *Issue*: The generated cache `ableton_dataset_index.json` was tracked by git.
    -   *Action*: Untracked the file using `git rm --cached` and ignored it in the root `.gitignore`.

---

## 4. Files Modified

1.  [preview_builder.py](file://~/Desktop/Loom/Sensei/preview/preview_builder.py): Added `generate_preview_data` and helper `_generate_diagnostics` functions.
2.  [app.py](file://~/Desktop/Loom/Sensei/app.py): Simplified the `/build-preview` endpoint and removed the `_preview_diagnostics` helper.
3.  [.gitignore](file://~/Desktop/Loom/.gitignore): Ignored the generated index `ableton_dataset_index.json`.

---

## 5. Verification Results

Ran `python3 -m pytest -q` from `~/Desktop/Loom`:
-   **56 passed, 1 skipped** in 9.80s.
-   All test categories (path stability, fallbacks, query/candidate selections, safety filters, grooves, and endpoints) passed successfully.

---

## 6. Remaining Risks
-   **Binary Groove Presets (`.agr`)**: We cannot natively parse note timings from binary-formatted `.agr` presets without reverse-engineering Ableton's proprietary serialization structure. This is protected by raising a `ValueError` rather than faking timing.

---

## 7. Recommended Next 5 Small Patches

1.  **Groove Pool Metadata Scanner**: Parse XML `.agr` files to extract time signature and loop details to show in the UI list.
2.  **Groove Library Search**: Add filter options to search grooves by category or swing percent.
3.  **Humanization Timing Scale**: Expose a timing scale configuration setting for random humanization offsets.
4.  **Diagnostics Breakdown UI**: Add a visual mapping table in the UI displaying matched vs unmatched notes.
5.  **MIDI File Fallback**: Support standard `.mid` / `.midi` files in groove templates using a midi reader library.
