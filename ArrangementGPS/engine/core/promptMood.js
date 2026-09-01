// Keyword-based mood/genre extraction from the user's prompt. No API call --
// deliberately simple so it's cheap and testable without OPENAI_API_KEY.
// Genre names are the exact strings Sensei's dataset uses
// (data/genre_identity/ableton_genre_neighbor_graph.json role_genre_counts),
// so a match here is guaranteed to be a genre Sensei can actually resolve
// against real instrument catalog entries.

const MINOR_SIGNALS = [
  "dark", "sad", "melancholic", "melancholy", "melankolik", "hüzünlü", "huzunlu",
  "moody", "dramatic", "night", "gece", "karanlık", "karanlik", "lo-fi", "lofi",
  "atmospheric", "emotional", "duygusal", "somber", "haunting", "minor", "eerie", "sinister"
];

const MAJOR_SIGNALS = [
  "energy", "energetic", "enerjik", "happy", "mutlu", "uplifting", "bright", "parlak",
  "party", "festival", "upbeat", "positive", "pozitif", "joyful", "euphoric", "major",
  "sunny", "cheerful"
];

// Ordered: more specific/compound terms first so they win over a shorter
// substring that might also appear (e.g. "hip hop" before a bare "house").
const GENRE_KEYWORDS = [
  ["Hip Hop", ["hip hop", "hiphop", "rap", "g-funk", "gfunk", "g funk", "west coast", "boom bap", "boombap"]],
  ["Drum & Bass", ["drum & bass", "drum and bass", "dnb", "d&b"]],
  ["Trap", ["trap"]],
  ["House", ["house"]],
  ["Techno", ["techno"]],
  ["Dubstep", ["dubstep"]],
  ["Reggaeton", ["reggaeton"]],
  ["Reggae", ["reggae"]],
  ["Dancehall", ["dancehall", "dance hall"]],
  ["Synthpop", ["synthpop", "synth pop", "synth-pop"]],
  ["Electronica", ["electronica", "electronic"]],
  ["Electro", ["electro"]],
  ["Breakbeat", ["breakbeat", "break beat"]],
  ["Garage", ["uk garage", "garage"]],
  ["R&B", ["r&b", "rnb", "r and b"]],
  ["Pop", ["pop"]],
  ["Rock", ["rock"]],
  ["Funk", ["funk"]],
  ["EDM", ["edm"]],
  ["Dub", ["dub"]],
  ["Jazz", ["jazz"]],
  ["Latin", ["latin"]],
  ["Ambient", ["ambient"]],
  ["Disco", ["disco"]],
  ["Soul", ["soul"]],
  ["Country", ["country"]],
  ["Folk", ["folk"]],
  ["Punk", ["punk"]],
  ["Gospel", ["gospel"]],
  ["Indie", ["indie"]],
  ["Classical", ["classical", "orchestral"]],
  ["Experimental", ["experimental"]],
  ["Blues", ["blues"]]
];

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Word-boundary match, not a bare substring check -- "rap" must not match
// inside "trap", "soul" must not match inside unrelated words, etc.
function containsWord(text, phrase) {
  return new RegExp(`\\b${escapeRegExp(phrase)}\\b`, "i").test(text);
}

// Live's own tempo range. A number outside it is far more likely to be
// something else in the prompt ("808", "2026") than a tempo.
const MIN_BPM = 40;
const MAX_BPM = 300;

// "126 bpm", "126bpm", "bpm 126", "at 126 bpm". A bare number is never
// treated as a tempo -- the unit has to be there, or "808 bassline" becomes
// an 808 BPM project.
export function parseTempo(prompt) {
  const text = String(prompt || "");
  const match = text.match(/(\d{2,3})\s*bpm\b/i) ?? text.match(/\bbpm\s*[:=]?\s*(\d{2,3})\b/i);
  if (!match) return null;
  const bpm = Number(match[1]);
  return Number.isFinite(bpm) && bpm >= MIN_BPM && bpm <= MAX_BPM ? bpm : null;
}

// "in F minor", "key of A minor", "C# major", "Bb minor". The mode word has
// to sit directly after the note, and a bare "a" only counts when it is
// introduced by "in"/"key of" -- otherwise "a minor tweak" would be read as
// the key of A minor.
export function parseKey(prompt) {
  const text = String(prompt || "");
  const pattern = /(?:\b(?:in|key\s+of)\s+)?\b([A-Ga-g])([#b\u266f\u266d]?)\s+(minor|major)\b/g;
  for (const match of text.matchAll(pattern)) {
    const letter = match[1];
    // The "in"/"key of" prefix is part of the match itself, so it has to be
    // tested there rather than in the text before it.
    const introduced = /^(?:in|key\s+of)\s+/i.test(match[0]);
    if (letter.toLowerCase() === "a" && !introduced) continue;
    const accidental = match[2].replace("\u266f", "#").replace("\u266d", "b");
    return {
      root: letter.toUpperCase() + accidental,
      mode: match[3].toLowerCase() === "major" ? "major" : "minor"
    };
  }
  return null;
}

export function deriveMoodFromPrompt(prompt) {
  const text = prompt || "";

  const minorHits = MINOR_SIGNALS.filter((word) => containsWord(text, word)).length;
  const majorHits = MAJOR_SIGNALS.filter((word) => containsWord(text, word)).length;
  const mode = majorHits > minorHits ? "major" : "minor";

  let genre = null;
  for (const [canonical, keywords] of GENRE_KEYWORDS) {
    if (keywords.some((keyword) => containsWord(text, keyword))) {
      genre = canonical;
      break;
    }
  }

  // An explicitly written key beats the mood keywords: "dark" suggesting
  // minor is a guess, "in F major" is a statement.
  const key = parseKey(prompt);
  return { mode: key ? key.mode : mode, genre, key, bpm: parseTempo(prompt) };
}
