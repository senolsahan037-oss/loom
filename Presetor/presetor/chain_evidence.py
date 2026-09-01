"""Kullanicinin kendi projelerinden olculmus cihaz zincirleri.

Bu modul oneri uretmez, sayar. Veri kaynagi
`scripts/extract_device_chains.py` ile taranmis 840 track; bir rol icin yeterli
gozlem yoksa oneri DONMEZ (None) -- zayif bir tahmin, tahmin olmamasindan
kotudur.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "measured_device_chains.json"
# Olculmus veri kullanicinin kendi projelerinden gelir, kisiseldir, depoda
# yayinlanmaz. Yoksa sentetik fixture kullanilir -- ama hangisinin
# kullanildigi HER ZAMAN raporlanir: fixture'dan cikan bir oneri kullanici
# hakkinda hicbir sey soylemez.
FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "fixture_device_chains.json"


def active_data_path() -> Path:
    return DATA_PATH if DATA_PATH.exists() else FIXTURE_PATH


def data_source() -> str:
    return "measured" if DATA_PATH.exists() else "synthetic_fixture"
# Bir cihazin oneriye girmesi icin o roldeki track'lerin en az bu kadarinda
# gorulmesi gerekir. Olculen veride EQ Eight cogu rolde %80+ cikiyor, yani
# esik gercek aliskanliklari eliyor degil, tek seferlikleri eliyor.
PRESENCE_THRESHOLD = 0.40
# Bir rol icin toplam bu kadar veri yoksa o rol hakkinda konusulmaz.
MIN_ROLE_SAMPLE = 10


@dataclass(frozen=True)
class DeviceEvidence:
    device: str
    presence: float       # bu rolde kac track'te goruldugu (0-1)
    occurrences: int
    median_position: float


@dataclass(frozen=True)
class ChainRecommendation:
    role: str
    chain: tuple[str, ...]
    devices: tuple[DeviceEvidence, ...]
    role_sample: int


def load_tracks(data_path: Path | None = None) -> list[dict]:
    path = data_path or active_data_path()
    if not path.exists():
        raise FileNotFoundError(
            f"{path} yok. Olculmus veri: python3 scripts/extract_device_chains.py --out {DATA_PATH}"
            f"  |  Fixture: python3 scripts/build_fixtures.py"
        )
    return json.loads(path.read_text(encoding="utf-8"))["tracks"]


def aggregate(tracks: list[dict]) -> dict[str, Counter]:
    by_role: dict[str, Counter] = defaultdict(Counter)
    for row in tracks:
        chain = tuple(row.get("chain") or ())
        if chain:
            by_role[row["role"]][chain] += 1
    return by_role


def device_usage(tracks: list[dict]) -> Counter:
    return Counter(device for row in tracks for device in (row.get("chain") or ()))


def chains_for_role(role: str, tracks: list[dict] | None = None) -> list[tuple[tuple[str, ...], int]]:
    rows = tracks if tracks is not None else load_tracks()
    return aggregate(rows).get(role, Counter()).most_common()


def recommend(role: str, tracks: list[dict] | None = None) -> ChainRecommendation | None:
    """Bir rol icin kanita dayali cihaz zinciri.

    Tam zincir dizisi neredeyse hic tekrarlamiyor (en sik dizi bile rolun
    ancak %5-19'u), o yuzden dizi degil CIHAZ VARLIGI sayilir: bu rolde
    track'lerin en az PRESENCE_THRESHOLD'unda gorulen cihazlar alinir ve
    gorulduklerileri ortalama konuma gore siralanir. Boylece oneri, gercekten
    tekrar eden aliskanlik olur; tek seferlik bir dizi degil.
    """
    rows = tracks if tracks is not None else load_tracks()
    role_chains = [tuple(row["chain"]) for row in rows if row["role"] == role and row.get("chain")]
    if len(role_chains) < MIN_ROLE_SAMPLE:
        return None

    sample = len(role_chains)
    presence = Counter()
    positions: dict[str, list[float]] = defaultdict(list)
    for chain in role_chains:
        for device in set(chain):
            presence[device] += 1
        for index, device in enumerate(chain):
            # Zincir uzunluklari farkli, mutlak indeks karsilastirilamaz.
            positions[device].append(index / max(1, len(chain) - 1) if len(chain) > 1 else 0.0)

    chosen = []
    for device, count in presence.items():
        share = count / sample
        if share < PRESENCE_THRESHOLD:
            continue
        ordered = sorted(positions[device])
        median = ordered[len(ordered) // 2]
        chosen.append(DeviceEvidence(device=device, presence=round(share, 3), occurrences=count, median_position=round(median, 3)))

    if not chosen:
        return None
    chosen.sort(key=lambda item: (item.median_position, -item.presence))
    return ChainRecommendation(
        role=role,
        chain=tuple(item.device for item in chosen),
        devices=tuple(chosen),
        role_sample=sample,
    )


def known_roles(tracks: list[dict] | None = None) -> list[str]:
    rows = tracks if tracks is not None else load_tracks()
    return sorted(aggregate(rows))


def summary(tracks: list[dict] | None = None) -> dict:
    rows = tracks if tracks is not None else load_tracks()
    recommendations = {}
    for role in known_roles(rows):
        result = recommend(role, rows)
        if result:
            recommendations[role] = {
                "chain": list(result.chain),
                "role_sample": result.role_sample,
                "devices": [
                    {"device": item.device, "presence": item.presence, "occurrences": item.occurrences}
                    for item in result.devices
                ],
            }
    return {
        "data_source": data_source(),
        "tracks_scanned": len(rows),
        "tracks_with_devices": sum(1 for row in rows if row.get("chain")),
        "presence_threshold": PRESENCE_THRESHOLD,
        "top_devices": device_usage(rows).most_common(15),
        "roles_with_recommendation": recommendations,
        "roles_without_enough_evidence": [role for role in known_roles(rows) if role not in recommendations],
    }
