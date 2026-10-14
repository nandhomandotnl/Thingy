"""Summary of a session: strokes per player and type, speeds and hit rate."""

import csv
from collections import defaultdict
from pathlib import Path
from typing import Dict, List


def load_strokes(path: Path) -> List[Dict[str, str]]:
    if path.is_dir():
        path = path / "slagen.csv"
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def summarize(rows: List[Dict[str, str]]) -> str:
    if not rows:
        return "Geen slagen gevonden."
    groups = defaultdict(list)
    for r in rows:
        groups[(r["speler"], r["slag"])].append(r)

    lines = [f"{'speler':<12} {'slag':<10} {'aantal':>6} {'gem km/h':>9} {'max km/h':>9} {'raak %':>7}"]
    for (player, kind), rs in sorted(groups.items()):
        speeds = [float(r["snelheid_kmh"]) for r in rs]
        hits = sum(int(r["raak"]) for r in rs)
        lines.append(f"{player:<12} {kind:<10} {len(rs):>6} {sum(speeds) / len(speeds):>9.1f} "
                     f"{max(speeds):>9.1f} {100 * hits / len(rs):>6.0f}%")
    saturated = sum(int(r.get("boven_meetbereik", 0) or 0) for r in rows)
    if saturated:
        lines.append(f"\nLet op: {saturated} slag(en) waren sneller dan het meetbereik (2000 °/s); "
                     "de snelheid daarvan is een ondergrens.")
    return "\n".join(lines)
