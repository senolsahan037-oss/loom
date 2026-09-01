import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

@dataclass
class AbletonQueryIntent:
    content_type: str | None = None
    genre: str | None = None
    pack: str | None = None
    tag: str | None = None
    keywords: List[str] = field(default_factory=list)
    role_hint: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content_type": self.content_type,
            "genre": self.genre,
            "pack": self.pack,
            "tag": self.tag,
            "keywords": self.keywords,
            "role_hint": self.role_hint,
        }

def parse_prompt_to_intent(prompt: str) -> AbletonQueryIntent:
    prompt_lower = prompt.lower()
    
    genres = ["boom bap", "trap", "drill", "phonk", "lo-fi", "hip hop", "trip hop", "g-funk", "funk", "jazz", "rock", "house", "techno", "ambient"]
    matched_genre = None
    for g in genres:
        if g in prompt_lower:
            matched_genre = " ".join(word.capitalize() for word in g.split())
            break

    packs_map = {
        "unnatural": "Unnatural Selection",
        "beat tools": "Beat Tools",
        "build and drop": "Build and Drop",
    }
    matched_pack = None
    for pk_key, pk_val in packs_map.items():
        if pk_key in prompt_lower:
            matched_pack = pk_val
            break

    # Determine role_hint
    matched_role = None
    if any(k in prompt_lower for k in ["bass", "sub"]):
        matched_role = "bass"
    elif any(k in prompt_lower for k in ["chord", "pad", "keys", "piano", "rhodes", "harmony"]):
        matched_role = "chords"
    elif any(k in prompt_lower for k in ["drum", "kit", "beat", "groove", "perc", "snare", "kick", "hihat", "cymbal", "tom", "clap"]):
        matched_role = "drums"

    # Determine content_type
    matched_type = None
    if "kit" in prompt_lower:
        matched_type = "kit"
    elif any(k in prompt_lower for k in ["groove", "clip", "midi"]):
        matched_type = "clip"
    elif any(k in prompt_lower for k in ["loop", "wav", "sample", "aif"]):
        matched_type = "audio"

    # Determine tags
    matched_tag = None
    if "expressive" in prompt_lower:
        matched_tag = "Expressive"

    # Extract keywords
    stop_words = {
        "bass", "sub", "chord", "chords", "pad", "pads", "keys", "piano", "rhodes", "harmony",
        "drum", "drums", "kit", "kits", "beat", "beats", "groove", "grooves", "perc", "snare", "kick", "hihat",
        "clip", "clips", "midi", "loop", "loops", "wav", "sample", "samples", "aif", "expressive"
    }
    # Also exclude genres and packs
    for g in genres:
        for w in g.split():
            stop_words.add(w)
    for pk in packs_map.keys():
        for w in pk.split():
            stop_words.add(w)

    words = re.findall(r'\b\w+\b', prompt_lower)
    keywords = [w for w in words if w not in stop_words]

    return AbletonQueryIntent(
        content_type=matched_type,
        genre=matched_genre,
        pack=matched_pack,
        tag=matched_tag,
        keywords=keywords,
        role_hint=matched_role
    )
