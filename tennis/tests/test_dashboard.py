import json
import urllib.request

from tennis_tracker import dashboard
from tennis_tracker.detect import Sample


def test_thinner_keeps_peaks_and_reduces_rate():
    b = dashboard.Broadcaster()
    q = b.subscribe()
    thin = dashboard.SampleThinner(b, "s1")
    samples = [Sample(i / 200, (0, 0, 1), (0, 0, 1500.0 if i == 7 else 10.0)) for i in range(200)]
    thin.add(samples, 200)
    msg = json.loads(q.get_nowait())
    assert msg["type"] == "samples" and msg["sensor"] == "s1"
    assert len(msg["t"]) == 50
    assert max(msg["g"]) == 1500


def test_server_serves_page_and_events():
    b = dashboard.Broadcaster()
    b.set_hello(dashboard.hello_message({"s1": {"player": "Jan"}}, "test"))
    b.publish({"type": "stroke", "sensor": "s1", "snelheid_kmh": 90}, keep=True)
    url = dashboard.start(b, "127.0.0.1", 8765)
    page = urllib.request.urlopen(url, timeout=5).read().decode()
    assert "Tennis live" in page
    with urllib.request.urlopen(url + "events", timeout=5) as r:
        first = json.loads(r.readline().decode()[len("data: "):])
        r.readline()
        second = json.loads(r.readline().decode()[len("data: "):])
    assert first["type"] == "hello" and first["sensors"][0]["player"] == "Jan"
    assert second["type"] == "stroke"
