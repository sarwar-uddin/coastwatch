"""Numbers behind the map: who fishes in Bangladesh waters, and how much is dark."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from .config import PROCESSED_DIR


def load(year: int, name: str) -> list[dict]:
    path: Path = PROCESSED_DIR / str(year) / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else []


BGD_MID = "405"  # Bangladesh's MMSI country prefix (Maritime Identification Digits)


def is_mid_transposition(mmsi: str | None) -> bool:
    """True when an MMSI's country prefix is a reordering of Bangladesh's 405,
    such as 450 (Lebanon). Found in the 2025 data: Bangladeshi trawlers (one
    broadcasting "BASUNDARA7") transmitting 450000xxx, so GFW reports them as
    Lebanese. A likely typo when the transponder was set up, not a foreign fleet."""
    if not mmsi or len(mmsi) < 3:
        return False
    prefix = mmsi[:3]
    return prefix != BGD_MID and sorted(prefix) == sorted(BGD_MID)


def suspect_flags(events: dict[str, list[dict]]) -> dict[str, list[str]]:
    """Flags whose every observed vessel has a transposed-405 MMSI → likely BGD."""
    mmsis: dict[str, set[str]] = defaultdict(set)
    for rows in events.values():
        for e in rows:
            flag, mmsi = e["vessel"].get("flag"), e["vessel"].get("mmsi")
            if flag and flag != "BGD" and mmsi:
                mmsis[flag].add(mmsi)
    return {flag: sorted(ids) for flag, ids in mmsis.items()
            if ids and all(is_mid_transposition(m) for m in ids)}


def build_summary(year: int) -> dict:
    effort = load(year, "fishing_effort")
    sar_all = load(year, "sar_all")
    sar_dark = load(year, "sar_dark")

    hours_by_flag: dict[str, float] = defaultdict(float)
    hours_by_month: dict[str, float] = defaultdict(float)
    for row in effort:
        hours = row.get("hours") or 0
        hours_by_flag[row.get("flag") or "unknown"] += hours
        hours_by_month[str(row.get("date"))[:7]] += hours

    events = {
        kind: load(year, f"events_{kind}")
        for kind in ("fishing", "gaps", "encounters", "loitering")
    }
    likely_bgd = suspect_flags(events)

    total_hours = sum(hours_by_flag.values())
    foreign_hours = sum(h for flag, h in hours_by_flag.items() if flag not in ("BGD", "unknown"))
    foreign_hours_adjusted = sum(
        h for flag, h in hours_by_flag.items() if flag not in ("BGD", "unknown", *likely_bgd))
    detections_all = sum(r.get("detections") or 0 for r in sar_all)
    detections_dark = sum(r.get("detections") or 0 for r in sar_dark)

    event_flags = {
        kind: Counter((e["vessel"].get("flag") or "unknown") for e in rows).most_common(5)
        for kind, rows in events.items()
    }

    return {
        "year": year,
        "fishing_hours_total": round(total_hours),
        "fishing_hours_by_flag": sorted(
            ((flag, round(h)) for flag, h in hours_by_flag.items()), key=lambda x: -x[1]),
        "foreign_share_of_fishing_hours": (foreign_hours / total_hours) if total_hours else None,
        "foreign_share_adjusted": (foreign_hours_adjusted / total_hours) if total_hours else None,
        "likely_bgd_flags": likely_bgd,
        "fishing_hours_by_month": dict(sorted(hours_by_month.items())),
        "sar_detections_total": round(detections_all),
        "sar_detections_dark": round(detections_dark),
        "dark_share_of_sar": (detections_dark / detections_all) if detections_all else None,
        "event_counts": {kind: len(rows) for kind, rows in events.items()},
        "event_top_flags": event_flags,
    }


def print_summary(year: int) -> None:
    s = build_summary(year)
    pct = lambda v: "n/a" if v is None else f"{v:.0%}"  # noqa: E731
    print(f"Bangladesh EEZ, {year}")
    print(f"  Apparent fishing (AIS): {s['fishing_hours_total']:,} hours, "
          f"foreign-flagged share {pct(s['foreign_share_of_fishing_hours'])}")
    for flag, mmsis in s["likely_bgd_flags"].items():
        print(f"    ! {flag} is probably Bangladeshi: every {flag} vessel has a 405-transposed MMSI "
              f"({', '.join(mmsis[:4])}{'…' if len(mmsis) > 4 else ''})")
    if s["likely_bgd_flags"]:
        print(f"    foreign share excluding those: {pct(s['foreign_share_adjusted'])}")
    for flag, hours in s["fishing_hours_by_flag"][:8]:
        print(f"    {flag:8} {hours:>10,} h")
    print(f"  Radar detections: {s['sar_detections_total']:,} "
          f"(not matched to AIS: {s['sar_detections_dark']:,}, {pct(s['dark_share_of_sar'])})")
    print("  Events: " + ", ".join(f"{k} {v:,}" for k, v in s["event_counts"].items()))
    for kind, flags in s["event_top_flags"].items():
        if flags:
            print(f"    {kind:10} top flags: " + ", ".join(f"{f} {n}" for f, n in flags))
