# vendor/

Place the Ableton Extensions SDK packages here (not tracked — Ableton distributes
them through Centercode to beta members):

- `ableton-extensions-sdk-1.0.0-beta.1.tgz`
- `ableton-extensions-cli-1.0.0-beta.1.tgz`

Then `npm install`. Requires Node 24+ and the Live 12.4.x **beta** build for
Developer Mode (`npm start`). `npm run package` works without Live.

Developer Mode only connects when the beta is the **only** Live instance running;
quit any release Live first.
