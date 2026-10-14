"""Command line: tennis-tracker scan | run | calibrate | identify | report | collect"""

import argparse
import asyncio
import datetime as dt
import logging
import sys
from pathlib import Path
from typing import Dict, List

from . import dashboard, protocol
from .classify import DEFAULT_KINDS, StrokeClassifier, calibration_path, load_for_sensor
from .detect import DetectorSettings, Stroke, StrokeDetector
from .forward import Forwarder, collect
from .report import load_strokes, summarize
from .sources import BleSensor, SimulatedSensor, scan
from .storage import Session

log = logging.getLogger("tennis_tracker")


def _looks_like_address(value: str) -> bool:
    return value.count(":") == 5 or value.count("-") == 4  # MAC, or macOS UUID


async def _resolve_sensors(args) -> List["tuple[str, str]"]:
    """Returns [(name, address)] for the sensors to use."""
    wanted = args.sensor or []
    if wanted and all(_looks_like_address(w) for w in wanted):
        return [(w, w) for w in wanted]

    print("Zoeken naar tennis-sensors ...")
    found = await scan()
    if wanted:
        result = []
        for w in wanted:
            match = [f for f in found if w in (f[0], f[1])]
            if not match:
                raise SystemExit(f"Sensor {w!r} niet gevonden. Gevonden: {[f[0] for f in found] or 'geen'}")
            result.append(match[0][:2])
        return result
    if len(found) < args.count:
        raise SystemExit(f"{args.count} sensor(s) nodig, {len(found)} gevonden: {[f[0] for f in found]}. "
                         "Staan ze aan en zijn ze niet met iets anders verbonden?")
    return [f[:2] for f in found[:args.count]]


async def _make_sources(args, on_samples, on_marker):
    if args.simulate:
        n = len(args.sensor or []) or args.count
        names = args.sensor or [f"Sim-{i + 1}" for i in range(n)]
        return [SimulatedSensor(name, on_samples, on_marker, rate_hz=args.rate, seed=i)
                for i, name in enumerate(names)]
    sensors = await _resolve_sensors(args)
    return [BleSensor(name, addr, on_samples, on_marker, rate_hz=args.rate) for name, addr in sensors]


def _detector_settings(args) -> DetectorSettings:
    return DetectorSettings(radius_m=args.radius, min_peak_dps=args.min_peak)


def _format_stroke(row: Dict[str, object]) -> str:
    hit = "RAAK " if int(row["raak"]) else "lucht"
    sat = " (>meetbereik)" if int(row["boven_meetbereik"]) else ""
    conf = f" {float(row['zekerheid']):.0%}" if float(row["zekerheid"]) else ""
    return (f"{dt.datetime.now():%H:%M:%S}  {row['speler']:<10} {row['slag']:<9}{conf:>5}  "
            f"{float(row['snelheid_kmh']):5.0f} km/h  piek {int(row['piek_graden_per_s']):4d} °/s  {hit}{sat}")


# ---------- run ----------

def _start_dashboard(args, broadcaster: "dashboard.Broadcaster") -> None:
    if args.no_dashboard:
        return
    url = dashboard.start(broadcaster, args.dashboard_host, args.dashboard_port)
    print(f"Dashboard: {url}")
    if not args.no_browser:
        import webbrowser

        webbrowser.open(url)


async def cmd_run(args) -> None:
    session = Session(Path(args.out), save_raw=not args.no_raw)
    forwarder = Forwarder(args.forward) if args.forward else None
    board = dashboard.Broadcaster()
    detectors: Dict[str, StrokeDetector] = {}
    classifiers: Dict[str, StrokeClassifier] = {}
    thinners: Dict[str, dashboard.SampleThinner] = {}
    players: Dict[str, str] = {}
    stats: Dict[str, int] = {}

    def on_stroke(stroke: Stroke) -> None:
        classifiers[stroke.sensor].apply(stroke)
        row = session.add_stroke(stroke, players[stroke.sensor])
        print(_format_stroke(row), flush=True)
        board.publish({"type": "stroke", **row}, keep=True)
        if forwarder:
            forwarder.send(row)

    def on_samples(sensor, samples) -> None:
        stats[sensor] = stats.get(sensor, 0) + len(samples)
        session.add_samples(sensor, samples)
        thinners[sensor].add(samples, args.rate)
        for s in samples:
            detectors[sensor].add(s)

    def on_marker(sensor, t) -> None:
        print(f"{dt.datetime.now():%H:%M:%S}  {players[sensor]:<10} --- markering (knop) ---", flush=True)
        session.add_marker(sensor, t)
        board.publish({"type": "marker", "sensor": sensor, "t": t}, keep=True)

    sources = await _make_sources(args, on_samples, on_marker)
    for i, src in enumerate(sources):
        players[src.name] = args.player[i] if args.player and i < len(args.player) else src.name
        classifiers[src.name] = load_for_sensor(Path(args.calibration), src.name)
        detectors[src.name] = StrokeDetector(src.name, _detector_settings(args), on_stroke)
        thinners[src.name] = dashboard.SampleThinner(board, src.name)
        state = "gekalibreerd" if classifiers[src.name].trained else "niet gekalibreerd (doe: tennis-tracker calibrate)"
        print(f"Sensor {src.name} -> speler {players[src.name]}, {state}")
    board.set_hello(dashboard.hello_message(
        {src.name: {"player": players[src.name], "calibrated": classifiers[src.name].trained} for src in sources},
        f"Sessie {session.dir.name}"))
    _start_dashboard(args, board)
    print(f"Sessie wordt opgeslagen in {session.dir}. Stoppen met Ctrl+C.\n")

    async def status():
        while True:
            await asyncio.sleep(30)
            parts = [f"{s.name}: {stats.get(s.name, 0) / 30:.0f} metingen/s, {s.clock.lost_packets} pakketten gemist"
                     for s in sources]
            for s in sources:
                stats[s.name] = 0
            log.info("Status: %s", "; ".join(parts))

    try:
        await asyncio.gather(status(), *(s.run_forever() for s in sources))
    finally:
        session.close()
        print("\n" + summarize(load_strokes(session.dir)))
        print(f"\nOpgeslagen in {session.dir}")


# ---------- calibrate ----------

async def cmd_calibrate(args) -> None:
    queue: asyncio.Queue = asyncio.Queue()
    detector_holder = {}

    def on_samples(sensor, samples):
        for s in samples:
            detector_holder["d"].add(s)

    sources = await _make_sources(args, on_samples, lambda *_: None)
    if len(sources) != 1:
        raise SystemExit("Kalibreer één sensor tegelijk (gebruik --sensor).")
    src = sources[0]
    detector_holder["d"] = StrokeDetector(src.name, _detector_settings(args), queue.put_nowait)
    task = asyncio.create_task(src.run_forever())
    loop = asyncio.get_running_loop()

    print(f"Kalibratie van {src.name}: per slagtype sla je {args.strokes} keer.\n")
    labelled: Dict[str, List[Stroke]] = {}
    try:
        for kind in args.kinds:
            await loop.run_in_executor(None, input, f">>> Klaar om {args.strokes}x {kind.upper()} te slaan? Druk op Enter ...")
            if isinstance(src, SimulatedSensor):
                src.kinds = [kind]
            while not queue.empty():
                queue.get_nowait()
            labelled[kind] = []
            while len(labelled[kind]) < args.strokes:
                stroke = await queue.get()
                labelled[kind].append(stroke)
                print(f"    {kind} {len(labelled[kind])}/{args.strokes}: piek {stroke.peak_dps:.0f} °/s")
    finally:
        task.cancel()

    clf = StrokeClassifier.train(labelled)
    path = calibration_path(Path(args.calibration), src.name)
    clf.save(path)
    total = sum(len(v) for v in labelled.values())
    correct = sum(clf.classify(s)[0] == kind for kind, ss in labelled.items() for s in ss)
    print(f"\nOpgeslagen in {path}. Herkenning op de kalibratieslagen: {correct}/{total} goed.")
    if correct < total:
        print("Tip: herhaal de kalibratie met duidelijk verschillende slagen.")


# ---------- small commands ----------

async def cmd_scan(args) -> None:
    found = await scan(args.timeout)
    if not found:
        print("Geen tennis-sensors gevonden. Staan ze aan (blauw knipperen)?")
    for name, addr, rssi in found:
        print(f"{name:<16} {addr}  signaal {rssi} dBm")


async def cmd_identify(args) -> None:
    from bleak import BleakClient

    (name, addr), = await _resolve_sensors(argparse.Namespace(sensor=[args.sensor], count=1))
    async with BleakClient(addr) as client:
        await client.write_gatt_char(protocol.CONTROL_UUID, protocol.cmd_identify(), response=True)
    print(f"{name} knippert nu 3 seconden wit.")


async def cmd_collect(args) -> None:
    board = dashboard.Broadcaster()
    board.set_hello(dashboard.hello_message({}, "Verzamelpunt: slagen van alle Pi's"))
    _start_dashboard(args, board)

    def on_row(row):
        print(_format_stroke(row), flush=True)
        board.publish({"type": "stroke", **row}, keep=True)

    await collect(args.listen, Path(args.out), on_row)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tennis-tracker", description="Tennisslagen analyseren met Thingy:53-sensors.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def sensor_opts(sp, multi=True):
        sp.add_argument("--sensor", action="append", help="naam (bv. Tennis-AB12) of adres; mag vaker" if multi else "naam of adres")
        sp.add_argument("--count", type=int, default=1, help="aantal sensors als je geen --sensor opgeeft")
        sp.add_argument("--rate", type=int, default=200, choices=[50, 100, 200, 400], help="metingen per seconde")
        sp.add_argument("--radius", type=float, default=1.0, help="afstand draaipunt-sweet spot in m voor de snelheidsschatting")
        sp.add_argument("--min-peak", type=float, default=500.0, help="minimale draaisnelheid (°/s) voor een slag")
        sp.add_argument("--calibration", default="kalibratie", help="map met kalibratiebestanden")
        sp.add_argument("--simulate", action="store_true", help="geen echte sensor; nep-slagen om te testen")

    def dashboard_opts(sp):
        sp.add_argument("--no-dashboard", action="store_true", help="geen live dashboard in de browser")
        sp.add_argument("--no-browser", action="store_true", help="dashboard wel starten maar browser niet openen")
        sp.add_argument("--dashboard-host", default="127.0.0.1",
                        help="0.0.0.0 = ook bereikbaar vanaf andere apparaten (bv. de Pi bekijken vanaf je laptop)")
        sp.add_argument("--dashboard-port", type=int, default=8000)

    sp = sub.add_parser("scan", help="zoek sensors in de buurt")
    sp.add_argument("--timeout", type=float, default=8.0)

    sp = sub.add_parser("run", help="slagen live meten en opslaan")
    sensor_opts(sp)
    sp.add_argument("--player", action="append", help="spelernaam per sensor, in dezelfde volgorde")
    sp.add_argument("--out", default="sessies", help="map voor de sessiebestanden")
    sp.add_argument("--no-raw", action="store_true", help="geen ruwe sensordata opslaan")
    sp.add_argument("--forward", help="stuur slagen door naar HOST:POORT (zie 'collect')")
    dashboard_opts(sp)

    sp = sub.add_parser("calibrate", help="leer forehand/backhand/opslag herkennen")
    sensor_opts(sp, multi=False)
    sp.add_argument("--strokes", type=int, default=5, help="aantal slagen per type")
    sp.add_argument("--kinds", nargs="+", default=DEFAULT_KINDS, help="slagtypes")

    sp = sub.add_parser("identify", help="laat een sensor wit knipperen")
    sp.add_argument("--sensor", required=True)

    sp = sub.add_parser("report", help="samenvatting van een sessie")
    sp.add_argument("path", help="sessiemap of slagen.csv")

    sp = sub.add_parser("collect", help="ontvang slagen van meerdere Pi's")
    sp.add_argument("--listen", default="0.0.0.0:5005")
    sp.add_argument("--out", default="sessies/alle_slagen.csv")
    dashboard_opts(sp)
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if args.command == "report":
        print(summarize(load_strokes(Path(args.path))))
        return
    commands = {"scan": cmd_scan, "run": cmd_run, "calibrate": cmd_calibrate,
                "identify": cmd_identify, "collect": cmd_collect}
    try:
        asyncio.run(commands[args.command](args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main(sys.argv[1:])
