#!/usr/bin/env python3
"""Verify each track's instrument against Sensei's catalog, without opening Live.

What it proves: the plan's instrument_family resolves to exactly ONE role in
Sensei's identity catalog, and that role matches the plan's sensei_role. This is
the same instrument_role_unresolved failure a real Live run produces -- caught
here before Live ever sees it.

What it does not prove: that the preset actually loads in Ableton.
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
    """Report instrument/role consistency for every track in the plan.

    The MCP server calls this too; it was made a function so there is a single
    source of truth.
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
    # Sensei's identity catalog is generated from the producer's OWN Ableton
    # install and is never published. It is absent in a clean clone; that is not
    # a failure, it is a precondition that has not been generated yet.
    if not os.path.exists(CATALOG):
        print("SKIPPED: Sensei identity catalog is missing.\n  %s" % CATALOG)
        print("  Generate it by running Sensei/ableton/genre_identity.py.")
        return 0
    if not os.path.exists(PLAN):
        print("SKIPPED: no session plan. Run the ArrangementGPS chain first.")
        return 0

    result = verify_plan()
    print("Roles Sensei generates for (drum/bass/chord): %d tracks" % len(result["supported"]))
    for item in result["supported"]:
        print("  ok  %-16s %-8s %s" % (item["track"], item["role"], item["instrument_family"]))
    print()
    print("Lanes with no Sensei role (out of scope, not a failure): %d tracks" % len(result["out_of_scope"]))
    print("  " + ", ".join(result["out_of_scope"]))

    if result["failures"]:
        print()
        print("FAILED:")
        for failure in result["failures"]:
            print("  - " + failure)
        return 1

    print()
    print("%d/%d supported tracks resolve to a single role" % (len(result["supported"]), len(result["supported"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
