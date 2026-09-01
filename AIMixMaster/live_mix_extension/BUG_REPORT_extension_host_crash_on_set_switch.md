# Extension Host crashes with "Invalid object reference" when switching Live Sets

## Environment

- Live: 12.4.5 Beta (b10), macOS
- `@ableton-extensions/sdk`: 1.0.0-beta.0
- `@ableton-extensions/cli`: 1.0.0-beta.0
- Extension installed via `.ablx` (Preferences → Extensions → Choose file), not run via Developer Mode

## Summary

While an installed extension is active, opening/switching to a different Live Set crashes the Extension Host process with an uncaught exception inside the SDK's own internal document-change handling (`onFlipDocumentChanged`), not in extension code. The process exits (code 1) and does not restart automatically; Live has to be relaunched (or the extension reinstalled) to get the extension running again.

## Steps to reproduce

1. Install any extension that calls `initialize(activation, "1.0.0")` and does nothing further unusual (in our case: a minimal extension that polls a file every 250ms and reads `context.application.song.tracks` on a 1s timer — see minimal repro extension attached).
2. Confirm it's active (log line `"<name> extension active"` appears, or any successful read against `context.application.song`).
3. With a Live Set open, open a **different** Live Set (File → Open, or double-click a different `.als`) while the extension is still active.
4. Observe the Extension Host log.

## Expected

The Extension Host keeps running (or the extension is notified/reinitialized cleanly against the new Live Set).

## Actual

The Extension Host crashes immediately:

```
2026-08-12T02:31:51.710377: error: Uncaught exception (uncaughtException)
2026-08-12T02:31:51.712587: error: TypeError: Invalid object reference
    at Object.onFlipDocumentChanged (<anonymous>:34:30)
    at listOnTimeout (node:internal/timers:605:17)
    at process.processTimers (node:internal/timers:541:7)
2026-08-12T02:31:51.713197: info: Process is exiting with code: 1
```

`onFlipDocumentChanged` is internal SDK code, not something our extension registers or calls directly.

## Reproducibility

Observed twice in the same session, both times immediately following a Live Set switch while the extension was active (no other action in between). Log excerpt from both occurrences (log file: `~/Library/Preferences/Ableton/Live 12.4.5b10/ExtensionHost.txt`):

```
2026-08-12T02:18:14.593993: info: [AIMixMaster Live Mix]: AIMixMaster Live Mix extension active. Commands: ...
2026-08-12T02:18:33.381712: error: Uncaught exception (uncaughtException)
2026-08-12T02:18:33.387784: error: TypeError: Invalid object reference
    at Object.onFlipDocumentChanged (<anonymous>:34:30)
    ...
2026-08-12T02:18:33.388134: info: Process is exiting with code: 1

[Live relaunched, extension re-activated at 02:24:26]

2026-08-12T02:31:51.710377: error: Uncaught exception (uncaughtException)
2026-08-12T02:31:51.712587: error: TypeError: Invalid object reference
    at Object.onFlipDocumentChanged (<anonymous>:34:30)
    ...
2026-08-12T02:31:51.713197: info: Process is exiting with code: 1
```

## Impact

Any extension meant to stay active across a working session (not just a single Live Set) becomes unusable as soon as the user opens a different project — the host has to be manually recovered by relaunching Live. There's no visible in-app indication that this happened; it was only discovered by checking the Extension Host log file directly.

## Related prior fix

The 12.4.5b6 release notes mention: *"Fixed a regression introduced in Live 12.4.5b4 where the error 'The Ableton Extension Host stopped running' would be shown when loading non-default Templates or Sets."* This looks like the same family of issue (Extension Host instability tied to Set/Template loading), possibly not fully covering the "switch Live Sets while an extension is already active" path reported here. Tested against 12.4.5b10; not yet confirmed whether 12.4.5b11 (August 12, 2026) resolves it, as it isn't listed in that release's bugfixes.

## Suggested fix direction

Catch/guard the internal document-change handler against a stale/invalid object reference when the active Live Set changes, or explicitly tear down and reinitialize per-extension state around a Live Set switch instead of letting an internal reference outlive the document it pointed to.
