# Sensei Shared Dataset Pool & Folder Architecture Plan

This plan documents the transition of the Sensei project from a drum-specific utility into a modular **Musical Decision Platform** (supporting Drum, Bass, Chord, Lead, Melody, Flow, Texture, FX, and Orchestration modules).

---

## 1. Directory Structure

The future repository tree is structured as follows:

```
Sensei/
  core/                       # Shared Musical Decision Engine Platform
    evidence/                 # Stores raw analyzer facts (AnalysisCandidate)
    resolver/                 # Resolves candidate predictions into SSoT (CanonicalResult)
    knowledge/                # Scales, chord voicing, groove templates, profiles
    decision/                 # Abstract plan generation (arrangement and variations)
    assembly/                 # Converts plans into note/control streams (grid offsets)
    execution/                # MIDI writing, Max for Live bridges, preview outputs
    contracts/                # Standard interface definition classes (typing / protocols)

  dataset/                    # Shared Index & Enrichment Parsers
    shared/                   # 1. CORE DATASET INDEX OWNER
      core_indexer.py         # Rebuilds and writes the authoritative index
      shared_metadata.py      # Authoritative metadata extraction (tempo, loop range)
      database_manager.py     # SQLite / JSON index read/write interface
    enrichment/               # 2. OPTIONAL METADATA ENRICHERS (No ownership of index)
      drum/                   # Drum pad semantic groups, choke tags
      bass/                   # Scales, slide/legato ranges
      chord/                  # Chord progression and voicing tags
      lead/                   # Melodic intervals, motif tags
      melody/                 # Monophonic hook identifiers
      flow/                   # Energy/complexity metrics

  generators/                 # INSTRUMENT PATTERN GENERATORS (Read-only views)
    drum/                     # Formulates same-kit drum patterns
    bass/                     # Formulates bass lines
    chord/                    # Formulates chord pads
    lead/                     # Formulates melody leads
    melody/                   # Formulates main hooks
    flow/                     # Formulates macro rhythm patterns
    texture/                  # Formulates ambient pads and noise lines
    fx/                       # Formulates sweeps and downlifters

  orchestration/              # ARRANGEMENT & PROCESSING PLATFORM
    arrangement/              # Owns target DAW track slots and MIDI write contexts
    mixmaster/                # Owns dynamics, track levels, and master processing

  ableton/                    # Low-level Ableton Clip & Preset Parsers
    inspector/                # .alc (clip), .adg (preset) and .als (set) XML/binary inspect loops
    library_scanner.py        # Ableton directory rglob file scanner
    library_protocol.py       # Candidate search and lookup protocol

  tests/                      # Comprehensive Unit & Integration Test Suites
```

---

## 2. Component Ownership & Isolation Boundaries

To maintain long-term stability and clean separation of concerns, the following boundaries are strictly enforced:

### 1. Dataset Index Ownership
-   `dataset/shared` **owns the index**. It is the only component allowed to create, write, or rebuild `ableton_dataset_index.json`.
-   **No generator** or enricher is permitted to own, create, or trigger a rebuild of the database index.

### 2. Optional Enrichment Layer
-   `dataset/enrichment/*` files are **optional metadata enrichers**. They run after the shared indexer has cataloged a file, adding auxiliary attributes (like choke assignments or harmonic properties) to the shared record. 
-   If an enrichment module is missing or fails, the core record remains completely valid and usable.

### 3. Read-Only Generators
-   `generators/*` are **consumers only**. They receive filtered views of the canonical dataset (e.g., `generators/bass` gets key/scale filtered clip lists).
-   Generators have no direct write access to database indexes, Ableton files, or track slots.

### 4. Orchestration Boundaries
-   `orchestration/arrangement` **owns DAW target slots and write context**. It tracks which MIDI clip goes to which track/channel, orchestrating the timeline.
-   `orchestration/mixmaster` is responsible for **processing and dynamics** (automating gains, panning, and master effects). It is classified under orchestration/processing and does *not* generate note patterns.
