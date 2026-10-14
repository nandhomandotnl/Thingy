# Thingy:52 als muis

Gebruik een [Nordic Thingy:52](https://www.nordicsemi.com/Products/Development-hardware/Nordic-Thingy-52)
als "air mouse" voor je laptop, met een Raspberry Pi ertussen.

```
 Thingy:52  ──Bluetooth LE──▶  Raspberry Pi  ──USB-kabel (Pi = USB-muis)──▶  Laptop
 (gyroscoop + knop)            (thingy-mouse)                               (geen software nodig)
```

De Pi leest de gyroscoop en de knop van de Thingy via Bluetooth, en doet zich via USB
voor als een gewone muis. Je laptop (Windows, macOS, Linux) ziet gewoon een USB-muis.

## Bediening

| Actie met de Thingy                  | Muis                                  |
|--------------------------------------|---------------------------------------|
| Draaien links/rechts                 | Cursor links/rechts                   |
| Kantelen omhoog/omlaag               | Cursor omhoog/omlaag                  |
| Knop (bovenkant) indrukken           | Linkermuisknop (vasthouden = slepen)  |
| Dubbel tikken op de Thingy           | Rechtermuisklik                       |
| Drie keer tikken                     | Pauze aan/uit                         |

LED-kleuren:
- **geel knipperend**: aan het kalibreren, leg de Thingy ~1 seconde stil
- **groen**: actief
- **blauw ademend**: gepauzeerd

## Snel proberen zonder Pi (direct op je laptop)

Heeft je laptop Bluetooth, dan kan hij ook zelf met de Thingy praten. Dan is er geen Pi nodig;
de app beweegt direct je eigen cursor. Werkt op Windows, macOS en Linux (Python 3.9+ nodig).

```bash
git clone https://github.com/nandhomandotnl/Thingy.git
cd Thingy
python -m pip install ".[desktop]"
python -m thingy_mouse --output desktop
```

- Zet de Thingy aan en zorg dat hij **niet** verbonden is met de Thingy-app op je telefoon.
  Koppel hem ook **niet** via de Bluetooth-instellingen van je laptop; de app maakt zelf verbinding.
- Leg de Thingy stil tot de LED groen wordt.
- **macOS**: geef je terminal toegang onder *Systeeminstellingen → Privacy en beveiliging →
  Toegankelijkheid* (muis besturen) en *Bluetooth*.
- Stoppen: `Ctrl+C` in de terminal. Tip: tik drie keer op de Thingy om de cursor te pauzeren.
- Eerst kijken of het werkt zonder dat je cursor wegschiet: `python -m thingy_mouse --output print -v`.

## Wat heb je nodig (met Pi)

- Nordic Thingy:52 met de standaard firmware
- Een Raspberry Pi **met USB-OTG**: Pi Zero W / **Pi Zero 2 W** (aanbevolen), Pi 4 of Pi 5.
  Een Pi 3 kan zich niet als USB-apparaat voordoen en werkt daarom niet.
- Een USB-datakabel (geen alleen-laden-kabel):
  - Pi Zero: de micro-USB poort met **"USB"** (niet "PWR") naar je laptop. De Pi krijgt dan ook stroom van de laptop.
  - Pi 4/5: de **USB-C** poort naar je laptop (die voedt dan ook de Pi; een Pi 5 kan daarbij te weinig stroom krijgen).
- Raspberry Pi OS (Bookworm of nieuwer)

## Installatie op de Pi

```bash
git clone https://github.com/nandhomandotnl/Thingy.git
cd Thingy
sudo ./scripts/install.sh
sudo reboot
```

Het installatiescript:
1. zet USB-gadgetmodus aan (`dtoverlay=dwc2` in `config.txt`, `libcomposite` in `/etc/modules`),
2. installeert de app in `/opt/thingy-mouse`,
3. installeert twee systemd-services die bij het opstarten starten:
   - `thingy-usb-gadget` maakt de USB-muis aan (`/dev/hidg0`),
   - `thingy-mouse` verbindt met de Thingy en stuurt de muis aan (verbindt automatisch opnieuw).

Na de reboot: zet de Thingy aan (zorg dat hij **niet** verbonden is met de Thingy-app op je telefoon),
sluit de Pi aan op je laptop en wacht tot de LED groen wordt.

Logs bekijken:

```bash
journalctl -u thingy-mouse -f
```

## Instellingen

Opties zet je in `/etc/default/thingy-mouse`, daarna `sudo systemctl restart thingy-mouse`:

```bash
THINGY_MOUSE_ARGS="--sensitivity 15 --address E1:23:45:67:89:AB"
```

| Optie                     | Standaard | Betekenis                                                   |
|---------------------------|-----------|-------------------------------------------------------------|
| `--address`               | eerste gevonden | Bluetooth-adres van jouw Thingy                       |
| `--sensitivity`           | 12        | Muisstappen per graad draaien                               |
| `--acceleration`          | 0.6       | Snelle bewegingen gaan verder                               |
| `--deadzone`              | 2.0       | Kleine bewegingen (graden/s) negeren tegen trillen          |
| `--smoothing`             | 0.3       | 0 = direct, hoger = vloeiender maar trager                  |
| `--rate`                  | 100       | Samples per seconde van de Thingy (5–200)                   |
| `--x-axis` / `--y-axis`   | z / x     | Welke gyro-as de cursor horizontaal/verticaal beweegt       |
| `--invert-x` / `--no-invert-x` (idem y) | aan | Richting omdraaien                              |
| `--click-freeze-ms`       | 150       | Cursor kort stilzetten bij klikken zodat je niet mis klikt  |
| `--output`                | hid       | `hid` = Pi als USB-muis, `desktop` = direct op deze computer, `uinput` = Linux virtuele muis, `print` = debug |

**Beweegt de cursor de verkeerde kant op?** Gebruik `--no-invert-x` / `--no-invert-y`.
**Houd je de Thingy anders vast** (bijv. rechtop)? Probeer een andere `--x-axis` / `--y-axis`
(met `--output print` zie je meteen welke kant hij op gaat).

## Testen zonder laptop

Op de Pi, zonder de service:

```bash
sudo systemctl stop thingy-mouse
/opt/thingy-mouse/venv/bin/thingy-mouse --output print -v
```

## Ontwikkelen

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
pytest
```

Code-overzicht:
- `thingy_mouse/thingy.py` – Bluetooth LE verbinding met de Thingy (bleak), gyro/knop/tik-notificaties, LED
- `thingy_mouse/motion.py` – gyroscoop → muisbeweging (bias-kalibratie, deadzone, versnelling, smoothing)
- `thingy_mouse/mouse.py` – muis-uitvoer: USB HID gadget, uinput of print
- `thingy_mouse/app.py` – knoppen/tikken → klikken, pauze, LED-feedback
- `scripts/usb_gadget.sh` – maakt van de Pi een USB-muis via configfs

## Problemen

- **"No Thingy found"**: staat de Thingy aan en is hij niet verbonden met je telefoon? Probeer `bluetoothctl scan on` om hem te zien.
- **"No USB device controller found"**: `dtoverlay=dwc2` ontbreekt of je hebt nog niet gereboot, of je Pi ondersteunt geen USB-OTG.
- **"USB host is not reading reports"**: verkeerde poort of een kabel zonder datalijnen.
- **Cursor drijft langzaam weg**: tik drie keer (pauze) en nog drie keer; bij hervatten wordt opnieuw gekalibreerd. Leg hem dan even stil.
