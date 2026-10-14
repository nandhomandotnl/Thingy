import csv

from tennis_tracker.report import load_strokes, summarize
from tennis_tracker.storage import STROKE_FIELDS


def test_summary(tmp_path):
    rows = [
        {"speler": "Jan", "slag": "forehand", "snelheid_kmh": "100", "raak": "1", "boven_meetbereik": "0"},
        {"speler": "Jan", "slag": "forehand", "snelheid_kmh": "80", "raak": "0", "boven_meetbereik": "1"},
    ]
    with open(tmp_path / "slagen.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=STROKE_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    text = summarize(load_strokes(tmp_path))
    assert "Jan" in text and "90.0" in text and "100.0" in text and "50%" in text and "meetbereik" in text
