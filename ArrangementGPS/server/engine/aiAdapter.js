export function extractJsonLike(raw) {
  if (!raw) return {};

  let text = String(raw).trim();

  text = text
    .replace(/^```json\s*/i, "")
    .replace(/^```\s*/i, "")
    .replace(/```$/i, "")
    .trim();

  const first = text.indexOf("{");
  const last = text.lastIndexOf("}");

  if (first !== -1 && last !== -1 && last > first) {
    text = text.slice(first, last + 1);
  }

  try {
    return JSON.parse(text);
  } catch {
    return {
      raw_text: raw,
      parse_error: true
    };
  }
}

const TRACKS = ["drums", "bass", "chords", "melody", "vocal", "fx"];

function toText(rawOutput, parsed) {
  if (typeof rawOutput === "string") return rawOutput;
  if (parsed.raw_text) return String(parsed.raw_text);

  try {
    return JSON.stringify(rawOutput ?? "");
  } catch {
    return "";
  }
}

function extractBpm(text) {
  const match = text.match(/(\d{2,3})\s*bpm/i);
  const bpm = Number(match?.[1]);

  return bpm > 40 && bpm < 220 ? bpm : undefined;
}

function extractKey(text) {
  const match = text.match(/\b([A-G](?:#|b)?\s*(?:major|minor|maj|min)?)\b/i);

  return match?.[1];
}

function extractProjectHints(text) {
  const lower = text.toLowerCase();
  const genres = [
    "boom bap",
    "trap",
    "g-funk",
    "west coast",
    "r&b",
    "pop",
    "drill",
    "lo-fi",
    "cinematic",
    "arabesk",
  ].filter((genre) => lower.includes(genre));
  const moodWords = [
    "dark",
    "emotional",
    "cinematic",
    "dusty",
    "aggressive",
    "melancholic",
    "uplifting",
    "nostalgic",
    "smooth",
  ].filter((word) => lower.includes(word));

  return {
    bpm: extractBpm(text),
    key: extractKey(text),
    genre_tags: genres,
    mood: moodWords.length ? moodWords.join(", ") : undefined,
  };
}

function extractTrackText(text, track) {
  const lines = text.split(/\r?\n/);
  const aliases = {
    drums: ["drums", "drum", "rhythm"],
    bass: ["bass", "low-end", "low end", "808"],
    chords: ["chords", "keys", "harmony", "pad"],
    melody: ["melody", "lead", "motif", "sample"],
    vocal: ["vocal", "voice", "rap", "hook"],
    fx: ["fx", "effects", "transitions", "atmosphere"],
  }[track];
  const found = lines.find((line) => aliases.some((alias) => line.toLowerCase().includes(alias)));

  if (found) {
    return found.replace(/^[-*\s"]+/, "").slice(0, 220);
  }

  return "";
}

function extractTrackIntents(text, parsed) {
  const result = {};

  for (const track of TRACKS) {
    result[track] =
      parsed.track_intents?.[track] ||
      parsed.tracks?.[track] ||
      extractTrackText(text, track);
  }

  return result;
}

function extractSoundRecommendations(text, parsed) {
  const result = {};

  for (const track of TRACKS) {
    result[track] =
      parsed.sound_recommendations?.[track] ||
      parsed.sounds?.[track] ||
      parsed.sound?.[track] ||
      "";
  }

  return result;
}

export function adaptAiOutput(rawOutput, userPrompt = "") {
  const parsed = extractJsonLike(rawOutput);
  const brief = toText(rawOutput, parsed);
  const projectHints = extractProjectHints(`${userPrompt}\n${brief}`);

  return {
    source: "llm",
    user_prompt: userPrompt,
    brief,
    raw: parsed,
    project: {
      ...projectHints,
      ...(parsed.project ?? {}),
    },
    scene: parsed.scene ?? {},
    sound_recommendations: extractSoundRecommendations(brief, parsed),
    track_intents: extractTrackIntents(brief, parsed),
    event_intents:
      parsed.event_intents ??
      parsed.scene?.event_markers ??
      []
  };
}
