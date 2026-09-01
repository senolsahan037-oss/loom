import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const DEFAULT_SYSTEM_INDEX_PATH = join(
  homedir(),
  "Library",
  "Application Support",
  "ArrangementGPS",
  "system_index.json"
);

const EMPTY_SELECTED_LIBRARY = {
  drums: {},
  bass: {},
  chords: {},
  melody: {},
  fx: {},
};

function getSystemIndexPath() {
  return process.env.ARRANGEMENTGPS_SYSTEM_INDEX || DEFAULT_SYSTEM_INDEX_PATH;
}

function readSystemIndex() {
  const systemIndexPath = getSystemIndexPath();

  if (!existsSync(systemIndexPath)) {
    return null;
  }

  try {
    return JSON.parse(readFileSync(systemIndexPath, "utf8"));
  } catch {
    return null;
  }
}

const TRACK_MATCHERS = {
  drums: {
    trackNames: ["drums"],
    pools: ["ableton_items", "samples", "plugins"],
    keywords: ["drum", "kit", "rack", "drums", "boom bap", "break", "percussion", "tal-drum"],
    avoid: ["midi clips/tonal", "bass", "lead", "pad", "piano"],
    fallback: "No strong drum device match found",
  },
  bass: {
    trackNames: ["bass"],
    pools: ["ableton_items", "plugins"],
    keywords: ["bass", "sub", "low", "operator bass", "analog bass", "tal-bassline-101"],
    avoid: ["drum", "vocal", "lead", "pad"],
    fallback: "No strong bass device match found",
  },
  chords: {
    trackNames: ["chords"],
    pools: ["ableton_items", "plugins"],
    keywords: ["keys", "pad", "piano", "rhodes", "electric piano", "chord", "harmony", "organ", "dexed", "surge xt", "zebra", "tal-u-no", "tal-j-8"],
    avoid: ["drum", "bass", "vocal"],
    fallback: "No strong harmonic instrument match found",
  },
  melody: {
    trackNames: ["melody"],
    pools: ["ableton_items", "plugins"],
    keywords: ["lead", "pluck", "melody", "melodic", "keys", "synth", "arp", "vital", "surge xt", "dexed", "zebra", "tyrell", "tal-noisemaker"],
    avoid: ["drum", "bass", "vocal"],
    fallback: "No strong melodic instrument match found",
  },
  fx: {
    trackNames: ["fx"],
    pools: ["ableton_items", "plugins"],
    keywords: ["reverb", "delay", "saturation", "atmosphere", "texture", "echo", "audio effect", "space", "surge xt effects", "tal-chorus", "tal-filter", "tal-reverb"],
    avoid: ["midi clips/tonal", "drum kit", "bass"],
    fallback: "No strong FX match found",
  },
};

const EXTENSION_SCORE = {
  ".clap": 14,
  ".adg": 18,
  ".adv": 16,
  ".amxd": 10,
  ".alc": -8,
};

function normalize(value) {
  return String(value ?? "").toLowerCase();
}

function getTrackIntent(track) {
  return [
    track?.name,
    track?.role,
    track?.density_profile,
    track?.groove_profile,
    track?.variation_plan,
    track?.transition_plan,
    track?.automation_intent,
    track?.sound_source_intent,
    ...(Object.values(track?.planning_metadata ?? {})),
  ]
    .map(normalize)
    .join(" ");
}

function scoreItem(item, matcher, trackIntent) {
  const haystack = [
    item.name,
    item.category,
    item.type,
    item.path,
    item.source,
    item.guessed_family,
    ...(item.tags ?? []),
    ...(item.character_keywords ?? []),
  ]
    .map(normalize)
    .join(" ");
  let score = 0;
  const reasons = [];

  for (const keyword of matcher.keywords) {
    if (haystack.includes(keyword)) {
      score += 14;
      reasons.push(`matched "${keyword}"`);
    }

    if (trackIntent.includes(keyword) && haystack.includes(keyword)) {
      score += 5;
    }
  }

  for (const avoid of matcher.avoid) {
    if (haystack.includes(avoid)) {
      score -= 10;
    }
  }

  if (normalize(item.source).includes("core library") || normalize(item.path).includes("core library")) {
    score += 8;
    reasons.push("Core Library");
  }

  score += EXTENSION_SCORE[item.extension] ?? 0;

  if (item.category === "sample" && matcher.pools.includes("samples")) {
    score += 5;
  }

  if (item.category === "plugin" && matcher.pools.includes("plugins")) {
    score += 5;
  }

  if (matcher.pools.includes("plugins")) {
    if (["bass", "chords", "melody"].some((trackName) => matcher.trackNames.includes(trackName)) && item.category === "plugin_instrument") {
      score += 12;
      reasons.push("local instrument plugin");
    }

    if (matcher.trackNames.includes("fx") && item.category === "plugin_effect") {
      score += 12;
      reasons.push("local effect plugin");
    }
  }

  if (matcher.trackNames.includes("drums") && item.category === "plugin_drum_instrument") {
    score += 12;
    reasons.push("local drum plugin");
  }

  if (item.type === "clip") {
    score -= 12;
  }

  if (item.extension === ".alc" && score > 0) {
    reasons.push("clip fallback");
  }

  return {
    score,
    reason: reasons.slice(0, 3).join(", ") || "closest keyword match",
  };
}

function scorePluginBank(item, matcher, trackIntent, trackKey) {
  const haystack = [
    item.plugin_name,
    item.preset_name,
    item.bank_name,
    item.category_guess,
    item.path,
    item.source,
    ...(item.character_keywords ?? []),
  ]
    .map(normalize)
    .join(" ");
  let score = 0;
  const reasons = [];

  for (const keyword of matcher.keywords) {
    if (haystack.includes(keyword)) {
      score += 10;
      reasons.push(`matched "${keyword}"`);
    }

    if (trackIntent.includes(keyword) && haystack.includes(keyword)) {
      score += 5;
    }
  }

  if (
    (trackKey === "drums" && item.category_guess === "drums") ||
    (trackKey === "bass" && item.category_guess === "bass") ||
    (trackKey === "chords" && item.category_guess === "harmony") ||
    (trackKey === "melody" && item.category_guess === "melody") ||
    (trackKey === "fx" && item.category_guess === "fx")
  ) {
    score += 18;
    reasons.push("matched track family");
  }

  if (item.extension === "folder") {
    score += 4;
    reasons.push("local bank folder");
  }

  if (item.plugin_name && item.plugin_name !== "Unknown Plugin" && item.plugin_name !== "VST Preset") {
    score += 8;
    reasons.push("known local plugin");
  }

  for (const avoid of matcher.avoid) {
    if (haystack.includes(avoid)) {
      score -= 8;
    }
  }

  return {
    score,
    reason: reasons.slice(0, 3).join(", ") || "closest plugin bank match",
  };
}

function toSelection(item, score, reason, fallbackReason) {
  if (!item || score < 12) {
    return {
      selected_name: "",
      selected_path: "",
      category: "",
      type: "",
      confidence: 0,
      reason: fallbackReason,
    };
  }

  return {
    selected_name: item.name ?? "",
    selected_path: item.path ?? "",
    category: item.category ?? "",
    type: item.type ?? "",
    confidence: Number(Math.min(0.98, Math.max(0.2, score / 80)).toFixed(2)),
    reason,
  };
}

function toPluginBankSelection(item, score, reason) {
  if (!item || score < 18) {
    return {
      local_plugin: "",
      preset_name: "",
      bank_name: "",
      plugin_bank_confidence: 0,
      plugin_bank_reason: "",
    };
  }

  return {
    local_plugin: item.plugin_name ?? "",
    preset_name: item.extension === "folder" ? "" : item.preset_name ?? "",
    bank_name: item.bank_name ?? "",
    plugin_bank_confidence: Number(Math.min(0.98, Math.max(0.25, score / 70)).toFixed(2)),
    plugin_bank_reason: reason,
  };
}

function findTrack(blueprint, matcher) {
  return blueprint.tracks?.find((track) =>
    matcher.trackNames.includes(normalize(track.name))
  );
}

export function matchAbletonLibrary(blueprint) {
  const systemIndex = readSystemIndex();

  if (!systemIndex) {
    return EMPTY_SELECTED_LIBRARY;
  }

  return Object.fromEntries(
    Object.entries(TRACK_MATCHERS).map(([key, matcher]) => {
      const track = findTrack(blueprint, matcher);
      const trackIntent = getTrackIntent(track);
      const items = matcher.pools.flatMap((pool) => systemIndex[pool] ?? []);
      const pluginBanks = systemIndex.plugin_banks ?? [];
      let best = { item: null, score: -Infinity, reason: "" };
      let bestPluginBank = { item: null, score: -Infinity, reason: "" };

      for (const item of items) {
        const result = scoreItem(item, matcher, trackIntent);

        if (result.score > best.score) {
          best = { item, score: result.score, reason: result.reason };
        }
      }

      for (const item of pluginBanks) {
        const result = scorePluginBank(item, matcher, trackIntent, key);

        if (result.score > bestPluginBank.score) {
          bestPluginBank = { item, score: result.score, reason: result.reason };
        }
      }

      return [
        key,
        {
          ...toSelection(best.item, best.score, best.reason, matcher.fallback),
          ...toPluginBankSelection(
            bestPluginBank.item,
            bestPluginBank.score,
            bestPluginBank.reason
          ),
        },
      ];
    })
  );
}
