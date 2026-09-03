# vendor/

Place the Ableton Extensions SDK packages here (not tracked — Ableton distributes
them through Centercode to beta members):

- `ableton-extensions-sdk-1.0.0-beta.1.tgz`
- `ableton-extensions-cli-1.0.0-beta.1.tgz`

Then `npm install`. Requires Node 24+ and the Live 12.4.x **beta** build for
Developer Mode (`npm start`). `npm run package` works without Live.

Developer Mode only connects when the beta is the **only** Live instance running;
quit any release Live first.

After stopping `npm start`, wait about a minute before starting it again —
Live keeps the dead host session for a while and a fast retry times out on the
control-channel handshake. Do not use `--verbose`: the beta.1 CLI never sees the
"connected" line in that mode and kills the host after 10 s.
