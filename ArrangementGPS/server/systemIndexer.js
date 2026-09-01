import { existsSync, mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_SYSTEM_INDEX_PATH = join(
  homedir(),
  "Library",
  "Application Support",
  "ArrangementGPS",
  "system_index.json"
);

const DEFAULT_SCAN_ROOTS = [
  join(homedir(), "Documents"),
  join(homedir(), "Music"),
  join(homedir(), "Library", "Application Support"),
  join(homedir(), "Library", "Audio"),
  join(homedir(), "Library", "Application Support", "Ableton"),
  "/Applications/Ableton Live 12 Standard.app/Contents/App-Resources/Core Library",
  "/Library/Application Support",
  "/Library/Audio",
  "/Library/Audio/Plug-Ins",
  join(homedir(), "Library", "Audio", "Plug-Ins"),
];

const ABLETON_EXTENSIONS = new Set([".adg", ".adv", ".alc", ".amxd"]);
const SAMPLE_EXTENSIONS = new Set([".wav", ".aiff", ".aif", ".mp3", ".flac", ".rex", ".rx2"]);
const PLUGIN_EXTENSIONS = new Set([".clap", ".vst3", ".component", ".vst"]);
const PLUGIN_BANK_EXTENSIONS = new Set([
  ".vital",
  ".vitalbank",
  ".fxp",
  ".fxb",
  ".srgpreset",
  ".scl",
  ".tun",
  ".syx",
  ".pjunoxl",
  ".tunobank",
  ".talpreset",
  ".h2p",
  ".h2pbank",
]);
const PLUGIN_BANK_FOLDER_HINTS = [
  "vital",
  "surge",
  "dexed",
  "tal-",
  "tal ",
  "u-he",
  "uhe",
  "zebra",
  "preset",
  "presets",
  "soundbank",
  "soundbanks",
  "patches",
];
const CLAP_INSTRUMENTS = new Set([
  "vital",
  "surge xt",
  "dexed",
  "tal-j-8",
  "tal-noisemaker",
  "tal-u-no-lx-v2",
  "tyrelln6",
  "zebra2",
  "zebrahz",
  "tal-bassline-101",
]);
const CLAP_DRUM_INSTRUMENTS = new Set(["tal-drum"]);
const CLAP_EFFECTS = new Set([
  "surge xt effects",
  "tal-chorus-lx",
  "tal-filter-2",
  "tal-reverb-4",
]);
const IGNORED_DIRS = new Set([
  "node_modules",
  "dist",
  ".git",
  "cache",
  "caches",
  "__pycache__",
  ".cache",
]);

function normalize(value) {
  return String(value ?? "").toLowerCase();
}

function getSystemIndexPath() {
  return process.env.ARRANGEMENTGPS_SYSTEM_INDEX || DEFAULT_SYSTEM_INDEX_PATH;
}

function getType(extension) {
  return {
    ".adg": "rack",
    ".adv": "preset",
    ".alc": "clip",
    ".amxd": "max_device",
    ".wav": "sample",
    ".aiff": "sample",
    ".aif": "sample",
    ".mp3": "sample",
    ".flac": "sample",
    ".rex": "rex_loop",
    ".rx2": "rex_loop",
    ".clap": "clap_plugin",
    ".vst3": "vst3_plugin",
    ".component": "au_component",
    ".vst": "vst_plugin",
    ".vital": "plugin_preset",
    ".vitalbank": "plugin_bank",
    ".fxp": "plugin_preset",
    ".fxb": "plugin_bank",
    ".srgpreset": "plugin_preset",
    ".scl": "tuning_file",
    ".tun": "tuning_file",
    ".syx": "plugin_bank",
    ".pjunoxl": "plugin_preset",
    ".tunobank": "plugin_bank",
    ".talpreset": "plugin_preset",
    ".h2p": "plugin_preset",
    ".h2pbank": "plugin_bank",
  }[extension] ?? "unknown";
}

function getSource(path) {
  if (path.includes("Core Library")) return "Core Library";
  if (path.includes("Plug-Ins")) return "Plugin Folder";
  if (path.includes("Packs")) return "Ableton Pack";
  if (path.includes("User Library")) return "User Library";
  return "User";
}

function normalizePluginName(name) {
  return normalize(name)
    .replace(/\s+/g, " ")
    .replace(/_/g, "-")
    .trim();
}

function getKnownClapClass(name, extension) {
  if (extension !== ".clap") return null;

  const normalizedName = normalizePluginName(name);

  if (CLAP_DRUM_INSTRUMENTS.has(normalizedName)) return "plugin_drum_instrument";
  if (CLAP_INSTRUMENTS.has(normalizedName)) return "plugin_instrument";
  if (CLAP_EFFECTS.has(normalizedName)) return "plugin_effect";

  return "plugin";
}

function guessPluginName(name, path, extension) {
  const haystack = normalize(`${name} ${path}`);

  if (extension === ".vital" || extension === ".vitalbank" || haystack.includes("vital")) return "Vital";
  if (extension === ".srgpreset" || haystack.includes("surge")) return "Surge XT";
  if (extension === ".syx" || haystack.includes("dexed")) return "Dexed";
  if (extension === ".pjunoxl" || extension === ".tunobank" || extension === ".talpreset" || haystack.includes("tal-")) {
    if (haystack.includes("bassline")) return "TAL-BassLine-101";
    if (haystack.includes("j-8")) return "TAL-J-8";
    if (haystack.includes("u-no")) return "TAL-U-NO-LX-V2";
    if (haystack.includes("drum")) return "TAL-Drum";
    if (haystack.includes("reverb")) return "TAL-Reverb-4";
    if (haystack.includes("chorus")) return "TAL-Chorus-LX";
    if (haystack.includes("filter")) return "TAL-Filter-2";
    return "TAL";
  }
  if (extension === ".h2p" || extension === ".h2pbank" || haystack.includes("u-he") || haystack.includes("zebra")) {
    if (haystack.includes("zebrahz")) return "ZebraHZ";
    if (haystack.includes("zebra2")) return "Zebra2";
    return "u-he";
  }
  if (extension === ".fxp" || extension === ".fxb") return "VST Preset";
  if (extension === ".scl" || extension === ".tun") return "Tuning";
  return "Unknown Plugin";
}

function guessPluginBankCategory(pluginName, name, path) {
  const haystack = normalize(`${pluginName} ${name} ${path}`);

  if (haystack.includes("drum")) return "drums";
  if (haystack.includes("bass") || haystack.includes("sub")) return "bass";
  if (haystack.includes("reverb") || haystack.includes("delay") || haystack.includes("chorus") || haystack.includes("filter") || haystack.includes("fx")) return "fx";
  if (haystack.includes("pad") || haystack.includes("keys") || haystack.includes("chord") || haystack.includes("piano")) return "harmony";
  if (haystack.includes("lead") || haystack.includes("pluck") || haystack.includes("arp") || haystack.includes("melody")) return "melody";
  if (["Vital", "Surge XT", "Dexed", "TAL-J-8", "TAL-NoiseMaker", "TAL-U-NO-LX-V2", "TyrellN6", "Zebra2", "ZebraHZ"].includes(pluginName)) return "melody";
  if (pluginName === "TAL-BassLine-101") return "bass";
  if (pluginName === "TAL-Drum") return "drums";
  if (["Surge XT Effects", "TAL-Chorus-LX", "TAL-Filter-2", "TAL-Reverb-4"].includes(pluginName)) return "fx";
  return "general";
}

function guessCategory(name, path, extension) {
  const haystack = normalize(`${name} ${path}`);
  const knownClapClass = getKnownClapClass(name, extension);

  if (knownClapClass) return knownClapClass;
  if (PLUGIN_EXTENSIONS.has(extension)) {
    if (haystack.includes("drum")) return "plugin_drum_instrument";
    if (haystack.includes("reverb") || haystack.includes("delay") || haystack.includes("chorus") || haystack.includes("filter") || haystack.includes("effect")) {
      return "plugin_effect";
    }
    return "plugin_instrument";
  }
  if (SAMPLE_EXTENSIONS.has(extension)) return "sample";
  if (haystack.includes("drum") || haystack.includes("kit") || haystack.includes("percussion")) return "drums";
  if (haystack.includes("bass") || haystack.includes("sub") || haystack.includes("808")) return "bass";
  if (haystack.includes("reverb") || haystack.includes("delay") || haystack.includes("echo")) return "audio_fx";
  if (haystack.includes("midi effect")) return "midi_fx";
  if (haystack.includes("pad") || haystack.includes("keys") || haystack.includes("piano") || haystack.includes("rhodes")) return "instrument";
  if (haystack.includes("lead") || haystack.includes("pluck") || haystack.includes("melody")) return "melodic";
  return "unknown";
}

function guessFamily(name, path) {
  const haystack = normalize(`${name} ${path}`);

  if (haystack.includes("surge xt effects") || haystack.includes("tal-chorus") || haystack.includes("tal-filter") || haystack.includes("tal-reverb")) return "fx";
  if (haystack.includes("tal-drum")) return "drums";
  if (haystack.includes("tal-bassline")) return "bass";
  if (haystack.includes("vital") || haystack.includes("surge xt") || haystack.includes("dexed") || haystack.includes("zebra") || haystack.includes("tyrell") || haystack.includes("tal-j-8") || haystack.includes("tal-noisemaker") || haystack.includes("tal-u-no")) return "melody";
  if (haystack.includes("drum") || haystack.includes("kit")) return "drums";
  if (haystack.includes("bass") || haystack.includes("sub")) return "bass";
  if (haystack.includes("pad") || haystack.includes("keys") || haystack.includes("piano") || haystack.includes("rhodes")) return "harmony";
  if (haystack.includes("lead") || haystack.includes("pluck") || haystack.includes("arp")) return "melody";
  if (haystack.includes("reverb") || haystack.includes("delay") || haystack.includes("saturat") || haystack.includes("echo")) return "fx";
  if (haystack.includes("vocal")) return "vocal";
  return "general";
}

function getCharacterKeywords(name, path) {
  const haystack = normalize(`${name} ${path}`);
  const keywords = [
    "warm",
    "dark",
    "bright",
    "dusty",
    "analog",
    "cinematic",
    "wide",
    "deep",
    "soft",
    "hard",
    "vintage",
    "lofi",
    "ambient",
    "acoustic",
    "electric",
    "saturated",
    "clean",
  ];

  return keywords.filter((keyword) => haystack.includes(keyword));
}

function createItem(path, extension) {
  const name = basename(path, extension);
  const category = guessCategory(name, path, extension);
  const source = getSource(path);

  return {
    name,
    path,
    extension,
    type: getType(extension),
    category,
    source,
    tags: [category, source, extension === ".clap" ? "clap" : ""].filter(Boolean),
    guessed_family: guessFamily(name, path),
    character_keywords: getCharacterKeywords(name, path),
  };
}

function createPluginBankItem(path, extension) {
  const isFolder = extension === "folder";
  const presetName = isFolder ? basename(path) : basename(path, extension);
  const pluginName = guessPluginName(presetName, path, extension);

  return {
    plugin_name: pluginName,
    preset_name: presetName,
    path,
    extension,
    type: isFolder ? "plugin_bank_folder" : getType(extension),
    bank_name: basename(dirname(path)),
    category_guess: guessPluginBankCategory(pluginName, presetName, path),
    character_keywords: getCharacterKeywords(presetName, path),
    source: getSource(path),
  };
}

function shouldIgnoreDirectory(name) {
  return IGNORED_DIRS.has(normalize(name));
}

function isPluginBankDirectory(path) {
  const haystack = normalize(path);

  if (!PLUGIN_BANK_FOLDER_HINTS.some((hint) => haystack.includes(hint))) return false;

  return (
    haystack.includes("preset") ||
    haystack.includes("soundbank") ||
    haystack.includes("patch") ||
    haystack.includes("surge") ||
    haystack.includes("tal-") ||
    haystack.includes("tal ") ||
    haystack.includes("vital") ||
    haystack.includes("u-he") ||
    haystack.includes("uhe")
  );
}

function scanDirectory(root, result) {
  if (!existsSync(root)) return;

  let entries = [];

  try {
    entries = readdirSync(root, { withFileTypes: true });
  } catch {
    return;
  }

  for (const entry of entries) {
    if (entry.isDirectory() && shouldIgnoreDirectory(entry.name)) continue;

    const path = join(root, entry.name);
    const extension = extname(entry.name).toLowerCase();

    if (entry.isDirectory()) {
      if (PLUGIN_EXTENSIONS.has(extension)) {
        result.plugins.push(createItem(path, extension));
        continue;
      }

      if (isPluginBankDirectory(path)) {
        result.plugin_banks.push(createPluginBankItem(path, "folder"));
      }

      scanDirectory(path, result);
      continue;
    }

    if (!entry.isFile()) continue;

    const item = createItem(path, extension);

    if (ABLETON_EXTENSIONS.has(extension)) {
      result.ableton_items.push(item);
    } else if (PLUGIN_BANK_EXTENSIONS.has(extension)) {
      result.plugin_banks.push(createPluginBankItem(path, extension));
    } else if (SAMPLE_EXTENSIONS.has(extension)) {
      result.samples.push(item);
    } else if (PLUGIN_EXTENSIONS.has(extension)) {
      result.plugins.push(item);
    }
  }
}

export function buildSystemIndex({ scanRoots = DEFAULT_SCAN_ROOTS, outputPath = getSystemIndexPath() } = {}) {
  const result = {
    created_at: new Date().toISOString(),
    scan_roots: scanRoots,
    totals: {
      ableton_items: 0,
      samples: 0,
      plugins: 0,
      plugin_banks: 0,
    },
    ableton_items: [],
    plugin_banks: [],
    samples: [],
    plugins: [],
  };

  for (const root of scanRoots) {
    scanDirectory(root, result);
  }

  result.totals = {
    ableton_items: result.ableton_items.length,
    samples: result.samples.length,
    plugins: result.plugins.length,
    plugin_banks: result.plugin_banks.length,
  };

  mkdirSync(dirname(outputPath), { recursive: true });
  writeFileSync(outputPath, JSON.stringify(result, null, 2));

  return result;
}

function isCliRun() {
  return process.argv[1] === fileURLToPath(import.meta.url);
}

if (isCliRun()) {
  const index = buildSystemIndex();
  console.log(`ArrangementGPS system index created`);
  console.log(`Ableton items: ${index.totals.ableton_items}`);
  console.log(`Samples: ${index.totals.samples}`);
  console.log(`Plugins: ${index.totals.plugins}`);
  console.log(`Plugin banks: ${index.totals.plugin_banks}`);
  console.log(`Saved: ${getSystemIndexPath()}`);
}
