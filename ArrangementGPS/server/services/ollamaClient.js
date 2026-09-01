const OLLAMA_BASE_URL = "http://127.0.0.1:11434";
const OLLAMA_URL = `${OLLAMA_BASE_URL}/api/generate`;
const OLLAMA_TIMEOUT_MS = 130000;

export const OLLAMA_MODEL = "llama3.1:8b";

function createOllamaPrompt(userPrompt) {
  return `
You are the creative production director for ArrangementGPS.

Your task:
Interpret the user's musical idea and write a short creative production brief
that the ArrangementGPS engine can turn into an arrangement blueprint.

OUTPUT:
- Return a short, usable, creative production brief.
- Plain text, bullet points or loose JSON are all fine.
- Do not try to produce a final UI schema.
- Do not try to fill in technical schema fields exhaustively.
- Do not name plugins, presets or local library items.

FORBIDDEN:
- Writing MIDI.
- Writing notes.
- Writing chord progressions.
- Writing drum patterns.
- Writing basslines.
- Writing lyrics.

CONVEY THIS IN THE BRIEF:
- State the style and mood, and the tempo/key if given.
- Describe how the energy should move through the song.
- Give a role and an intention for drums, bass, chords, melody, vocal and fx.
- Name the important dramatic turning points and event ideas.

USER PROMPT:
${userPrompt}
`;
}

export async function requestBlueprintFromOllama(prompt) {
  const trimmedPrompt = String(prompt ?? "").trim();

  if (!trimmedPrompt) {
    throw new Error("Prompt is required.");
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), OLLAMA_TIMEOUT_MS);

  console.log("[generate-blueprint] Calling Ollama");

  try {
    const response = await fetch(OLLAMA_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      signal: controller.signal,
      body: JSON.stringify({
        model: OLLAMA_MODEL,
        prompt: createOllamaPrompt(trimmedPrompt),
        stream: false,
        options: {
          temperature: 0.7,
          num_ctx: 4096
        }
      })
    });

    if (!response.ok) {
      throw new Error(`Ollama request failed with status ${response.status}.`);
    }

    const payload = await response.json();
    console.log("[generate-blueprint] Ollama responded");

    const brief = payload.response ?? "";
    console.log("[generate-blueprint] LLM brief received");

    return brief;
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error("Ollama request timed out");
    }

    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

export async function getOllamaReachability() {
  try {
    const response = await fetch(`${OLLAMA_BASE_URL}/api/tags`);
    return response.ok ? "reachable" : "unreachable";
  } catch {
    return "unreachable";
  }
}
