# Thingy:53 firmware: Bluetooth-muis

Met deze firmware wordt de Nordic Thingy:53 een gewone **Bluetooth-muis**. Je koppelt hem
in de Bluetooth-instellingen van je laptop (Windows, macOS, Linux, ook Android/iPad).
Je hebt geen Raspberry Pi en geen extra software nodig.

## Bediening

| Thingy:53                                    | Muis                                  |
|----------------------------------------------|---------------------------------------|
| Draaien links/rechts                         | Cursor links/rechts                   |
| Voorkant omhoog/omlaag kantelen              | Cursor omhoog/omlaag                  |
| Grote knop bovenop (SW3)                     | Linkermuisknop (vasthouden = slepen)  |
| Tweede knop (SW4)                            | Rechtermuisknop                       |
| Beide knoppen 2 s vasthouden                 | "Voorkant" 90° draaien (zie hieronder)|
| Grote knop vasthouden tijdens aanzetten      | Alle koppelingen wissen               |

De gyroscoop werkt samen met de versnellingsmeter, dus het maakt niet uit of je de
Thingy een beetje schuin of gekanteld in je hand houdt: links draaien is altijd links.

**Gaat omhoog/omlaag raar (of zijwaarts)?** Dan wijst de "voorkant" de verkeerde kant op.
Houd beide knoppen 2 seconden vast: de LED knippert wit (1–4 keer) en de voorkant draait
90°. Herhaal tot het goed voelt; de keuze wordt onthouden.

LED:
- **blauw knipperend**: wacht op een computer (koppelmodus)
- **geel**: leg de Thingy even stil (kalibreren, ~½ s)
- **kort groen knipperen** (elke 3 s): actief
- **3× rood** bij aanzetten: koppelingen gewist

## Firmware erop zetten

Kant-en-klaar bestand: [`prebuilt/thingy53_mouse_v1.0.0_dfu_application.zip`](prebuilt/thingy53_mouse_v1.0.0_dfu_application.zip)
(download het via GitHub met de knop "Download raw file").

### Optie A: draadloos met je telefoon (makkelijkst)

1. Installeer **nRF Connect Device Manager** (Android/iOS) en zet het zip-bestand op je telefoon.
2. Zet de Thingy:53 aan (SW1). Met de fabrieksfirmware is hij zichtbaar in de app.
3. Kies de Thingy → tab **Image** → **Select file** → kies de zip → **Start**
   (kies bij de vraag "Test and Confirm" of "Confirm only").
4. Wacht tot het klaar is; de Thingy herstart als muis (blauw knipperen).

### Optie B: via USB-kabel met de computer

1. Installeer [nRF Connect for Desktop](https://www.nordicsemi.com/Products/Development-tools/nRF-Connect-for-Desktop) en daarin de app **Programmer**.
2. Zet de Thingy:53 uit, houd **SW4** ingedrukt en zet hem aan (SW1) terwijl hij met USB-C aan
   de computer zit. Hij staat nu in de bootloader (MCUboot serial recovery).
3. Kies in Programmer de Thingy:53, **Add file** → de zip, en klik **Write**.

## Koppelen met je laptop

1. Zet de Thingy aan; de LED knippert blauw.
2. Laptop: Bluetooth-instellingen → *Apparaat toevoegen* → **Thingy53 Mouse**.
3. Leg de Thingy even stil tot de LED stopt met geel; klaar.

Wil je hem aan een andere laptop koppelen en lukt dat niet? Zet hem uit, houd de grote knop
ingedrukt en zet hem aan (3× rood = koppelingen gewist).

## Zelf bouwen

Nodig: [nRF Connect SDK](https://docs.nordicsemi.com/bundle/ncs-latest/page/nrf/installation.html) v3.4.1.

```bash
west build -p -b thingy53/nrf5340/cpuapp firmware/thingy53
# resultaat: build/dfu_application.zip
```

Unit tests van het bewegingsalgoritme (op je pc):

```bash
cc -Wall -o /tmp/test_airmouse firmware/thingy53/tests/test_airmouse.c firmware/thingy53/src/airmouse.c -lm && /tmp/test_airmouse
```

Instellingen zoals gevoeligheid staan in `air_cfg` in `src/main.c`.

## Code

- `src/main.c`: Bluetooth HID-muis (HID over GATT), knoppen, LED, BMI270-sensor
- `src/airmouse.c`: gyroscoop + zwaartekracht → muisbeweging (kalibratie, deadzone, versnelling)
- `boards/thingy53_nrf5340_cpuapp.overlay`: zet de BMI270-sensor aan
- `boards/thingy53_nrf5340_cpuapp.conf`: firmware-updates via Bluetooth blijven mogelijk
