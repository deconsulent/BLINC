# Climathon

## SecondPulse

Climathon 2026 Track 3 entry. A tool that triages retired EV battery packs for a second life as stationary solar storage, plus a simulated CAN gateway that shows how an unmodified pack could talk to a solar inverter.

### What is real and what is not

Read this before the demo.

- The SOH model is trained on **synthetic data** generated from a formula in `backend/main.py`. It shows the pipeline. It does not predict real packs.
- The CAN gateway in Tab 2 is a **simulation**. No CAN hardware is involved.
- The ESP32 firmware is also a **simulation**. It does not drive CAN hardware. It runs the state machine against made up pack values and prints JSON over USB serial.
- Every euro, carbon and hub figure is a **project assumption**, not a measurement. They all live in the `ASSUMPTIONS` dict at the top of `backend/main.py`.
- The 1,500 ms precharge hold is a design value of this project.

### Layout

```
compose.yaml                  Docker Compose for backend and frontend
requirements.txt
backend/main.py               FastAPI: triage, CAN gateway simulation, assumptions
frontend/app.py               Streamlit, 4 tabs, sidebar badge shows API or fallback mode
firmware/esp32_can_gateway/   ESP32 sketch (simulation)
tests/test_backend.py         pytest
CONTRIBUTING.md               Team workflow and folder owners
```

### Run on a Mac (virtual environment first)

Needs Python 3.11 or 3.12. The pinned versions were installed and tested on Python 3.12. Python 3.14 is likely to lack wheels for the pinned numpy and `scikit-learn`.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# terminal 1: API on http://localhost:8000
uvicorn backend.main:app --port 8000

# terminal 2: dashboard on http://localhost:8501
streamlit run frontend/app.py
```

The sidebar badge shows `Mode: API` when the dashboard reached the API. If the API is not running, the dashboard falls back to calling the same backend functions in process and the badge shows `Mode: in process fallback`. So `streamlit run frontend/app.py` alone also works.

To point the dashboard at another API: `BACKEND_URL=http://host:8000 streamlit run frontend/app.py`.

Run the tests:

```bash
python -m pytest
```

API docs are at http://localhost:8000/docs while the backend runs. Endpoints: `GET /api/health`, `GET /api/oem-models`, `GET /api/assumptions`, `POST /api/triage`, `POST /api/can-gateway-simulate`.

### Run with Docker (second option)

Start Docker Desktop first.

```bash
docker compose up --build
```

Backend on http://localhost:8000, dashboard on http://localhost:8501. The frontend reaches the backend at `http://backend:8000` through the `BACKEND_URL` variable. Build once before the demo so that nothing is downloaded on stage. Stop with `docker compose down`.

### Tier rules

Checked in this order, so every input lands in exactly one tier:

1. Tier 3 (blocked) if the pack has a flagged OEM defect, or cell spread is above 100 mV, or SOH is below 62.
2. Tier 1 if SOH is 74 or higher and cell spread is 45 mV or less.
3. Tier 2 for everything else.

Each tier shows its own saving, computed from the assumptions: Tier 1 is 1 minus 63/165, Tier 2 is 1 minus 76/165. Tier 3 shows no saving.

### CAN frames

Pylontech style frames, 8 bytes each, little endian, built with `struct.pack`.

| ID | Bytes | Content |
| --- | --- | --- |
| 0x351 | 0 to 1, 2 to 3, 4 to 5, 6 to 7 | charge voltage limit, charge current limit, discharge current limit, discharge voltage limit (all 0.1 units, unsigned) |
| 0x355 | 0 to 1, 2 to 3, 4 to 7 | SOC in percent, SOH in percent, zero padding |
| 0x356 | 0 to 1, 2 to 3, 4 to 5, 6 to 7 | voltage in 0.01 V (unsigned), current in 0.1 A (signed), temperature in 0.1 C (signed), zero padding |
| 0x359 | 0, 1, 2, 3, 4, 5 to 6, 7 | protection 1, protection 2, alarm 1, alarm 2, pack count, ASCII "PN", zero |

The layout was checked against `PYLON-LV-CAN.cpp` in the Battery Emulator repository by dalathegreat. Differences, all also noted in the code:

- The emulator sends shorter frames for 0x351 (6 bytes) and 0x355. Here every frame is padded to 8 bytes.
- The 0x356 voltage is signed 16 bit in the low voltage protocol, which tops out at 327.67 V. These packs run at 350 V and above, so it is unsigned here. Whether a real inverter accepts this is not tested.
- The protocol has no cell imbalance bit. The imbalance cutoff is reported as the system error bit (0x80) in 0x359 byte 1. The original spec used 0x04 in byte 0, which in this protocol means cell undervoltage.

The OEM side IDs (0x1DB, 0x55B and others) are reference labels for display only. Their payloads are not modelled.

### Firmware

`firmware/esp32_can_gateway/esp32_can_gateway.ino` targets a plain ESP32 DevKit. It simulates the state machine and prints one JSON line every 500 ms at 115200 baud, including the four frames as hex. It uses `millis()` timing and no blocking delay.

- Precharge: CAN TX stays off until 1,500 ms after the simulated enable line goes high.
- Isolation test: every 10 s a 1.2 s simulated pulse runs, and `iso_610h_masked` is true only while it runs.
- Every 30 s the enable line drops for 2 s, so the hold can be seen again.
- Serial commands: `e` toggles enable, `i` starts a pulse, `a` returns to the automatic cycle.

Compile and flash with `arduino-cli` (not yet run on this project, see the list below):

```bash
arduino-cli core install esp32:esp32
arduino-cli compile --fqbn esp32:esp32:esp32 firmware/esp32_can_gateway
arduino-cli upload --fqbn esp32:esp32:esp32 -p /dev/cu.SLAB_USBtoUART firmware/esp32_can_gateway
arduino-cli monitor -p /dev/cu.SLAB_USBtoUART -c baudrate=115200
```

### Demo script for the two Saturday expert sessions

Session 1: Kristiāns Kreinbergs (RTU Electronic Systems) and Aleksandrs Mihailovs (MAA intelengineering).

1. Open Tab 2 with the ESP32 serial output next to it. Say clearly that both are simulations.
2. Move the "time since enable" slider across 1,500 ms and show CAN TX switching from HELD to ACTIVE. Show the same in the serial output.
3. Switch on the isolation test pulse and show `iso_610h_masked` change. Explain that the timing value is the project's design choice and ask whether 1,500 ms is right for their inverters.
4. Switch on the cell imbalance fault and show both current limits go to zero and the 0x359 system error bit.
5. Go to Tab 1, select the Jaguar I PACE and show the safety gate blocking it. Ask where a citable source for the tab disconnect defect would be.

Session 2: Evita Jaunozola (RTU Innovations, EIT Urban Mobility Hub) and Karlis Skuja (WindHackers UX).

1. Open Tab 3 and select the RTU Ķīpsala campus hub, then the Riga Airport e bike buffer.
2. Show the sizing result and the cost comparison. Say that the euro figures are project assumptions and ask which of them they can help source.
3. Point out that Tier 1 and Tier 2 show different savings, and that a blocked pack produces no hub.
4. Ask what the pilot sites would need to see before agreeing to a trial.

### Claims that still need a source before the jury sees them

- Cost per kWh for Tier 1 (63), Tier 2 (76), cell teardown (113), new LFP (165), the labor hours, and the 61.8 percent saving that follows from them.
- 72 kg CO2e avoided per usable kWh.
- The second life fade rates and cycles per year. With these, a near new Leaf shows about 27 years of remaining life, which looks too optimistic.
- The Jaguar I PACE early revision pouch tab disconnect defect. It was not found in the Battery Emulator wiki.
- The Nissan Leaf "3 star compatibility" rating from the original spec. It was not found, and the claim was removed.
- That the 1,500 ms hold fixes a real SMA problem. Battery Emulator issue 1959 is about an Enerdel Think battery, mentions a 610h isolation message from that BMS, and does not mention 1,500 ms. It does not cover the Leaf or the Stellantis pack.
- That a Pylontech low voltage frame set is accepted by inverters when used for 350 to 400 V packs. This needs a test with a real inverter.
- Battery Emulator support for each pack in the registry, and parallel operation of 2 or 3 identical packs.
- The OEM CAN ID labels, including the VW MEB one.
- Pack data in the registry: nominal voltage, series cell counts and chemistry.
- Hardware prices (ESP32 board, supply and relay, fuse) and that a dual CAN board of the named type exists at that price.
- Solar yield (3.8 kWh per kWp per day), energy per scooter charge and per cargo e bike charge.
- That Riga pilot sites and organisations have agreed to anything. They are ideas.
- That reusing a whole pack without teardown is permitted under EU battery rules. Needs a legal source.

### Team workflow

Branch per person or feature, pull requests into main, nobody pushes straight to main after the first push. Folder owners are listed in [CONTRIBUTING.md](CONTRIBUTING.md).
