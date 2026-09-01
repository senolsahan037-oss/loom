export function buildScene() {
  return {
    sections: [
      { id: "intro", name: "Intro", start_bar: 1, end_bar: 8, energy: 20 },
      { id: "verse_1", name: "Verse 1", start_bar: 9, end_bar: 24, energy: 60 },
      { id: "hook", name: "Hook", start_bar: 25, end_bar: 32, energy: 90 },
      { id: "verse_2", name: "Verse 2", start_bar: 33, end_bar: 48, energy: 65 },
      { id: "bridge", name: "Bridge", start_bar: 49, end_bar: 56, energy: 35 },
      { id: "final_hook", name: "Final Hook", start_bar: 57, end_bar: 72, energy: 100 },
      { id: "outro", name: "Outro", start_bar: 73, end_bar: 80, energy: 15 }
    ],

    lanes: {
      drums: {
        activity: {
          intro: 30,
          verse_1: 75,
          hook: 95,
          verse_2: 75,
          bridge: 40,
          final_hook: 100,
          outro: 20
        }
      },
      bass: {
        activity: {
          intro: 10,
          verse_1: 80,
          hook: 95,
          verse_2: 80,
          bridge: 30,
          final_hook: 100,
          outro: 25
        }
      },
      chords: {
        activity: {
          intro: 50,
          verse_1: 60,
          hook: 80,
          verse_2: 60,
          bridge: 70,
          final_hook: 85,
          outro: 40
        }
      },
      melody: {
        activity: {
          intro: 25,
          verse_1: 35,
          hook: 85,
          verse_2: 45,
          bridge: 55,
          final_hook: 90,
          outro: 30
        }
      },
      vocal: {
        activity: {
          intro: 0,
          verse_1: 85,
          hook: 90,
          verse_2: 85,
          bridge: 20,
          final_hook: 95,
          outro: 10
        }
      },
      fx: {
        activity: {
          intro: 60,
          verse_1: 30,
          hook: 55,
          verse_2: 35,
          bridge: 75,
          final_hook: 85,
          outro: 70
        }
      }
    },

    event_markers: [
      {
        track: "drums",
        bar: 24,
        type: "snare_scroll",
        label: "Hook Build"
      },
      {
        track: "fx",
        bar: 56,
        type: "short_silence",
        label: "Pre Final Hook"
      },
      {
        track: "bass",
        bar: 57,
        type: "bass_entry",
        label: "Final Impact"
      }
    ]
  };
}
