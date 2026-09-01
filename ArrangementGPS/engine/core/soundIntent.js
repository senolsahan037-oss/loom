export function buildSoundIntent() {
  return {
    global: {
      warmth: 0.85,
      darkness: 0.65,
      analog_feel: 0.8,
      dust: 0.7,
      aggression: 0.35,
      stereo_width: 0.6,
      cleanliness: 0.45
    },

    tracks: {
      "drums.kit": {
        character: "boom bap kit — deep punchy kick, tight dry snare, loose crisp hats, organic percussion",
        warmth: 0.7,
        attack: 0.8,
        dirt: 0.4
      },

      "bass.main": {
        character: "warm analog bass",
        low_weight: 0.9,
        movement: 0.55,
        saturation: 0.45
      },
      "bass.sub": {
        character: "clean low foundation",
        low_weight: 0.95,
        movement: 0.25
      },

      "chords.keys": {
        character: "dusty electric piano / rhodes feel",
        warmth: 0.85,
        width: 0.55,
        texture: 0.65
      },
      "chords.pad": {
        character: "soft atmospheric pad",
        width: 0.75,
        darkness: 0.55
      },

      "melody.lead": {
        character: "lonely expressive lead",
        emotion: 0.9,
        brightness: 0.55,
        width: 0.45
      },

      "vocal.main": {
        character: "wide intimate vocal space",
        clarity: 0.75,
        room: 0.45,
        delay_tail: 0.4
      },

      "fx.transitions": {
        character: "tape space, risers, impacts",
        width: 0.8,
        movement: 0.75,
        atmosphere: 0.85
      }
    }
  };
}
