import { Router } from "express";
import { generateBlueprintFromPrompt } from "../blueprintGenerator.js";

const router = Router();

router.post("/", async (request, response) => {
  console.log("[generate-blueprint] request received");

  try {
    const blueprint = await generateBlueprintFromPrompt(request.body?.prompt);

    console.log("[generate-blueprint] blueprint returned");
    response.status(200).json({ success: true, blueprint });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Blueprint generation failed";

    console.error("[generate-blueprint] Request failed", message);
    console.log("[generate-blueprint] Sending HTTP response");
    response.status(500).json({
      success: false,
      error: message,
    });
  }
});

export default router;
