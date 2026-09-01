export function buildProductionTree() {
  return {
    tracks: [
      {
        id: "drums",
        name: "Drums",
        type: "group",
        generator: "sensei_drum",
        priority: 1,
        children: [
          { id: "drums.kit", name: "Kit", role: "Full drum kit", generator: "sensei_drum", priority: 1, locked: false }
        ]
      },
      {
        id: "bass",
        name: "Bass",
        type: "group",
        generator: "bass_generator",
        priority: 2,
        children: [
          { id: "bass.main", name: "Main Bass", role: "Main low-end movement", generator: "bass_generator", priority: 1, locked: false },
          { id: "bass.sub", name: "Sub Bass", role: "Low foundation", generator: "bass_generator", priority: 2, locked: false }
        ]
      },
      {
        id: "chords",
        name: "Chords",
        type: "group",
        generator: "harmony_generator",
        priority: 3,
        children: [
          { id: "chords.keys", name: "Keys", role: "Harmony bed", generator: "harmony_generator", priority: 1, locked: false },
          { id: "chords.pad", name: "Pad", role: "Atmospheric support", generator: "harmony_generator", priority: 2, locked: false },
          { id: "chords.strings", name: "Strings", role: "Emotional layer", generator: "harmony_generator", priority: 3, locked: false }
        ]
      },
      {
        id: "melody",
        name: "Melody",
        type: "group",
        generator: "melody_generator",
        priority: 4,
        children: [
          { id: "melody.lead", name: "Lead", role: "Main motif", generator: "melody_generator", priority: 1, locked: false },
          { id: "melody.counter", name: "Counter Melody", role: "Response motif", generator: "melody_generator", priority: 2, locked: false },
          { id: "melody.texture", name: "Texture", role: "Ear candy / atmosphere", generator: "sound_designer", priority: 3, locked: false }
        ]
      },
      {
        id: "vocal",
        name: "Vocal",
        type: "group",
        generator: "vocal_planner",
        priority: 5,
        children: [
          { id: "vocal.main", name: "Main Vocal", role: "Primary vocal space", generator: "vocal_planner", priority: 1, locked: false },
          { id: "vocal.doubles", name: "Doubles", role: "Hook support", generator: "vocal_planner", priority: 2, locked: false },
          { id: "vocal.adlibs", name: "Adlibs", role: "Accent / response", generator: "vocal_planner", priority: 3, locked: false },
          { id: "vocal.fx", name: "Vocal FX", role: "Throws / tails", generator: "ai_sound_designer", priority: 4, locked: false }
        ]
      },
      {
        id: "fx",
        name: "FX",
        type: "group",
        generator: "ai_sound_designer",
        priority: 6,
        children: [
          { id: "fx.risers", name: "Risers", role: "Build tension", generator: "ai_sound_designer", priority: 1, locked: false },
          { id: "fx.impacts", name: "Impacts", role: "Section impact", generator: "ai_sound_designer", priority: 2, locked: false },
          { id: "fx.atmospheres", name: "Atmospheres", role: "Space / mood", generator: "ai_sound_designer", priority: 3, locked: false },
          { id: "fx.transitions", name: "Transitions", role: "Section glue", generator: "ai_sound_designer", priority: 4, locked: false }
        ]
      }
    ]
  };
}
