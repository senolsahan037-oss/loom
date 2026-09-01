// Prompt -> tempo/key/genre/mode cikarimi. Saf fonksiyon, Live gerekmiyor.
import assert from "node:assert/strict";
import { deriveMoodFromPrompt, parseKey, parseTempo } from "../engine/core/promptMood.js";

const checks = [];
function check(label, fn) {
  fn();
  checks.push(label);
}

check("bpm is read out of the prompt", () => {
  assert.equal(parseTempo("dark tech house, 126 bpm"), 126);
  assert.equal(parseTempo("128BPM banger"), 128);
  assert.equal(parseTempo("bpm: 90 boom bap"), 90);
});
check("a bare number is never mistaken for a tempo", () => {
  assert.equal(parseTempo("808 heavy trap"), null);
  assert.equal(parseTempo("a 2026 sounding record"), null);
});
check("a tempo outside Live's range is rejected", () => {
  assert.equal(parseTempo("999 bpm"), null);
  assert.equal(parseTempo("12 bpm"), null);
});
check("an explicitly stated key is read", () => {
  assert.deepEqual(parseKey("lofi beat in F# minor"), { root: "F#", mode: "minor" });
  assert.deepEqual(parseKey("anthem in C major"), { root: "C", mode: "major" });
  assert.deepEqual(parseKey("key of Bb minor"), { root: "Bb", mode: "minor" });
});
check('"a minor <noun>" is not read as the key of A minor', () => {
  assert.equal(parseKey("a minor tweak to the drums"), null);
  assert.deepEqual(parseKey("in a minor mood"), { root: "A", mode: "minor" });
});
check("a stated key overrides the mood keywords", () => {
  // "dark" alone would give minor; the written key wins either way.
  assert.equal(deriveMoodFromPrompt("dark anthem in C major").mode, "major");
});
check("mood keywords still decide when no key is written", () => {
  assert.equal(deriveMoodFromPrompt("dark moody night").mode, "minor");
  assert.equal(deriveMoodFromPrompt("energetic uplifting festival").mode, "major");
});
check("genre resolves to a name Sensei's dataset actually carries", () => {
  // "Tech House" is not in Sensei's 33-genre vocabulary; "House" is, and it
  // has real coverage (drum 31 / bass 4 / chord 5), so this is correct.
  assert.equal(deriveMoodFromPrompt("dark rolling tech house").genre, "House");
  assert.equal(deriveMoodFromPrompt("boom bap loop").genre, "Hip Hop");
  assert.equal(deriveMoodFromPrompt("808 heavy trap").genre, "Trap");
});
check("a compound genre wins over a shorter substring", () => {
  assert.equal(deriveMoodFromPrompt("uk garage shuffle").genre, "Garage");
  assert.equal(deriveMoodFromPrompt("west coast g-funk").genre, "Hip Hop");
});
check("the full prompt round-trip carries tempo and genre together", () => {
  const result = deriveMoodFromPrompt("dark rolling tech house, 126 bpm, hypnotic bassline");
  assert.equal(result.bpm, 126);
  assert.equal(result.genre, "House");
  assert.equal(result.mode, "minor");
});

console.log(`${checks.length} kontrol gecti:`);
for (const label of checks) console.log(`  ok  ${label}`);
console.log("TUM KONTROLLER GECTI");
