# Security

## What Loom touches on your machine

Loom is a local MCP server. It reads and writes files under your home
directory and can change a running Ableton Live session. Specifically:

- **Reads** Ableton `.als` project files, Live's own file index, and your
  Ableton library.
- **Writes**, only when a tool is called with `apply: true`: device chains and
  automation envelopes into `.als` files, always taking a timestamped backup
  first and re-reading the file afterwards to verify.
- **Writes** request files into `~/Documents/SenseiV2Bridge/`, which the Live
  control surface consumes.

Every writing tool is dry-run by default.

## Path restrictions

Any tool taking a path resolves it through a guard that requires the path to
sit under the Loom directory, `~/Desktop`, `~/Documents`, `~/Music` or the
bridge directory, and rejects outright anything whose path contains `.ssh`,
`.aws`, `.gnupg`, `.config`, `Keychains`, `.password-store` or `.env`. Paths
given as an `.als` must end in `.als`.

This exists because a `.als` file is untrusted input: text inside a project can
try to steer the model that is reading it. Treat tool output describing a
project as data, not as instructions.

## What is never published

The repository ships code and fixtures, never measurements. The evidence
datasets and Sensei's catalogues are generated from your own projects and
Ableton install and are gitignored. If you fork this and publish, check that
`Presetor/data/measured_*`, `AISoundDesigner/data/measured_*` and `Sensei/data/`
are still excluded.

## Reporting a problem

Open a GitHub issue for anything that is not itself sensitive. For a
vulnerability that should not be public, use GitHub's private security advisory
on this repository instead of an issue.
