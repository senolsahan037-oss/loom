# Loom extension

The Ableton Extension that is Loom's only Live endpoint. Two things live here:

- **The bridge** (`src/bridge.ts`): a file queue under the extension's own
  storage directory that the Loom MCP writes requests into and reads outcomes
  from. Claim, expiry, session, replay journal, ownership ledger, structured
  outcome. Protocol `loom.bridge/3`. Everything Live-specific arrives through
  structural interfaces; `src/extension.ts` is the only file that touches SDK
  objects.
- **"Loom: Generate"** (right-click a Session clip slot or MIDI clip): runs
  the embedded Sensei runtime against the device evidence on the track and
  writes through the bridge's own `write_clip` -- same validation, same
  ownership ledger as a write the MCP asks for. A slot holding a clip Loom did
  not write is refused.

What the SDK cannot do (transport, meters, time signature, key write, preset
loading, recording) is refused with a named reason, never approximated. A
Drum Rack can be assembled from sample files (`build_drum_kit`: chain +
Simpler per pad), which is how a preset kit is rebuilt from its own samples;
`journal_import` carries an earlier extension id's replay journal over.

## Versions

`manifest.json` is the one place the package version is written; the build
fails if `package.json` differs and injects it into the bundle
(`surface_version: loom-extension/<version>`). `minimumApiVersion` must equal
`SDK_API_VERSION` in `bridge.ts`. The queue protocol (`BRIDGE_PROTOCOL`) is a
separate number: the MCP refuses to mutate through a bridge that publishes
another one.

## Build, test, package

```bash
npm install                 # needs the SDK tarballs in vendor/ (see vendor/README.md)
npm run test:bridge         # fake Live, real queue code, 100+ checks -- no SDK runtime needed
npx tsc --noEmit
npm run package             # dist/loom.ablx
```

Identity: manifest `name` "Loom" + `author` "SubverseLab" give the extension id `subverselab.loom` (Live derives it from those two fields); the storage and bridge live under `Extensions Data/subverselab.loom/`. `tests/fake_live_host.ts` runs this bridge as a separate process over a fake
set; `mcp_server/tests/test_bridge_consumer_real.py` drives the MCP against
it. `tools/measure_bridge.py` is the real-Live acceptance run.

Developer Mode (`npm start`) needs the Live beta to be the only Live running;
see `vendor/README.md` for the host quirks.
