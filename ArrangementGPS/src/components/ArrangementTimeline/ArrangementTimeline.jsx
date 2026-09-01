import { useState } from "react";
import "./ArrangementTimeline.css";

function getSectionColumns(sections) {
  return sections
    .map((section) => `${Math.max(section.end_bar - section.start_bar + 1, 1)}fr`)
    .join(" ");
}

function getBarMarkers(totalBars) {
  const markers = [1, 16, 32, 48, 64, totalBars];
  return [...new Set(markers.filter((bar) => bar <= totalBars))];
}

function getBarPosition(bar, totalBars) {
  if (bar >= totalBars) return 100;

  return ((bar - 1) / totalBars) * 100;
}

function formatBarTime(bar, bpm) {
  const safeBpm = Number(bpm) > 0 ? Number(bpm) : 120;
  const seconds = Math.round((bar - 1) * 4 * (60 / safeBpm));
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;

  return `${minutes}:${String(remainingSeconds).padStart(2, "0")}`;
}

function getSectionEventPosition(section, totalBars, anchor = "center") {
  const anchorBar = anchor === "start" ? section.start_bar : (section.start_bar + section.end_bar) / 2;

  return getBarPosition(anchorBar, totalBars);
}

function getSectionName(sectionId, sections) {
  return sections.find((section) => section.id === sectionId)?.name ?? sectionId;
}

function formatSectionList(sectionIds, sections) {
  return sectionIds.map((sectionId) => getSectionName(sectionId, sections)).join(", ");
}

const behaviorCopy = {
  Drums: "Controlled verses, stronger hooks, reduced bridge/outro.",
  Bass: "Low-end support enters after intro, stronger in hooks, simplified in bridge.",
  Chords: "Harmonic bed supports intro and bridge, thicker during hooks.",
  Melody: "Lead identity appears in intro and hooks, with space left through verses.",
  Vocal: "Verse-focused space with wider hook presence and bridge silence.",
  FX: "Atmosphere and transitions support section changes without dominating.",
};

const fallbackEventPlan = {
  Drums: {
    verse_1: { label: "Groove Entry", anchor: "start" },
    hook: { label: "Hook Lift", anchor: "center" },
    bridge: { label: "Bridge Reset", anchor: "center" },
    final_hook: { label: "Final Hook Push", anchor: "center" },
  },
  Bass: {
    verse_1: { label: "Bass Entry", anchor: "start" },
    hook: { label: "Hook Weight", anchor: "center" },
    bridge: { label: "Bridge Pullback", anchor: "center" },
    final_hook: { label: "Final Return", anchor: "center" },
  },
  Chords: {
    intro: { label: "Harmonic Entry", anchor: "start" },
    hook: { label: "Hook Expansion", anchor: "center" },
    bridge: { label: "Texture Shift", anchor: "center" },
    final_hook: { label: "Final Layer", anchor: "center" },
  },
  Melody: {
    intro: { label: "Motif Entry", anchor: "start" },
    hook: { label: "Hook Lead", anchor: "center" },
    bridge: { label: "Bridge Variation", anchor: "center" },
    outro: { label: "Outro Motif", anchor: "center" },
  },
  Vocal: {
    verse_1: { label: "Verse Space", anchor: "start" },
    hook: { label: "Hook Presence", anchor: "center" },
    bridge: { label: "Bridge Silence", anchor: "center" },
    final_hook: { label: "Final Hook Focus", anchor: "center" },
  },
  FX: {
    intro: { label: "Atmosphere Entry", anchor: "start" },
    hook: { label: "Transition Lift", anchor: "center" },
    bridge: { label: "Bridge Space", anchor: "center" },
    final_hook: { label: "Final Impact", anchor: "center" },
  },
};

const musicalNameReplacements = [
  ["s&h pluck slow lead", "Arabesk Lead"],
  ["harpsichord", "Dusty Rhodes"],
  ["sub mellow", "Warm Analog Bass"],
  ["hs kit boxing", "Boom Bap Kit"],
  ["zebrahz", "Analog Lead"],
  ["zebra2", "Analog Lead"],
  ["vital preset", "Modern Synth Tone"],
  ["surge xt", "Expressive Synth"],
  ["u-he", "Analog Character"],
];

function formatMusicalValue(value) {
  const nextValue = String(value ?? "");
  const normalizedValue = nextValue.toLowerCase();
  const replacement = musicalNameReplacements.find(([technicalName]) =>
    normalizedValue.includes(technicalName)
  );

  return replacement ? replacement[1] : nextValue;
}

function getIntentChips(track) {
  const metadata = track.planning_metadata ?? {};
  const hiddenLabels = {
    Drums: new Set(["Kit", "Drum Kit Recommendation", "Sound"]),
  };

  return Object.entries(metadata)
    .filter(([label]) => !(hiddenLabels[track.name]?.has(label)))
    .map(([label, value]) => {
      if (track.name === "Melody" && label === "Identity") {
        return ["Character", "Emotional"];
      }

      if (track.name === "Chords" && label === "Texture") {
        return ["Texture", "Dusty Vintage"];
      }

      return [label, formatMusicalValue(value)];
    });
}

function getSuggestedSound(track) {
  const metadata = track.planning_metadata ?? {};
  const preferredKeys = {
    Drums: ["Kit", "Drum Kit Recommendation", "Sound"],
    Bass: ["Character", "Bass Character", "Sound"],
    Chords: ["Harmony", "Sound"],
    Melody: ["Identity", "Lead Identity", "Sound"],
    Vocal: ["Space", "Vocal Space", "Sound"],
    FX: ["Atmosphere", "FX Palette", "Sound"],
  };
  const keys = preferredKeys[track.name] ?? ["Sound", "Texture", "Character"];
  const matchedKey = keys.find((key) => metadata[key]);

  return formatMusicalValue(matchedKey ? metadata[matchedKey] : track.sound_source_intent);
}

function getSuggestedSoundLabel(trackName) {
  if (trackName === "Drums") return "Kit";
  if (trackName === "Bass") return "Bass";
  if (trackName === "Chords") return "Harmony";
  if (trackName === "Melody") return "Lead";
  if (trackName === "Vocal") return "Vocal Space";
  if (trackName === "FX") return "FX Palette";

  return "Sound";
}

function getDensitySummary(track) {
  const activeSections = track.active_sections ?? [];
  const sectionDensity = {
    verse_1: activeSections.includes("verse_1") ? "Medium" : "Low",
    hook: activeSections.includes("hook") ? "High" : "Low",
    bridge: activeSections.includes("bridge") ? "Low" : "Silent",
  };

  return [
    ["Verse", sectionDensity.verse_1],
    ["Hook", sectionDensity.hook],
    ["Bridge", sectionDensity.bridge],
  ];
}

function normalizeTrackEventName(value) {
  return String(value ?? "").toLowerCase().replace(/^track_/, "");
}

function getBlueprintEventMarkers(track, sections, totalBars, blueprintEvents) {
  const trackName = normalizeTrackEventName(track.name);
  const topLevelEvents = Array.isArray(blueprintEvents)
    ? blueprintEvents.filter((event) => normalizeTrackEventName(event.track) === trackName)
    : [];
  const sourceEvents = topLevelEvents.length > 0
    ? topLevelEvents
    : track.event_markers ?? track.arrangement_events ?? track.events;

  if (!Array.isArray(sourceEvents)) return [];

  return sourceEvents
    .map((event) => {
      const sectionId = event.section ?? event.section_id;
      const section = sections.find((nextSection) => nextSection.id === sectionId);
      const eventBar = Number(event.bar);
      const position = Number.isFinite(eventBar)
        ? getBarPosition(eventBar, totalBars)
        : section
          ? getSectionEventPosition(section, totalBars, event.anchor)
          : null;

      if (position === null) return null;

      return {
        id: event.id ?? `${track.id}-${event.label ?? sectionId ?? eventBar}`,
        label: event.label ?? "Arrangement Event",
        position,
      };
    })
    .filter(Boolean)
    .slice(0, 4);
}

function getFallbackEventMarkers(track, sections, totalBars) {
  const trackPlan = fallbackEventPlan[track.name] ?? {};

  return Object.entries(trackPlan)
    .map(([sectionId, event]) => {
      const section = sections.find((nextSection) => nextSection.id === sectionId);

      if (!section) return null;

      return {
        id: `${track.id}-${sectionId}`,
        label: event.label,
        position: getSectionEventPosition(section, totalBars, event.anchor),
      };
    })
    .filter(Boolean)
    .slice(0, 4);
}

function getEventMarkers(track, sections, totalBars, blueprintEvents) {
  const blueprintMarkers = getBlueprintEventMarkers(track, sections, totalBars, blueprintEvents);

  return blueprintMarkers.length > 0
    ? blueprintMarkers
    : getFallbackEventMarkers(track, sections, totalBars);
}

function ArrangementTimeline({ blueprint }) {
  const [expandedLanes, setExpandedLanes] = useState(() => new Set(["track_drums"]));
  const sections = blueprint.arrangement.sections;
  const tracks = blueprint.tracks;
  const sectionColumns = getSectionColumns(sections);
  const barMarkers = getBarMarkers(blueprint.project.total_bars);
  const eventMarkersSource = blueprint.scene?.event_markers ?? blueprint.event_intents;

  function toggleLane(trackId) {
    setExpandedLanes((current) => {
      const next = new Set(current);

      if (next.has(trackId)) {
        next.delete(trackId);
      } else {
        next.add(trackId);
      }

      return next;
    });
  }

  return (
    <section className="panel timeline-panel" aria-labelledby="timeline-title">
      <div className="panel__header">
        <div>
          <p className="panel__kicker">Production Plan</p>
          <h2 id="timeline-title">Arrangement Overview</h2>
        </div>
      </div>

      <div className="arrangement-overview">
        <div className="arrangement-overview__labels" aria-hidden="true" />
        <div className="bar-ruler" aria-label="Bar markers">
          {barMarkers.map((bar) => (
            <span key={bar} style={{ "--bar-position": `${getBarPosition(bar, blueprint.project.total_bars)}%` }}>
              <strong>Bar {bar}</strong>
              <em>{formatBarTime(bar, blueprint.project.bpm)}</em>
            </span>
          ))}
        </div>

        <div className="arrangement-overview__labels" aria-hidden="true" />
        <div className="section-locators" style={{ gridTemplateColumns: sectionColumns }}>
          {sections.map((section) => (
            <div className="section-locator" key={section.id}>
              <strong>{section.name}</strong>
              <span>
                {section.start_bar}-{section.end_bar}
              </span>
            </div>
          ))}
        </div>

        {tracks.map((track) => {
          const isExpanded = expandedLanes.has(track.id);
          const eventMarkers = getEventMarkers(
            track,
            sections,
            blueprint.project.total_bars,
            eventMarkersSource
          );

          return (
            <div className="planning-lane" key={track.id}>
              <button
                className="planning-lane__summary"
                type="button"
                aria-expanded={isExpanded}
                onClick={() => toggleLane(track.id)}
              >
                <span className="planning-lane__name">{track.name}</span>
                <span
                  className={`intent-strip intent-strip--${track.name.toLowerCase()}`}
                  style={{ gridTemplateColumns: sectionColumns }}
                  aria-hidden="true"
                >
                  {sections.map((section) => {
                    const isActive = (track.active_sections ?? []).includes(section.id);
                    const energy = track.energy_profile?.[section.id] ?? 0;

                    return (
                      <span
                        className={isActive ? "intent-strip__section is-active" : "intent-strip__section"}
                        key={`${track.id}-${section.id}`}
                        style={{ "--energy": energy / 100 }}
                      />
                    );
                  })}
                  {eventMarkers.map((event) => (
                    <span
                      className="event-marker"
                      key={event.id}
                      style={{ "--event-position": `${event.position}%` }}
                      title={event.label}
                      aria-label={event.label}
                    >
                      ▲
                    </span>
                  ))}
                </span>
                <span className="planning-lane__chevron">{isExpanded ? "Collapse" : "Expand"}</span>
              </button>

              {isExpanded ? (
                <div className="intent-card">
                  <p className="intent-card__summary">{behaviorCopy[track.name] ?? track.role}</p>
                  <div className="intent-card__chips">
                    <span className="intent-chip intent-chip--suggested">
                      <strong>{getSuggestedSoundLabel(track.name)}</strong>
                      <span className="intent-chip__value">{getSuggestedSound(track)}</span>
                    </span>
                    {getIntentChips(track).map(([label, value]) => (
                      <span className="intent-chip" key={`${track.id}-${label}`}>
                        <strong>{label}</strong>
                        <span className="intent-chip__value">{value}</span>
                      </span>
                    ))}
                    <span className="intent-chip intent-chip--density">
                      <strong>Density</strong>
                      <span className="intent-chip__value intent-chip__value--badges">
                        {getDensitySummary(track).map(([label, value]) => (
                          <em className={`density-badge density-badge--${value.toLowerCase()}`} key={label}>
                            {label} {value === "Medium" ? "MED" : value.toUpperCase()}
                          </em>
                        ))}
                      </span>
                    </span>
                  </div>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default ArrangementTimeline;
