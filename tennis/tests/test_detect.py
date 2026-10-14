import asyncio

from tennis_tracker.classify import StrokeClassifier
from tennis_tracker.detect import Sample, StrokeDetector
from tennis_tracker.sources import SimulatedSensor


def run_sim(kinds, duration=40.0, seed=1):
    strokes = []
    det = StrokeDetector("sim", on_stroke=strokes.append)
    sim = SimulatedSensor("sim", lambda _n, ss: [det.add(s) for s in ss], lambda *_: None,
                          realtime=False, duration_s=duration, kinds=kinds, seed=seed)
    asyncio.run(sim.run_forever())
    return sim, strokes


def test_every_simulated_stroke_is_detected_once():
    sim, strokes = run_sim(["forehand", "backhand", "opslag"])
    assert len(sim.strokes_played) >= 10
    assert len(strokes) == len(sim.strokes_played)
    assert all(900 * 0.9 < s.peak_dps < 1800 * 1.1 for s in strokes)
    assert all(40 < s.speed_kmh < 130 for s in strokes)


def test_hits_are_detected():
    sim, strokes = run_sim(["forehand"], seed=3)
    assert any(s.hit for s in strokes)
    assert all(s.jolt_g < 1 for s in strokes if not s.hit)


def test_noise_is_not_a_stroke():
    det = StrokeDetector("x")
    for i in range(2000):
        assert det.add(Sample(i / 200, (0, 0, 1), (20.0, -15.0, 30.0))) is None


def test_classifier_learns_stroke_types():
    labelled = {k: run_sim([k], duration=25, seed=i)[1] for i, k in enumerate(["forehand", "backhand", "opslag"])}
    clf = StrokeClassifier.train(labelled)
    for i, kind in enumerate(["forehand", "backhand", "opslag"]):
        _, strokes = run_sim([kind], duration=20, seed=10 + i)
        assert strokes and all(clf.classify(s)[0] == kind for s in strokes)
