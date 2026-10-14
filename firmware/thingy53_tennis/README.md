# Thingy:53 firmware: tennis-sensor

Stuurt de bewegingssensor (BMI270: versnelling ±16 g, draaisnelheid ±2000 °/s, standaard
200 metingen per seconde) via Bluetooth naar `tennis-tracker` op een laptop of Raspberry Pi.
Gebruik en uitleg: zie [`tennis/README.md`](../../tennis/README.md).

Kant-en-klaar: [`prebuilt/thingy53_tennis_v1.0.0_dfu_application.zip`](prebuilt/thingy53_tennis_v1.0.0_dfu_application.zip)

Het Bluetooth-protocol staat beschreven bovenin [`src/main.c`](src/main.c).

Zelf bouwen (nRF Connect SDK v3.4.1):

```bash
west build -p -b thingy53/nrf5340/cpuapp firmware/thingy53_tennis
```
