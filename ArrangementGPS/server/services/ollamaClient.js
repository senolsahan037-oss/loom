const OLLAMA_BASE_URL = "http://127.0.0.1:11434";
const OLLAMA_URL = `${OLLAMA_BASE_URL}/api/generate`;
const OLLAMA_TIMEOUT_MS = 130000;

export const OLLAMA_MODEL = "llama3.1:8b";

function createOllamaPrompt(userPrompt) {
  return `
Sen ArrangementGPS icin yaratici produksiyon direktorusun.

Görevin:
Kullanicinin muzik fikrini yorumla ve ArrangementGPS motorunun aranjman blueprint'ine cevirebilecegi kisa bir kreatif produksiyon brief'i yaz.

CIKTI:
- Kisa, kullanisli, yaratici bir produksiyon brief'i dondur.
- Duz metin, maddeler veya gevsek JSON kullanabilirsin.
- Final UI semasi uretmeye calisma.
- Teknik schema alanlarini eksiksiz doldurmaya calisma.
- Plugin, preset veya lokal kutuphane adi onerme.

YASAK:
- MIDI yazma.
- Nota yazma.
- Akor dizisi yazma.
- Drum pattern yazma.
- Bassline yazma.
- Lyric yazma.

BRIEF'TE SU ANLAMLARI VER:
- Stil, duygu, tempo/key varsa belirt.
- Enerji akisinin nasil olmasi gerektigini anlat.
- Drums, bass, chords, melody, vocal ve fx icin rol/intention yaz.
- Onemli dramatik donus noktalarini ve event fikirlerini belirt.

KULLANICI PROMPTU:
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
