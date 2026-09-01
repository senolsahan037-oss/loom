# core/genre_blend.py
import re
import copy
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

@dataclass
class GenreBlend:
    primary_genre: str
    secondary_genre: Optional[str] = None
    primary_weight: float = 1.0
    secondary_weight: float = 0.0
    role_overrides: Dict[str, str] = field(default_factory=dict)
    confidence: float = 1.0
    raw_prompt: str = ""

GENRE_STYLE_PROFILES = {
    "boom_bap": {
        "kick_density": 0.3,
        "hat_density": 0.4,
        "ghost_snare_ratio": 0.1,
        "swing_ticks": 12,
        "velocity_mean": 90,
        "velocity_std": 10
    },
    "trap": {
        "kick_density": 0.7,
        "hat_density": 0.8,
        "ghost_snare_ratio": 0.3,
        "swing_ticks": 4,
        "velocity_mean": 100,
        "velocity_std": 15
    },
    "house": {
        "kick_density": 0.5,
        "hat_density": 0.6,
        "ghost_snare_ratio": 0.0,
        "swing_ticks": 8,
        "velocity_mean": 95,
        "velocity_std": 8
    },
    "drill": {
        "kick_density": 0.8,
        "hat_density": 0.9,
        "ghost_snare_ratio": 0.5,
        "swing_ticks": 2,
        "velocity_mean": 105,
        "velocity_std": 12
    }
}

def normalize_genre(genre_str: str) -> str:
    """
    Normalizes variations of genre names to canonical strings.
    """
    g = (genre_str or "").strip().lower()
    # Remove punctuation
    g = re.sub(r"[^\w\s]", "", g)
    
    if g in {"boom bap", "boombap", "boom_bap", "rap"}:
        return "boom_bap"
    elif g in {"trap"}:
        return "trap"
    elif g in {"house", "club", "dance", "techno"}:
        return "house"
    elif g in {"drill"}:
        return "drill"
    return g

def map_role_name(role_str: str) -> str:
    """
    Maps Turkish/informal role terms to canonical drum roles.
    """
    r = role_str.strip().lower()
    if r in {"kick", "kik", "bas", "bass"}:
        return "kick"
    elif r in {"snare", "trampet", "clap", "alkış"}:
        return "snare"
    elif r in {"hat", "hatler", "hihat", "hi-hat", "hats"}:
        return "closed_hat"
    return r

def parse_genre_blend(prompt: str, current_genre: Optional[str] = None) -> GenreBlend:
    """
    Parses a natural language prompt to identify primary/secondary genres, weights,
    and role-specific overrides.
    """
    raw_prompt = prompt
    p = prompt.lower().strip()

    # Default fallback values
    primary_genre = normalize_genre(current_genre) if current_genre else "boom_bap"
    secondary_genre = None
    primary_weight = 1.0
    secondary_weight = 0.0
    role_overrides = {}

    # List of known genres for regex matching
    genres_list = ["boom bap", "boombap", "boom_bap", "trap", "house", "drill", "club", "dance"]
    genres_pattern = "|".join(genres_list)

    # 1. Check percentage blend: "%70 boom bap %30 trap" or similar
    pct_match = re.findall(r"(?:%\s*(\d+)|\b(\d+)\s*%)\s*(" + genres_pattern + r")", p)
    if len(pct_match) >= 2:
        # Resolve weights
        w1_str = pct_match[0][0] or pct_match[0][1]
        g1_str = pct_match[0][2]
        w2_str = pct_match[1][0] or pct_match[1][1]
        g2_str = pct_match[1][2]
        
        w1 = float(w1_str) / 100.0
        w2 = float(w2_str) / 100.0
        g1 = normalize_genre(g1_str)
        g2 = normalize_genre(g2_str)

        if w1 >= w2:
            primary_genre, primary_weight = g1, w1
            secondary_genre, secondary_weight = g2, w2
        else:
            primary_genre, primary_weight = g2, w2
            secondary_genre, secondary_weight = g1, w1
        
        return GenreBlend(primary_genre, secondary_genre, primary_weight, secondary_weight, role_overrides, confidence=0.95, raw_prompt=raw_prompt)

    # Helper function to find a genre in a matched group
    def extract_genre(text: str) -> Optional[str]:
        for g in genres_list:
            if g in text:
                return normalize_genre(g)
        return None

    # 2. X hissi olan Y / X pulse'ı olan Y / X enerjisi olan Y
    match = re.search(r"\b(" + genres_pattern + r")\s+(hissi|pulse|enerjisi|tınısı|rengi|havası|groove)[’'\w]*\s+olan\s+([\w\s]+)\b", p)
    if match:
        sec = extract_genre(match.group(1))
        prim = extract_genre(match.group(3))
        if prim and sec:
            return GenreBlend(prim, sec, 0.7, 0.3, role_overrides, confidence=0.9, raw_prompt=raw_prompt)

    # 3. Y kalsın ama X gibi aksın / Y omurgayı koru, X hareketi ekle
    match = re.search(r"\b(" + genres_pattern + r")\s+omurgayı\s+koru,\s+(" + genres_pattern + r")\s+hareketi\s+ekle\b", p)
    if match:
        prim = extract_genre(match.group(1))
        sec = extract_genre(match.group(2))
        if prim and sec:
            return GenreBlend(prim, sec, 0.75, 0.25, role_overrides, confidence=0.9, raw_prompt=raw_prompt)

    # 4. Y ama biraz X
    match = re.search(r"\b(" + genres_pattern + r")\s+ama\s+(?:biraz|daha|ekstra)\s+(" + genres_pattern + r")\b", p)
    if match:
        prim = extract_genre(match.group(1))
        sec = extract_genre(match.group(2))
        if prim and sec:
            return GenreBlend(prim, sec, 0.7, 0.3, role_overrides, confidence=0.85, raw_prompt=raw_prompt)

    # 5. Ana yapı Y, üst hareket X
    match = re.search(r"ana\s+yapı\s+(" + genres_pattern + r")\b.*üst\s+hareket\s+(" + genres_pattern + r")\b", p)
    if match:
        prim = extract_genre(match.group(1))
        sec = extract_genre(match.group(2))
        if prim and sec:
            return GenreBlend(prim, sec, 0.7, 0.3, role_overrides, confidence=0.9, raw_prompt=raw_prompt)

    # 6. Kick Y gibi, hi-hat X gibi olsun (Role levels)
    role_matches = re.findall(r"\b(kick|hi-hat|hihat|hatler|snare|trampet)\s+(" + genres_pattern + r")\s+gibi\b", p)
    if role_matches:
        for rm in role_matches:
            role = map_role_name(rm[0])
            gen = normalize_genre(rm[1])
            role_overrides[role] = gen
        # Resolve primary/secondary based on roles
        if role_overrides:
            genres_in_overrides = list(role_overrides.values())
            primary_genre = genres_in_overrides[0]
            if len(set(genres_in_overrides)) > 1:
                secondary_genre = genres_in_overrides[1]
                primary_weight = 0.6
                secondary_weight = 0.4
            return GenreBlend(primary_genre, secondary_genre, primary_weight, secondary_weight, role_overrides, confidence=0.9, raw_prompt=raw_prompt)

    # 7. Bunu X groove'a/tarzına yaklaştır
    match = re.search(r"\b(" + genres_pattern + r")\b.*yaklaştır\b", p)
    if match:
        sec = extract_genre(match.group(1))
        if current_genre:
            return GenreBlend(normalize_genre(current_genre), sec, 0.7, 0.3, role_overrides, confidence=0.8, raw_prompt=raw_prompt)
        elif sec:
            return GenreBlend(sec, None, 1.0, 0.0, role_overrides, confidence=0.8, raw_prompt=raw_prompt)

    # 8. Daha X renkli / X gibi aksın
    match = re.search(r"daha\s+(" + genres_pattern + r")\s+(renkli|tarzı|havası|vibe)", p)
    if match:
        sec = extract_genre(match.group(1))
        if current_genre:
            return GenreBlend(normalize_genre(current_genre), sec, 0.7, 0.3, role_overrides, confidence=0.8, raw_prompt=raw_prompt)

    # 9. Generic single genre detect
    for g in genres_list:
        if re.search(r"\b" + re.escape(g) + r"\b", p):
            primary_genre = normalize_genre(g)
            break

    # Look for role override keywords like "hatler biraz trap gibi aksın"
    for r_word in ["hatler", "hihat", "hi-hat", "kick", "snare"]:
        if r_word in p:
            # Find the closest genre after this word
            sub_text = p[p.find(r_word):]
            for g in genres_list:
                if re.search(r"\b" + re.escape(g) + r"\b.*gibi", sub_text):
                    role_overrides[map_role_name(r_word)] = normalize_genre(g)
                    secondary_genre = normalize_genre(g)
                    primary_weight = 0.7
                    secondary_weight = 0.3
                    break

    return GenreBlend(primary_genre, secondary_genre, primary_weight, secondary_weight, role_overrides, confidence=0.7, raw_prompt=raw_prompt)

def merge_style_profiles(primary_profile: Dict[str, Any], secondary_profile: Dict[str, Any], primary_weight: float, secondary_weight: float) -> Dict[str, Any]:
    """
    Blends two numeric style profiles using linear interpolation.
    """
    merged = {}
    all_keys = set(primary_profile.keys()).union(set(secondary_profile.keys()))

    for key in all_keys:
        val_prim = primary_profile.get(key)
        val_sec = secondary_profile.get(key)

        if isinstance(val_prim, (int, float)) and isinstance(val_sec, (int, float)):
            # Linearly interpolate numeric features
            merged[key] = (primary_weight * val_prim) + (secondary_weight * val_sec)
        else:
            # Fallback to primary for non-numeric features
            merged[key] = val_prim if val_prim is not None else val_sec

    return merged

def apply_role_overrides(merged_profile: Dict[str, Any], role_overrides: Dict[str, str], genre_profiles: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Applies role-level overrides to the merged profile.
    If a role (e.g. closed_hat) has a specific genre override, its stats are replaced by that genre's stats.
    """
    final_profile = copy.deepcopy(merged_profile)
    
    for role, target_genre in role_overrides.items():
        profile = genre_profiles.get(target_genre)
        if not profile:
            continue
            
        if role == "closed_hat":
            final_profile["hat_density"] = profile.get("hat_density", final_profile["hat_density"])
        elif role == "kick":
            final_profile["kick_density"] = profile.get("kick_density", final_profile["kick_density"])
        elif role == "snare":
            final_profile["ghost_snare_ratio"] = profile.get("ghost_snare_ratio", final_profile["ghost_snare_ratio"])

    return final_profile

def apply_blend_constraints(merged_profile: Dict[str, Any], primary_genre: str, secondary_genre: Optional[str]) -> Dict[str, Any]:
    """
    Applies sanity check constraints to the blended style profile.
    Ensures parameters remain within healthy musical bounds.
    """
    final_profile = copy.deepcopy(merged_profile)
    
    # Enforce minimum boundaries
    final_profile["kick_density"] = max(0.1, min(1.0, final_profile.get("kick_density", 0.5)))
    final_profile["hat_density"] = max(0.1, min(1.0, final_profile.get("hat_density", 0.5)))
    final_profile["ghost_snare_ratio"] = max(0.0, min(1.0, final_profile.get("ghost_snare_ratio", 0.0)))
    
    if "velocity_mean" in final_profile:
        final_profile["velocity_mean"] = max(40, min(120, int(final_profile["velocity_mean"])))
        
    return final_profile
