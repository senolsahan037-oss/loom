# Sensei Shared Dataset Root

`DatasetRoot` is the shared, versioned data boundary for Loom devices and
generators. Sensei is the first production consumer; ArrangementGPS is retained
as a legacy collection. Future drum, bass, chord, melody, arrangement and mix
devices register here and consume the same contracts.

```text
dataset sources -> locked release -> read-only collection views -> generators
```

## Ownership rules

- Dataset builders may create a new release.
- A released artifact is immutable and content-hashed.
- Generators and devices are read-only consumers.
- Source-native evidence and derived metadata must remain separate.
- Unknown or ambiguous evidence must not be replaced with guesses.
- DAW writes and target selection stay outside the dataset layer.

## Layout

```text
schemas/               Shared JSON contracts
registry/              Generator and collection registry
releases/              Versioned, immutable release manifests
dataset/reader/        Read-only Python consumer API
tools/                 Validation commands
api_generated/         Legacy generated examples
taxonomy/              Shared controlled vocabulary
```

The active release is selected by `releases/current.json`. The first shared
release exposes Sensei Phase 6 artifacts without copying or mutating them. This
keeps the already-verified SHA-256 identities intact while migration into a
self-contained artifact store remains possible.

## Validate

From `DatasetRoot`:

```bash
python3 tools/validate_release.py
python3 -m unittest discover -s tests -v
```

## Consume

```python
from dataset.reader import SharedDataset

dataset = SharedDataset.open_current()
clips = dataset.iter_jsonl("sensei.canonical_midi")
```

Consumers should request a collection by its stable ID. They must not depend on
physical file paths or rebuild indexes during generation.
