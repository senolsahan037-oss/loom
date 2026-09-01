const EMPTY_SELECTED_LIBRARY = {
  drums: {},
  bass: {},
  chords: {},
  melody: {},
  fx: {},
};

export async function matchAbletonLibrary(blueprint) {
  try {
    const response = await fetch("/api/library-match", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ blueprint }),
    });

    if (!response.ok) {
      return EMPTY_SELECTED_LIBRARY;
    }

    const payload = await response.json();

    return payload.selected_library ?? EMPTY_SELECTED_LIBRARY;
  } catch {
    return EMPTY_SELECTED_LIBRARY;
  }
}
