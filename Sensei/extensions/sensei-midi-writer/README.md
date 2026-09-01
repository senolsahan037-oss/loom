# Sensei Generate (SDK bridge)

This is the official-SDK bridge for Sensei. It deliberately has no Remote Script dependency and no background queue.

## What it does

Right-click a Session View clip slot in Ableton Live and select **Sensei: Generate**. The extension runs the local Sensei Generate contract. It writes only when that contract returns `ready_to_write`; otherwise it shows the structured reason and changes nothing.

The extension invokes `tools/generate_cli.py` directly without a shell. The CLI
reads the verified project context, runs the fail-closed generate gate, and
returns a versioned report. The extension rejects malformed reports, blocked
reports, unauthorized writes, invalid notes, audio clips, and existing MIDI
clips that are shorter than the generated material.

The local project context is read from `current_project_context.json` in Live's per-extension **Extensions Data** directory. It is created only after Sensei has verified the active target, genre, bars and seed. There is intentionally no default active context.

The output payload remains:

```json
{
  "clip_length": 16,
  "notes": [
    { "pitch": 36, "time": 0, "duration": 0.25, "velocity": 110 },
    { "pitch": 38, "time": 1, "duration": 0.25, "velocity": 100 }
  ]
}
```

Sensei's verified groove catalog exports the same SDK-compatible note contract,
with optional provenance alongside it:

```json
{
  "schema_version": "sensei.sdk-midi-write.v1",
  "clip_length": 16,
  "notes": [{ "pitch": 36, "time": 0.285, "duration": 0.25, "velocity": 110 }],
  "provenance": { "groove_reference_id": "ableton-groove:…" }
}
```

`provenance` is audit data only. The extension accepts only `notes` and
`clip_length` as commands, and still validates every note before writing.

## Development

From this directory, install dependencies with `npm install`, enable **Developer Mode** in Live’s Extensions preferences, then run `npm start`. Copy `.env.example` to `.env` and set `EXTENSION_HOST_PATH` if Live is installed somewhere else.

`npm run package` produces `dist/sensei-midi-writer.ablx` for installation.

The manual **Sensei: Write MIDI JSON…** command is hidden by default. Set
`SENSEI_ENABLE_MANUAL_MIDI=1` in the Extension Host environment only when it is
needed for local development.

The local package paths expect the SDK distribution at `~/Downloads/extensions-sdk-1`. Replace them with published package versions when Ableton makes those available. The package contains the minimal Python generation runtime and release-pinned artifacts because Live's Extension Host intentionally cannot read arbitrary project directories.

## Safety boundary

The user explicitly invokes Generate on the destination slot. The extension has no filesystem queue, does not infer an instrument from a track name, and does not write outside the selected slot. Missing context, unresolved target, missing/changed release artifact, unsafe MIDI, or an unavailable runtime results in a report—not a write.
