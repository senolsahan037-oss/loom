import * as esbuild from "esbuild";
import * as fs from "node:fs";
import * as path from "node:path";
import * as zlib from "node:zlib";
import { fileURLToPath } from "node:url";

const manifest = JSON.parse(fs.readFileSync("manifest.json", "utf8"));
const production = process.argv.includes("--production");
const extensionRoot = path.dirname(fileURLToPath(import.meta.url));
const senseiRoot = path.resolve(extensionRoot, "../..");
const runtimeRoot = path.join(extensionRoot, "dist", "runtime");

fs.rmSync(runtimeRoot, { recursive: true, force: true });
for (const relativePath of [
  "tools/generate_cli.py",
  "core/__init__.py",
  "core/generate_gate.py",
  "core/live_context_resolver.py",
  "core/midi_runtime.py",
  "core/midi_variation_engine.py",
  "core/target_resolver.py",
  "ableton/__init__.py",
  "ableton/instrument_capabilities.py",
  "ableton/genre_identity.py",
  "ableton/inspector/__init__.py",
  "ableton/inspector/midi_reader.py",
]) {
  const destination = path.join(runtimeRoot, relativePath);
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.copyFileSync(path.join(senseiRoot, relativePath), destination);
}

// The measured genre evidence lives beside Sensei, not inside it. Without this
// block the embedded runtime imported mi/ through a try/except, got nothing,
// and reported "no measured evidence" for every section -- silently, in Live,
// while the source tree said otherwise. Only the small aggregate profiles
// travel; the per-song maps are regenerated, not shipped.
const intelligenceRoot = path.resolve(senseiRoot, "..", "MusicalIntelligence");
for (const relativePath of ["mi/__init__.py", "mi/profiles.py"]) {
  const destination = path.join(runtimeRoot, relativePath);
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.copyFileSync(path.join(intelligenceRoot, relativePath), destination);
}
for (const name of ["drum_patterns.json", "pop_harmony.json", "pop_bass.json"]) {
  const source = path.join(intelligenceRoot, "data", "corpus", name);
  if (!fs.existsSync(source)) throw new Error(`Evidence profile missing, run the MusicalIntelligence scans first: ${source}`);
  const destination = path.join(runtimeRoot, "data", "corpus", name);
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.copyFileSync(source, destination);
}

const releaseSource = path.join(senseiRoot, "data", "dataset_releases", "phase6", "dataset_release.manifest.json");
const release = JSON.parse(fs.readFileSync(releaseSource, "utf8"));
for (const artifact of Object.values(release.artifacts) as Array<{ path: string }>) {
  // The manifest records artifact paths relative to the data root (an
  // absolute path here is the build machine's and exists on no other disk).
  // Older manifests still carry absolute paths; both forms resolve.
  const source = path.isAbsolute(artifact.path) ? artifact.path : path.join(senseiRoot, "data", artifact.path);
  const relativePath = path.relative(path.join(senseiRoot, "data"), source);
  if (relativePath.startsWith("..") || path.isAbsolute(relativePath)) throw new Error(`Release artifact is outside data root: ${source}`);
  const destination = path.join(runtimeRoot, "data", relativePath);
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.copyFileSync(source, destination);
  artifact.path = relativePath;
}
const releaseDestination = path.join(runtimeRoot, "data", "dataset_releases", "phase6", "dataset_release.manifest.json");
fs.mkdirSync(path.dirname(releaseDestination), { recursive: true });
fs.writeFileSync(releaseDestination, JSON.stringify(release, null, 2) + "\n");

const embeddedRuntime: Record<string, string> = {};
function collectRuntime(directory: string) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) collectRuntime(fullPath);
    else embeddedRuntime[path.relative(runtimeRoot, fullPath)] = zlib.gzipSync(fs.readFileSync(fullPath), { level: 9 }).toString("base64");
  }
}
collectRuntime(runtimeRoot);

await esbuild.build({
  entryPoints: ["src/extension.ts"],
  outfile: manifest.entry,
  bundle: true,
  format: "cjs",
  platform: "node",
  sourcemap: !production,
  minify: production,
  plugins: [{
    name: "sensei-embedded-runtime",
    setup(build) {
      build.onResolve({ filter: /^sensei:runtime$/ }, () => ({ path: "sensei:runtime", namespace: "sensei-runtime" }));
      build.onLoad({ filter: /.*/, namespace: "sensei-runtime" }, () => ({ contents: `export default ${JSON.stringify(embeddedRuntime)};`, loader: "js" }));
    },
  }],
  loader: { ".html": "text" },
});
