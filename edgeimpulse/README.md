# Thingy:53 met de Edge Impulse-firmware uitlezen via Bluetooth (NUS)

Dit gebruikt de **originele, ongewijzigde** firmware van Edge Impulse:
[edgeimpulse/firmware-nordic-thingy53](https://github.com/edgeimpulse/firmware-nordic-thingy53),
als git-submodule in [`../firmware/edgeimpulse_thingy53`](../firmware/edgeimpulse_thingy53).
Die firmware is gemaakt voor **nRF Connect SDK (NCS) 2.4.0**.

## Firmware bouwen (NCS 2.4.0)

```bash
git submodule update --init firmware/edgeimpulse_thingy53

# NCS 2.4.0 in een aparte map (eenmalig)
west init -m https://github.com/nrfconnect/sdk-nrf --mr v2.4.0 ~/ncs/v2.4.0
cd ~/ncs/v2.4.0 && west update && cd -

cd firmware/edgeimpulse_thingy53
west build -b thingy53_nrf5340_cpuapp
```

Flashen: zie de README van de firmware (J-Link met `west flash`, of via USB/Bluetooth-DFU
met `build/zephyr/dfu_application.zip`). Na het opstarten adverteert de Thingy als `EdgeImpulse`.

## Python-script

```bash
python -m pip install bleak cbor2
python edgeimpulse/thingy53_nus.py --length 2000 --freq 100
```

Het script zoekt de Thingy op naam, stuurt via NUS een meetverzoek en zet het resultaat in
`ImuSample`-dataclasses (`t`, `acc_x/y/z` in m/s², `gyro_x/y/z`).

Goed om te weten over deze firmware:

- **Geen live stroom.** De firmware meet een vaste tijd (`--length`, in ms) en stuurt de
  meting daarna in één keer terug. Frequenties: 20, 62.5 of 100 Hz.
- **Alleen accelerometer, geen gyroscoop.** De originele firmware leest alleen de ADXL362
  (`Accelerometer`). De gyroscoop (BMI270) wordt niet gebruikt, dus `gyro_*` blijft `None`.
- **Eigen NUS-UUID's.** Het protocol is NUS, maar met de Edge Impulse-UUID's
  `e2a0000x-ec31-4ec3-a97a-1c34d87e9878` in plaats van de standaard `6e40000x-...`.
- Koppel de Thingy niet via de Bluetooth-instellingen en sluit de nRF Edge Impulse-app;
  het script maakt zelf verbinding.
