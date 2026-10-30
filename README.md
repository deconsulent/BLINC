# Climathon

## SecondPulse

Climathon 2026 Track 3 entry. A tool that triages retired EV battery packs for a second life as stationary solar storage, plus a CAN gateway that runs on a Raspberry Pi and shows how an unmodified pack could talk to a solar inverter.

### What is real and what is not

Read this before the demo.

- The SOH model is trained on **synthetic data** generated from a formula in `backend/main.py`. It shows the pipeline. It does not predict real packs.
- The gateway in `gateway/` is **our own Python implementation** of the translation, inspired by the open source Battery Emulator project by dalathegreat. It is **not a port** of that firmware and does not contain its code. Differences from the emulator are listed under "CAN frames".
- A Linux board is **not a safety rated controller**. The real protection must be the hardware fuse, the battery's own BMS and the contactors. **The Pi must never be the only safeguard.** See `docs/PI_SETUP.md`.
- Only a `demo` BMS profile exists. Its frame layout is made up and does not match any real vehicle. A real battery needs a profile written for it before the gateway can read it.
- Demo mode uses a fake BMS and mock GPIO. It switches nothing real.
- The gateway has not yet been run on a real Pi, with a real battery or with a real inverter. It is tested without hardware (see "Tests").
- Tab 2 shows one of three sources, and a sidebar badge says which: **Live Pi**, **Demo bus**, or **Pure simulation**.
- Every euro, carbon and hub figure is a **project assumption**, not a measurement. They all live in the `ASSUMPTIONS` dict at the top of `backend/main.py`.
- The 1,500 ms precharge hold is a design value of this project.

### Layout

```
compose.yaml                  Docker Compose for the API and the dashboard (not the gateway)
requirements.txt              API, dashboard and tests
requirements-gateway.txt      What the Pi needs (python-can, gpiozero, lgpio)
backend/main.py               FastAPI: triage, CAN gateway simulation, assumptions
frontend/app.py               Streamlit, 4 tabs, sidebar badges for API mode and Tab 2 source
shared/can_frames.py          The one implementation of the Pylontech style frames
shared/gateway_rules.py       The one implementation of the gateway rules
gateway/pi_can_gateway.py     Gateway service for the Pi (live and demo modes)
gateway/fake_bms.py           Fake BMS frames for the demo
gateway/config.toml           CAN channels, GPIO pins, pack values, HTTP port
gateway/systemd/              Units that start the gateway on boot
docs/PI_SETUP.md              Step by step Pi setup, wiring and bench tests
tests/                        pytest, no hardware needed
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

# terminal 2: gateway in demo mode, status page on http://localhost:8100/state
python -m gateway.pi_can_gateway --mode demo

# terminal 3: dashboard on http://localhost:8501
streamlit run frontend/app.py
```

The sidebar has two badges:

- `Mode: API` or `Mode: in process fallback` shows whether the dashboard reached the API. Without the API it calls the same backend functions in process.
- `Tab 2 source` shows `Live Pi`, `Demo bus (memory)` or `Pure simulation`. With no gateway running, Tab 2 uses the software simulation. A checkbox in the sidebar forces the simulation even when a gateway answers.

On a Mac the demo gateway uses an in memory CAN bus and mock GPIO, because Linux vcan and real GPIO do not exist there. On a Linux machine it tries vcan interfaces first.

To point the dashboard at other addresses: `BACKEND_URL=http://host:8000 GATEWAY_URL=http://secondpulse.local:8100 streamlit run frontend/app.py`.

### Tests

```bash
python -m pytest
```

The tests cover the tier rules, the CAN payloads, the precharge hold, derating, isolation masking, the imbalance cutoff, the BMS timeout, the mock GPIO fallback, the relay interlock, the full demo chain on an in memory bus, and the HTTP status page. No hardware is needed.

### Run with Docker (second option)

Start Docker Desktop first.

```bash
docker compose up --build
```

Backend on http://localhost:8000, dashboard on http://localhost:8501. The image builds on arm64 and on x86.

The **gateway does not run in Docker**. It needs the host's CAN interfaces and GPIO, so run it directly on the host (on the Pi, or in demo mode on your Mac). The dashboard container reads the gateway through `GATEWAY_URL`, which defaults to `http://host.docker.internal:8100`. Build once before the demo so that nothing is downloaded on stage. Stop with `docker compose down`.

### Tier rules

Checked in this order, so every input lands in exactly one tier:

1. Tier 3 (blocked) if the pack has a flagged OEM defect, or cell spread is above 100 mV, or SOH is below 62.
2. Tier 1 if SOH is 74 or higher and cell spread is 45 mV or less.
3. Tier 2 for everything else.

Each tier shows its own saving, computed from the assumptions: Tier 1 is 1 minus 63/165, Tier 2 is 1 minus 76/165. Tier 3 shows no saving.

### CAN frames

Pylontech style frames, 8 bytes each, little endian, built with `struct.pack` in `shared/can_frames.py`. The API simulation and the Pi gateway both use that module.

| ID | Bytes | Content |
| --- | --- | --- |
| 0x351 | 0 to 1, 2 to 3, 4 to 5, 6 to 7 | charge voltage limit, charge current limit, discharge current limit, discharge voltage limit (all 0.1 units, unsigned) |
| 0x355 | 0 to 1, 2 to 3, 4 to 7 | SOC in percent, SOH in percent, zero padding |
| 0x356 | 0 to 1, 2 to 3, 4 to 5, 6 to 7 | voltage in 0.01 V (unsigned), current in 0.1 A (signed), temperature in 0.1 C (signed), zero padding |
| 0x359 | 0, 1, 2, 3, 4, 5 to 6, 7 | protection 1, protection 2, alarm 1, alarm 2, pack count, ASCII "PN", zero |

The layout was checked against `PYLON-LV-CAN.cpp` in the Battery Emulator repository. Differences, all also noted in the code:

- The emulator sends shorter frames for 0x351 (6 bytes) and 0x355. Here every frame is padded to 8 bytes.
- The 0x356 voltage is signed 16 bit in the low voltage protocol, which tops out at 327.67 V. These packs run at 350 V and above, so it is unsigned here. Whether a real inverter accepts this is not tested.
- The protocol has no cell imbalance bit. The imbalance cutoff is reported as the system error bit (0x80) in 0x359 byte 1. The original spec used 0x04 in byte 0, which in this protocol means cell undervoltage.

The OEM side IDs (0x1DB, 0x55B and others) in the registry are reference labels for display only. The gateway decodes BMS frames only through a profile (see below).

### Gateway

`gateway/pi_can_gateway.py` runs on a Raspberry Pi with SocketCAN. It reads native BMS frames on `can0`, applies the rules in `shared/gateway_rules.py`, drives the precharge and main relays over GPIO (gpiozero with the lgpio backend, which works on a Pi 5 as well as older boards), and sends the translated frames on `can1`.

Rules, the same as in the API simulation:

- CAN TX is held until 1,500 ms after the enable input goes high. The precharge relay is on during the hold, the main relay after it.
- While the inverter isolation test pulse runs, the BMS isolation flag is masked, plus a short grace time (`iso_mask_grace_ms`) because the BMS flag lags the test. Outside a pulse the flag is a real fault and cuts the limits.
- Current limit is 0.2C, halved outside 5 to 38 C, charge cut to a quarter above 88 percent SOC, discharge cut to a quarter below 18 percent SOC.
- A cell spread above 100 mV, an unmasked isolation fault, or no BMS frames for 2 seconds sets both current limits to zero and the system error bit in 0x359.
- The two relays are never on together.

Modes:

- `--mode live` uses real `can0`, `can1` and GPIO. `--require-gpio` makes it refuse to start with mock relays (the systemd unit sets it). Without that flag, a missing GPIO falls back to a mock factory with a loud warning.
- `--mode demo` needs no hardware. It tries Linux vcan interfaces and falls back to an in memory bus. GPIO is always mocked. `gateway/fake_bms.py` generates the BMS frames, and an enable signal, isolation pulses and a cell imbalance episode run on a timer.

Pins, channels, pack values and the HTTP port are in `gateway/config.toml`, not in code. The config loader rejects unknown keys and pins that clash with the CAN HAT.

The gateway serves a **read only** status page: `GET /state` and `GET /health` on port 8100. There is no login, so keep it on a trusted network.

**BMS profiles.** Native BMS frames are decoded by a profile in `gateway/profiles.py`. Only the `demo` profile exists. To support a real pack, add a class with the same `decode` method, register it in `PROFILES`, and set `profile` in the config.

Setup on the Pi, wiring, bench tests with `candump` and `cansend`, systemd, and USB CAN adapters: see [docs/PI_SETUP.md](docs/PI_SETUP.md).

### Demo script for the two Saturday expert sessions

Session 1: Kristiāns Kreinbergs (RTU Electronic Systems) and Aleksandrs Mihailovs (MAA intelengineering).

1. Start the gateway in demo mode and open Tab 2. The badge reads `Demo bus`. Say clearly that the BMS is fake and nothing real is switched. If you run it on the Pi with vcan, show `candump vcan1` next to the dashboard.
2. Watch the precharge hold: after each enable drop (every 30 s) the contactor shows PRECHARGING for 1.5 s and the inverter side frames stop, then CLOSED and they resume. Ask whether 1,500 ms is right for their inverters, since it is a design value.
3. Watch `iso_610h_masked` become true during the isolation pulse (every 10 s) without the limits dropping.
4. Wait for the imbalance episode (every 60 s): both current limits go to zero and 0x359 shows the system error bit. For a quicker look, tick the force simulation checkbox and use the imbalance fault switch.
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
- That the gateway works on a real Pi with a real CAN HAT, real relays and a real BMS. Nothing here has run on hardware yet. The overlay lines in the Pi guide come from the Waveshare wiki and were not tested on our board.
- That the Linux vcan demo path works. It was written but could not be run on the Mac or in Docker Desktop, whose kernel has no vcan module. The in memory fallback was run.
- Real BMS frame layouts for the packs in the registry. The only profile is a made up demo one.
- Battery Emulator support for each pack in the registry, and parallel operation of 2 or 3 identical packs.
- The OEM CAN ID labels, including the VW MEB one.
- Pack data in the registry: nominal voltage, series cell counts and chemistry.
- Hardware prices. The Raspberry Pi 4 and CAN HAT are not priced yet, and the supply, relay and fuse prices are assumptions. The earlier 15 euro board price no longer applies, because the gateway board changed.
- Solar yield (3.8 kWh per kWp per day), energy per scooter charge and per cargo e bike charge.
- That Riga pilot sites and organisations have agreed to anything. They are ideas.
- That reusing a whole pack without teardown is permitted under EU battery rules. Needs a legal source.

### Team workflow

Branch per person or feature, pull requests into main, nobody pushes straight to main after the first push. Folder owners (backend, frontend, gateway and others) are listed in [CONTRIBUTING.md](CONTRIBUTING.md), with the names still to be filled in.
