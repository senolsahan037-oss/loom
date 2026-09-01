import { matchAbletonLibrary } from "./libraryMatcher.js";
import { requestBlueprintFromOllama } from "./services/ollamaClient.js";
import { adaptAiOutput } from "./engine/aiAdapter.js";
import { normalizeAiBlueprint } from "./engine/blueprintNormalizer.js";

export async function generateBlueprintFromPrompt(prompt) {
  let blueprint;

  try {
    const rawResponse = await requestBlueprintFromOllama(prompt);
    const adapted = adaptAiOutput(rawResponse, prompt);
    console.log("[generate-blueprint] AI output adapted");
    blueprint = normalizeAiBlueprint(adapted);
    console.log("[generate-blueprint] Blueprint normalized");
  } catch (error) {
    const message = error instanceof Error ? error.message : "LLM generation failed";

    console.error("[generate-blueprint] LLM unavailable, fallback blueprint used", message);
    const fallbackAdapted = adaptAiOutput(
      "Emergency fallback brief: balanced 80 bar arrangement with clear verse, hook, bridge, final hook and outro energy.",
      prompt
    );
    console.log("[generate-blueprint] AI output adapted");
    blueprint = normalizeAiBlueprint({
      ...fallbackAdapted,
      source: "emergency_fallback"
    });
    blueprint.production_log = [
      { type: "complete", message: "Prompt received" },
      { type: "pending", message: "LLM unavailable, fallback blueprint used" },
      { type: "complete", message: "Blueprint normalized" },
      { type: "pending", message: "Waiting for local generators" },
    ];
    console.log("[generate-blueprint] Blueprint normalized");
  }

  const developerMetadata = {
    ...(blueprint.developer_metadata ?? {}),
    selected_library: matchAbletonLibrary(blueprint),
  };
  console.log("[generate-blueprint] UI blueprint returned");

  return {
    ...blueprint,
    developer_metadata: developerMetadata,
  };
}
