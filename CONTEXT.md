# SecondPulse: Project Context

This file holds everything that has happened so far on the SecondPulse project, so a fresh Claude Code session can pick up without any other explanation. Written Saturday 31 October 2026, shortly after midnight, Riga time. The jury demo is on Sunday 1 November 2026.

## 1. What the project is

SecondPulse is our entry for Climathon 2026, Track 3 (Green Mobility and Circular Economy). The idea is to give retired EV battery packs a second life as stationary solar storage without taking the pack apart. Two parts:

1. A triage tool that estimates the state of health (SOH) and remaining useful life (RUL) of a retired pack and sorts it into a tier.
2. A CAN bus gateway that sits between the untouched EV battery and a solar inverter. It reads the battery's native BMS messages, translates them into the Pylontech style messages the inverter expects, and sends them out on a second CAN channel.

The pilot idea is off grid solar micromobility charging hubs in Riga (for example the RTU Kipsala campus) and substation peak shaving buffers.

## 2. Original spec (AGENT\_BUILD\_SPEC.md)

The original spec was a single markdown file with full source code. Its structure:

- docker-compose.yml, requirements.txt
- backend/main.py: FastAPI app with a synthetic data RandomForest model for SOH, a 90 percent confidence interval from the spread of the tree predictions, an OEM battery database, and two endpoints: /api/triage and /api/can-gateway-simulate (plus /api/oem-models).
- frontend/app.py: Streamlit dashboard with four tabs.
- firmware/esp32\_can\_gateway/esp32\_can\_gateway.ino: an ESP32 sketch that only simulates the state machine and prints JSON over serial.

### OEM battery database in the spec

Nissan Leaf ZE1 40 kWh, VW MEB ID.3 and ID.4 58 kWh, BMW i3 33 kWh, Stellantis eCMP Peugeot e-208 50 kWh, and Jaguar I-PACE 90 kWh early revision. The Jaguar is flagged as a safety defect (the spec claims early LG pouch tabs disconnect internally), so it is always blocked.

### Tier logic in the spec

- Blocked, Tier 3 (recycling): OEM defect flag, or cell voltage spread above 100 mV, or SOH below 62 percent.
- Tier 1 (whole pack reuse through the CAN gateway): SOH 74 or higher and cell spread 45 mV or less.
- Tier 2 (module level 48 volt repackaging): everything else.

### Money and carbon assumptions in the spec

Tier 1 cost 63 euro per kWh, Tier 2 76, cell level teardown 113, new LFP storage 165. Avoided carbon is about 72 kg CO2e per usable kWh. These are project assumptions, not measurements.

### Gateway behaviour in the spec

- Hold CAN transmit until 1500 ms after the inverter enable signal (pre charge delay).
- Handle an inverter isolation test pulse (the spec calls it 0x610 arbitration).
- Current limit of about 0.2C for second life longevity, with derating for temperature and for very high or low SOC.
- Cut charge and discharge to zero on a cell imbalance fault (spread above 100 mV).
- Output Pylontech style frames 0x351, 0x355, 0x356 and 0x359.

### Four dashboard tabs

1. AI and ML SOH triage and safety gate.
2. Live CAN gateway.
3. Riga micromobility hub and ROI calculator.
4. Hardware architecture and protection specs (bill of materials about 102 euro for the gateway node).

### Saturday expert sessions in the spec

Demo Tab 2 and the hardware to the electronics and engineering mentors, and Tab 3 to the mobility and innovation mentors, stressing the 61.8 percent capital cost saving for Tier 1.

## 3. Problems found in the spec

- The compose file has working\_dir lines with no colon, so the YAML is invalid.
- The frontend imports backend functions directly and never calls the API.
- CAN payloads were loose hex groups, not real 8 byte frames.
- The simulation reseeds the global numpy generator on every call.
- The 61.8 percent saving was hardcoded for every pack, but it only applies to Tier 1. Euro per kWh values were scattered.
- The ML model trains on synthetic data from a formula. Fine for a demo, but it must be labelled.
- The firmware does not drive real CAN hardware and the isolation field never changed.
- Tier boundaries needed a check for gaps and overlaps.
- Claims we could not verify: GitHub issue 1959 in the dalathegreat Battery-Emulator repo, the Jaguar pouch tab defect, the SMA Battery\_Undervoltage fault, and the 0x610 collision. We need real sources before the jury asks.

## 4. What was sent to Claude Code, in order

1. Main build prompt. Build the project from the spec, fix the eight problems above, use compose.yaml, write real struct packed 8 byte frames, keep money numbers in one config dict, add a synthetic data note, make the firmware honest, write tests (Jaguar always blocked, healthy Leaf in Tier 1, TX held below 1500 ms, imbalance zeroes both limits, every payload 8 bytes), write a README with a Mac run guide, the demo script and a list of unsourced claims.
2. Git prompt. Push to the private repo https://github.com/deconsulent/climathon, project at the repo root, small commits, a .gitignore, CONTRIBUTING.md with a branch and pull request flow, never force push. GitHub login was already done in the terminal.
3. Change of plan: use a Raspberry Pi as the gateway instead of the ESP32. Work on a branch called pi-gateway and open a pull request. Replace the firmware with a Python gateway at gateway/pi\_can\_gateway.py using Linux SocketCAN (python can package), a shared frame encoding module, GPIO relays with gpiozero and lgpio and a mock fallback, live mode and demo mode (vcan, or an in memory bus on a Mac), an HTTP or websocket state endpoint for Tab 2 with a badge showing live Pi, demo bus or pure simulation, a docs/PI\_SETUP.md guide, a systemd service, tests that need no hardware, and honest README text.
4. Hardware update: the board is a Raspberry Pi 4 Model B (2018), Raspberry Pi OS 64 bit, assume a dual channel MCP2515 CAN HAT, also document two USB CAN adapters, and explain that GPIO is 3.3 volt only so relays need a proper relay board.

## 5. Current status (reported by Claude Code at about 23:53 on 30 October)

Done:

- The Pi works, login works, it has internet through the Mac, and the gateway program runs in test (demo) mode.

Not yet done:

- No CAN board is fitted yet. It is not known which board will be used.
- Relays are not wired.
- No cable loopback test has been run.
- Start on boot is not enabled, on purpose, until the loopback test passes.
- Only a fake battery profile exists. No real battery settings are written.
- The dashboard on the Mac is not yet connected to the Pi.
- Fixes to the Pi setup guide were made but not yet committed.
- The pi-gateway branch push and pull request status is unknown. It must be checked with git status, git log and git remote in the project folder.
- I could not inspect the Mac myself, because folder access was denied. Everything above about progress comes from what the user relayed.

## 6. Hardware facts and decisions

- Gateway computer: Raspberry Pi 4 Model B (2018). It has no CAN of its own.
- CAN options discussed: a dual channel MCP2515 HAT, two USB CAN adapters (CANable or Waveshare types), two MCP2515 modules wired to SPI, an ESP32 with a transceiver, an Arduino Due (two built in CAN controllers, needs 3.3 volt transceivers such as SN65HVD230), or an STM32 board. Decision so far: stay on the Pi.
- Virtual CAN is a real Linux feature and carries genuine CAN frames, but there are no wires or voltages, and in demo mode the battery data is invented. For the jury: this is the real gateway software on a simulated bus, never call it hardware.
- For the cable demo you need: two jumper wires (CAN H to CAN H, CAN L to CAN L), 120 ohm termination at both ends of the loop (most boards have a jumper), a 5 volt 3 amp USB C supply, an Ethernet cable to the Mac, and a 2 channel optically isolated relay board that accepts 3.3 volt signals, with its own coil supply. LEDs or lamps on the relay outputs are enough for the demo.
- For a real pack only, never without qualified supervision: DC rated contactors, a precharge resistor, a DC fuse, a manual service disconnect, a 12 volt supply for the BMS wake line, the exact connector and pinout, an inverter that accepts the messages, and insulated tools. These packs run at roughly 350 to 400 volts DC. The Pi must never be the only safeguard.

## 7. Team and workflow

- Repo: https://github.com/deconsulent/climathon (private).
- One branch per person or feature, pull requests into main, nobody pushes straight to main after the first push. Branch protection on main is a GitHub setting the repo owner has to switch on.
- Folder owners (backend, frontend, gateway) are placeholders to fill in.

## 8. Writing and style rules for all project text

- No hyphens and no em dashes in comments, docstrings, UI text and docs. File names and code syntax are exempt.
- Human sounding, plain, factual text with no marketing words.
- No TODO placeholders. Never claim something works unless it was actually run.

## 9. Honesty points for the jury

- The ML model is trained on synthetic data from a formula.
- The money figures are project assumptions.
- The gateway is our own Python implementation of the translation, inspired by the open source Battery Emulator project. It is not a port of its firmware.
- A Linux board is not a safety rated controller. The real protection is the hardware fuse, the battery's own BMS and the contactors.
- Experts will ask for sources on the issue 1959, Jaguar, SMA and 0x610 claims.
