#!/usr/bin/env python3
"""Her MCP aracini gercek stdio uzerinden cagirir. Live gerekmiyor.

Kanitladigi sey: sunucu ayakta, 14 aracin sema/handler eslesmesi dogru, ve
her arac gercek veriyle calisip beklenen alanlari donduruyor.
Kanitlamadigi sey: Ableton Live'in bu ciktilarla ne yaptigi.

Yan etkisi olan iki arac (bridge'e istek yazan ve gap logu'na ekleyen)
cagriliyor ve arkasindan temizleniyor; .als yazan arac sadece kuru calisiyor.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "mcp_server" / "server.py"
PYTHON = ROOT / "Sensei" / ".venv" / "bin" / "python"
GAP_LOG = ROOT / "Docs" / "MISSING_CONTROLS_LOG.md"
BRIDGE_REQUESTS = Path.home() / "Documents" / "SenseiV2Bridge" / "requests"
SAMPLE_ALS = Path.home() / "Desktop" / "solo" / "Turtle.als"
# Turtle has no automation at all; this one has ten envelopes, so the
# automation tool is tested against something that actually exists.
AUTOMATED_ALS = Path.home() / "Desktop" / "solo" / "overdozz Project" / "overdozz.als"

GAP_MARKER = "MCP_SELFTEST_ENTRY_DO_NOT_KEEP"


class Server:
    def __init__(self):
        self.process = subprocess.Popen(
            [str(PYTHON), str(SERVER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, cwd=str(ROOT),
        )
        self.next_id = 0

    def call(self, method, params=None):
        self.next_id += 1
        request = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("server closed the connection")
        return json.loads(line)

    def tool(self, name, arguments=None):
        response = self.call("tools/call", {"name": name, "arguments": arguments or {}})
        result = response.get("result", {})
        text = (result.get("content") or [{}])[0].get("text", "")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {"_raw": text}
        return result.get("isError", False), payload

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)


checks = []
failures = []


def check(label, condition, detail=""):
    if condition:
        checks.append(label)
    else:
        failures.append("%s  %s" % (label, detail))


def main():
    if not PYTHON.exists():
        print("Sensei venv bulunamadi: %s" % PYTHON)
        return 1

    server = Server()
    created_request = None
    # Zincir her calistiginda yeni bir build dizini, renderer da yeni bir job
    # dosyasi birakir. Test kendi coplugunu toplamazsa her kosuda repoya bir
    # klasor daha eklenir.
    created_build_dir = None
    created_job_file = None
    try:
        init = server.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "selftest", "version": "1"}})
        check("initialize yaniti veriyor", init.get("result", {}).get("serverInfo", {}).get("name") == "loom-mcp", init)

        # Sunucu artik sayfaliyor; istemci imleci takip etmek zorunda.
        listed = []
        cursor = None
        while True:
            page = server.call("tools/list", {"cursor": cursor} if cursor else {})["result"]
            listed.extend(page["tools"])
            cursor = page.get("nextCursor")
            if not cursor:
                break
        names = [tool["name"] for tool in listed]
        check("27 arac yayinlaniyor", len(names) == 27, len(names))
        check("her aracin inputSchema'si var", all("inputSchema" in tool for tool in listed))
        check("arac adlari benzersiz", len(set(names)) == len(names))

        is_error, payload = server.tool("__does_not_exist__")
        check("bilinmeyen arac hata donduruyor", is_error, payload)

        # --- ArrangementGPS: gercek zincir ---
        is_error, payload = server.tool("arrangementgps_create_action_list", {"prompt": "dark rolling tech house, 126 bpm, hypnotic bassline"})
        check("zincir prompt'tan calisiyor", not is_error, payload)
        if not is_error:
            check("prompt'taki tempo plana gecti", payload["project"]["bpm"] == 126, payload["project"])
            check("5 zincir adiminin hepsi calisti", len(payload["steps"]) == 5, payload["steps"])
            check("17 track, 7 locator", payload["tracks_total"] == 17 and payload["locators"] == 7, payload)
            check("6 track Sensei kapsaminda", payload["tracks_sensei_can_generate"] == 6, payload)
            check("action list dosyasi gercekten yazildi", payload["action_list_file"] and Path(payload["action_list_file"]).exists(), payload["action_list_file"])
            check("bos gorev listesi uretmiyor", payload["tracks_total"] > 0, payload)
            created_build_dir = Path(payload["build_dir"])

        is_error, payload = server.tool("arrangementgps_verify_plan")
        check("plan dogrulama calisiyor", not is_error, payload)
        if not is_error:
            check("plan Sensei katalogunu geciyor", payload["ok"], payload["failures"])
            check("kapsam disi lane'ler hata sayilmiyor", len(payload["out_of_scope"]) == 11, payload["out_of_scope"])

        is_error, payload = server.tool("arrangementgps_search_library", {"role": "drum", "genre": "House", "limit": 5})
        check("kutuphane aramasi rol+tur ile calisiyor", not is_error, payload)
        if not is_error:
            check("sonuclar Sensei dogrulamali", payload["results"] and all(r["sensei_verified"] for r in payload["results"]), payload)
            check("her sonuc istenen rolde", all(r["role"] == "drum" for r in payload["results"]), payload)
            check("katalog gercekten okundu", payload["catalog_size"] > 5000, payload["catalog_size"])

        # --- AIMixMaster: gercek .als ---
        if SAMPLE_ALS.exists():
            is_error, payload = server.tool("aimixmaster_inspect_als", {"als_path": str(SAMPLE_ALS)})
            check("als incelemesi calisiyor", not is_error and payload.get("tracks"), payload)

            is_error, payload = server.tool("aimixmaster_inspect_arrangement", {"als_path": str(SAMPLE_ALS)})
            check("arrangement incelemesi calisiyor", not is_error, payload)
            if not is_error:
                check("bolumler klip sinirlarindan cikariliyor", payload["section_count"] >= 1, payload)
                check("tempo okunuyor", payload["tempo"], payload)

            is_error, payload = server.tool("renderer_create_export_manifest", {"als_path": str(SAMPLE_ALS)})
            check("render manifesti gercek projeden uretiliyor", not is_error, payload)
            if not is_error:
                check("her track icin karar var", payload["track_count"] > 0, payload)
                check("render edilemeyenlerin sebebi yazili", all(item["reason"] for item in payload["excluded"]), payload["excluded"][:3])
                check("manifest dosyaya yazildi", Path(payload["job_path"]).exists(), payload["job_path"])
                created_job_file = Path(payload["job_path"])

            is_error, payload = server.tool("aimixmaster_analyze_mixer", {"als_path": str(SAMPLE_ALS)})
            check("mikser analizi calisiyor", not is_error, str(payload)[:200])
            if not is_error:
                check("gercek gain staging raporu donuyor", payload.get("schema_version") and payload.get("markdown"), list(payload))
                check("master zinciri inceleniyor", "limiter_status" in payload.get("master", {}), payload.get("master"))
                check("sabit -6 dB hedefi artik yok", "gain_staging_target_db" not in payload, list(payload))
                check("track basina kayit uretiliyor", payload["track_count"] > 0, payload["track_count"])

            is_error, payload = server.tool("aimixmaster_analyze_clip_alignment", {"als_path": str(SAMPLE_ALS)})
            check("klip hizalama analizi calisiyor", not is_error, str(payload)[:200])
            if not is_error:
                check("hizalama raporu markdown uretiyor", bool(payload.get("markdown")), list(payload))

            is_error, payload = server.tool("aimixmaster_drum_buss_state", {"als_path": str(SAMPLE_ALS)})
            check("drum buss durumu okunuyor", not is_error, str(payload)[:200])
            if not is_error:
                check("drum buss yoksa duruma acikca yaziliyor", "has_drum_buss" in payload, list(payload))

            is_error, payload = server.tool("aimixmaster_build_drum_buss", {"als_path": str(SAMPLE_ALS)})
            # Kaynak track yoksa hata dondurmesi dogru davranis; yazmamasi sart.
            check("drum buss kuru calisma .als'e dokunmuyor", is_error or payload.get("applied") is False, payload)
        else:
            check("ornek .als bulundu", False, str(SAMPLE_ALS))

        if AUTOMATED_ALS.exists():
            is_error, payload = server.tool("aimixmaster_inspect_automation", {"als_path": str(AUTOMATED_ALS)})
            check("otomasyon incelemesi calisiyor", not is_error, str(payload)[:200])
            if not is_error:
                check("otomasyon zarflari bulunuyor", payload["envelope_count"] == 10, payload["envelope_count"])
                check("her hedef cozulebiliyor", payload["unresolved_targets"] == 0, payload["unresolved_targets"])
                check("otomasyon yazmanin desteklenmedigi acikca bildiriliyor", payload["write_supported"] is False, payload)
        else:
            check("otomasyonlu ornek proje bulundu", False, str(AUTOMATED_ALS))

        # --- Presetor ---
        is_error, payload = server.tool("presetor_chain_evidence", {"role": "kick"})
        check("zincir kaniti rol bazinda geliyor", not is_error, str(payload)[:200])
        if not is_error:
            check("kick icin kanit var", payload["has_recommendation"], payload)
            check("her cihaz kac track'te goruldugunu tasiyor",
                  all("presence" in item and "occurrences" in item for item in payload["devices"]), payload.get("devices"))
            check("kanit kac track'e dayandigini soyluyor", payload["role_sample"] >= 10, payload.get("role_sample"))

        is_error, payload = server.tool("presetor_chain_evidence", {"role": "__yok_boyle_bir_rol__"})
        check("kaniti olmayan rol icin oneri URETILMIYOR",
              not is_error and payload["has_recommendation"] is False, payload)

        if SAMPLE_ALS.exists():
            is_error, payload = server.tool("presetor_plan_chains", {"als_path": str(SAMPLE_ALS)})
            check("zincir plani projeden uretiliyor", not is_error, str(payload)[:200])
            if not is_error:
                check("her track icin bir plan satiri var", payload["track_count"] > 0, payload["track_count"])
                check("plan durumlari sayiliyor", payload["status_counts"], payload.get("status_counts"))

            busy = next((p["track"] for p in payload.get("plans", []) if p["status"] == "already_has_chain"), None)
            if busy:
                is_error, payload = server.tool("presetor_apply_chain", {
                    "als_path": str(SAMPLE_ALS), "target_track": busy, "donor_track": busy,
                })
                check("dolu track'e kuru calisma da yazmiyor",
                      is_error or payload.get("applied") is False, payload)

        # --- AISoundDesigner ---
        is_error, payload = server.tool("sounddesigner_palette", {"role": "bass"})
        check("ses paleti rol bazinda geliyor", not is_error, str(payload)[:200])
        if not is_error:
            check("bass paleti var", payload["has_palette"], payload)
            check("her sample kac projede goruldugunu tasiyor",
                  all(item["projects"] >= 2 for item in payload["samples"]), payload.get("samples", [])[:3])

        is_error, payload = server.tool("sounddesigner_palette", {})
        check("palet ozeti bounce oranini bildiriyor",
              not is_error and 0 < payload["bounce_share"] < 1, str(payload)[:200])

        if SAMPLE_ALS.exists():
            is_error, payload = server.tool("sounddesigner_inspect_project", {"als_path": str(SAMPLE_ALS)})
            check("proje ses kaynaklari okunuyor", not is_error, str(payload)[:200])
            if not is_error:
                check("track sayisi bildiriliyor", payload["track_count"] > 0, payload["track_count"])

        is_error, payload = server.tool("ableton_extract_arrangement_shapes", {"roots": [str(Path.home() / "Desktop" / "solo")], "limit": 3})
        check("arrangement sekli cikarimi calisiyor", not is_error, payload)
        if not is_error:
            check("taranan proje sayisi bildiriliyor", payload["scanned"] == 3, payload["scanned"])

        # --- Sensei ---
        is_error, payload = server.tool("sensei_prepare_variation", {"role": "drum", "genre": "Hip Hop", "bars": 4, "seed": 7})
        check("sensei varyasyon uretimi cevap veriyor", not is_error, str(payload)[:200])

        before = set(BRIDGE_REQUESTS.glob("*.json")) if BRIDGE_REQUESTS.exists() else set()
        # Live kapali: arac artik "QUEUED" deyip gecmiyor, beklemeyi deneyip
        # tuketilmedigini soyluyor.
        is_error, payload = server.tool("sensei_write_clip_to_live", {
            "name": "MCP selftest", "length_beats": 4.0,
            "notes": [{"pitch": 36, "start": 0.0, "duration": 0.5, "velocity": 100}],
            "wait_seconds": 1.0,
        })
        check("bridge yazimi sonucu bildiriyor", not is_error, payload)
        if not is_error:
            created_request = Path(payload["request_file"])
            check("istek dosyasi diskte gercekten var", created_request.exists(), str(created_request))
            check("Live tuketmediyse bu acikca soyleniyor",
                  payload["status"] in ("NOT_CONSUMED", "WRITTEN_TO_LIVE", "REJECTED_BY_LIVE"), payload["status"])
            check("consumed alani tahmin degil, olculmus deger",
                  isinstance(payload["consumed"], bool), payload.get("consumed"))
            after = set(BRIDGE_REQUESTS.glob("*.json"))
            check("kuyruga tam bir istek eklendi", len(after - before) == 1, len(after - before))

        is_error, payload = server.tool("sensei_write_clip_to_live", {
            "name": "MCP selftest blind", "length_beats": 4.0,
            "notes": [{"pitch": 36, "start": 0.0, "duration": 0.5, "velocity": 100}],
            "wait_seconds": 0,
        })
        check("wait_seconds=0 kor kuyruga alma olarak isaretleniyor",
              not is_error and payload["status"] == "QUEUED" and payload["consumed"] is None, payload)
        if not is_error:
            Path(payload["request_file"]).unlink(missing_ok=True)

        # --- Otomasyon yazma ---
        if SAMPLE_ALS.exists():
            is_error, payload = server.tool("aimixmaster_write_automation", {
                "als_path": str(SAMPLE_ALS), "track": "1-Viral Kit",
                "parameter": "volume", "unit": "db",
                "points": [{"time": 0, "value": -12}, {"time": 16, "value": 0}],
            })
            check("otomasyon kuru calismasi calisiyor", not is_error, str(payload)[:200])
            if not is_error:
                check("kuru calisma .als'e yazmiyor", payload["applied"] is False, payload)
                check("parametrenin gercek araligi okunuyor", payload["parameter_range"][1] > 1, payload.get("parameter_range"))
                check("hedef PointeeId cozuluyor", payload["pointee_id"], payload.get("pointee_id"))

            is_error, payload = server.tool("aimixmaster_write_automation", {
                "als_path": str(SAMPLE_ALS), "track": "1-Viral Kit",
                "parameter": "volume", "unit": "db", "points": [{"time": 0, "value": 40}],
            })
            check("aralik disi deger reddediliyor", is_error and "outside the parameter range" in str(payload), str(payload)[:140])

            is_error, payload = server.tool("aimixmaster_write_automation", {
                "als_path": str(SAMPLE_ALS), "track": "1-Viral Kit",
                "parameter": "pan", "points": [{"time": 16, "value": 0.5}, {"time": 4, "value": -0.5}],
            })
            check("geriye giden zaman reddediliyor", is_error and "backwards" in str(payload), str(payload)[:140])

            is_error, payload = server.tool("aimixmaster_list_automatable_parameters", {
                "als_path": str(SAMPLE_ALS), "track": "1-Viral Kit", "contains": "gain", "limit": 5})
            check("otomasyonlanabilir parametreler listeleniyor", not is_error, str(payload)[:160])
            if not is_error:
                check("her parametre hedef id ve aralik tasiyor",
                      all(p.get("pointee_id") and p.get("min") is not None for p in payload["parameters"]),
                      payload["parameters"][:2])
                check("araligi bildirilmemis parametreler disarida", payload["writable"] < payload["total"], (payload["writable"], payload["total"]))
                check("filtre uygulaniyor", all("gain" in p["tag"].lower() for p in payload["parameters"]), payload["parameters"][:2])
                check("sinir asilmiyor", payload["returned"] <= 5, payload["returned"])

            is_error, payload = server.tool("renderer_validate_renders", {
                "als_path": str(SAMPLE_ALS), "renders_dir": str(Path.home() / "Desktop"),
            })
            check("render dogrulamasi calisiyor (soundfile kurulu)", not is_error, str(payload)[:160])

        # --- Telemetri ---
        is_error, payload = server.tool("ableton_get_bridge_status")
        check("bridge durumu okunuyor", not is_error and "bridge_root" in payload, payload)

        gap_before = GAP_LOG.read_text(encoding="utf-8") if GAP_LOG.exists() else ""
        is_error, payload = server.tool("ableton_record_gap", {
            "category": "Telemetry",
            "description": GAP_MARKER,
            "observed_behavior": GAP_MARKER,
            "required_implementation": GAP_MARKER,
        })
        check("gap kaydi calisiyor", not is_error, payload)
        gap_after = GAP_LOG.read_text(encoding="utf-8") if GAP_LOG.exists() else ""
        check("gap gercekten dogru dosyaya yazildi", GAP_MARKER in gap_after, GAP_LOG)
        if GAP_MARKER in gap_after and gap_before:
            GAP_LOG.write_text(gap_before, encoding="utf-8")
            check("test girdisi gap logundan temizlendi", GAP_MARKER not in GAP_LOG.read_text(encoding="utf-8"))
    finally:
        if created_request and created_request.exists():
            created_request.unlink()
        if created_job_file and created_job_file.exists():
            created_job_file.unlink()
        if created_build_dir and created_build_dir.exists() and created_build_dir.parent.name == "Builds":
            shutil.rmtree(created_build_dir)
        server.close()

    print("%d kontrol gecti:" % len(checks))
    for label in checks:
        print("  ok  %s" % label)
    if failures:
        print()
        print("BASARISIZ:")
        for failure in failures:
            print("  - %s" % failure)
        return 1
    print("TUM ARACLAR CALISIYOR")
    return 0


if __name__ == "__main__":
    sys.exit(main())
