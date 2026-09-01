#!/usr/bin/env python3
"""Her track'in enstrumaninin Sensei'nin kataloguna gore cozulup cozulmedigini
Live acmadan dogrular.

Kanitladigi sey: plan'daki instrument_family adi Sensei'nin kimlik katalogunda
TEK bir role cozuluyor ve o rol plan'in sensei_role'u ile ayni. Bu, gercek
Live kosusundaki instrument_role_unresolved hatasinin ta kendisidir -- Live'da
patlamadan once burada yakalanir.

Kanitlamadigi sey: preset'in Ableton'da gercekten yuklenebildigi.
"""
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "Sensei/data/genre_identity/ableton_preset_genre_identities.jsonl")
PLAN = os.path.join(ROOT, "ArrangementGPS/engine/output/ableton_session_plan.json")

def load_role_index(catalog_path=CATALOG):
    roles_by_name = collections.defaultdict(set)
    with open(catalog_path) as handle:
        for line in handle:
            entry = json.loads(line)
            roles_by_name[entry["normalized_name"]].add(entry["role"])
    return roles_by_name


def verify_plan(plan_path=PLAN, catalog_path=CATALOG):
    """Plan'daki her track icin enstruman/rol tutarliligini dondurur.

    MCP sunucusu da bunu cagirir; tek dogruluk kaynagi burasi olsun diye
    fonksiyon haline getirildi.
    """
    roles_by_name = load_role_index(catalog_path)
    with open(plan_path) as handle:
        tracks = json.load(handle)["tracks"]

    failures = []
    supported = []
    out_of_scope = []

    for track in tracks:
        name = track["ableton_name"]
        role = track.get("sensei_role")
        family = track.get("instrument_family")

        if role is None:
            out_of_scope.append(name)
            if family:
                failures.append("%s: Sensei rolu yok ama '%s' yuklenmek isteniyor" % (name, family))
            continue

        supported.append({"track": name, "role": role, "instrument_family": family})
        if not family:
            failures.append("%s: rol '%s' ama enstruman secilmemis" % (name, role))
            continue
        resolved = roles_by_name.get(family.lower())
        if not resolved:
            failures.append("%s: '%s' katalogda yok" % (name, family))
        elif len(resolved) > 1:
            failures.append("%s: '%s' birden fazla role cozuluyor %s" % (name, family, sorted(resolved)))
        elif role not in resolved:
            failures.append("%s: rol '%s' ama '%s' katalogda '%s'" % (name, role, family, sorted(resolved)[0]))

    return {
        "catalog_size": len(roles_by_name),
        "supported": supported,
        "out_of_scope": out_of_scope,
        "failures": failures,
        "ok": not failures,
    }


def main():
    # Sensei'nin kimlik katalogu kullanicinin KENDI Ableton kurulumundan
    # uretilir ve depoda yayinlanmaz. Temiz bir klonda yoktur; bu bir
    # basarisizlik degil, uretilmemis bir on kosuldur.
    if not os.path.exists(CATALOG):
        print("ATLANDI: Sensei kimlik katalogu yok.\n  %s" % CATALOG)
        print("  Uretmek icin Sensei/ableton/genre_identity.py calistirin.")
        return 0
    if not os.path.exists(PLAN):
        print("ATLANDI: session plan yok. Once ArrangementGPS zincirini calistirin.")
        return 0

    result = verify_plan()
    print("Sensei'nin urettigi roller (drum/bass/chord): %d track" % len(result["supported"]))
    for item in result["supported"]:
        print("  ok  %-16s %-8s %s" % (item["track"], item["role"], item["instrument_family"]))
    print()
    print("Sensei'nin rolu olmayan lane'ler (kapsam disi, hata degil): %d track" % len(result["out_of_scope"]))
    print("  " + ", ".join(result["out_of_scope"]))

    if result["failures"]:
        print()
        print("BASARISIZ:")
        for failure in result["failures"]:
            print("  - " + failure)
        return 1

    print()
    print("%d/%d desteklenen track tek role cozuluyor" % (len(result["supported"]), len(result["supported"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
