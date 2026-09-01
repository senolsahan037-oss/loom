import OpenAI from "openai";

export async function generateCreativeBrief(prompt) {
  if (!process.env.OPENAI_API_KEY) {
    // mode/genre come from deriveMoodFromPrompt (keyword-based, no LLM), so
    // the rest of the pipeline doesn't depend on this being a real brief --
    // this placeholder just keeps local/offline testing runnable. The
    // OpenAI client itself must stay uncreated until here too: its
    // constructor throws immediately when no key/env var is present.
    return `PROJECT\n${prompt}\n\n(offline placeholder brief -- OPENAI_API_KEY not set, no LLM call made)`;
  }

  const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
  const response = await client.responses.create({
    model: "gpt-4.1-mini",
    input: `
You are ArrangementGPS, an AI executive music producer.

Create a concise production brief for this prompt:
${prompt}

Return useful production planning content only.
Do not write MIDI, notes, lyrics, drum patterns, or basslines.

Include:
PROJECT
STYLE
ARRANGEMENT
DRUMS
BASS
CHORDS
MELODY
VOCAL
FX
EVENT MARKERS
SOUND INTENT
`
  });

  return response.output_text;
}
