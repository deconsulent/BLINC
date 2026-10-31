# SecondPulse: Next Steps for Claude Code (after the CAN board is fitted)

Paste this file into Claude Code once the CAN board is physically on the Raspberry Pi 4 Model B. Read CONTEXT.md first if you have no other background. Work on the pi-gateway branch, or a new branch named can-bringup if pi-gateway is already merged. Never push straight to main and never force push.

## Before you start

Ask me these three things and wait for the answers. Do not guess.

1. The name printed on the CAN board.
2. The number printed on the metal oscillator can (for example 8.000, 12.000 or 16.000 MHz).
3. Whether the board has one or two MCP2515 chips, or whether I am using two USB CAN adapters instead.

Then check the repo state with git status, git log and git remote, and tell me whether the pi-gateway work was pushed and whether a pull request exists. If anything is uncommitted, commit it in small commits first, including the fixes to docs/PI\_SETUP.md.

## Step 1: Bring up the CAN interfaces

1. Write the exact overlay lines for my board and the oscillator value into the Pi 4 boot config, and show me the lines before changing the file. Back up the original config first.
2. Tell me when to reboot. After the reboot, confirm that can0 and can1 exist, bring them up at 500 kbps, and show the output of ip link and dmesg for the CAN chips.
3. If an interface does not appear, diagnose it step by step (wrong oscillator value, SPI not enabled, wrong interrupt pin) and fix one thing at a time.
4. Update docs/PI\_SETUP.md with the final, tested commands for this board.

## Step 2: Loopback test with a cable

1. Tell me exactly how to connect the two channels: CAN H to CAN H, CAN L to CAN L, and where to enable the 120 ohm termination on both channels. If the board has no termination jumper, tell me what resistors to add.
2. Run candump on can1 and send test frames from can0 with cansend, then the other way around. Show me that the frames cross.
3. Run the gateway in live mode with the fake battery source on can0, and confirm that the translated Pylontech style frames 0x351, 0x355, 0x356 and 0x359 arrive on can1 with correct 8 byte payloads and correct decoded values.
4. Check the 1500 ms hold, the isolation pulse handling, the current derating and the imbalance cutoff in live mode, and report the real results.
5. Add an automated loopback test script that only runs when real CAN interfaces are present and skips cleanly otherwise.

## Step 3: Relays and GPIO

1. Pick GPIO pins for the contactor and precharge relays that do not clash with the pins the CAN board uses. Put them in the config file and show me a pin map.
2. Write the wiring guide for a 2 channel optically isolated relay board with its own coil supply. Remind me that the GPIO is 3.3 volt only. For the demo, use LEDs or lamps on the relay outputs. Never connect a contactor coil or any high voltage directly to the Pi.
3. Test the relay sequence with the hardware: precharge relay first, main relay after 1500 ms, both off on any fault. Add a fail safe so that if the gateway process crashes or loses its heartbeat, the relays drop to the safe state.

## Step 4: Dashboard connection

1. Connect the Streamlit dashboard on my Mac to the gateway on the Pi over the network. Use a fixed name or address for the Pi and note it in the README.
2. Tab 2 must show the badge for the active source: live Pi, demo bus, or pure simulation. Confirm that it switches correctly when I unplug the network cable.
3. Make sure the dashboard still works with no Pi at all.

## Step 5: Start on boot

Only do this after the loopback test and the relay test pass, and only after I confirm.

1. Install the systemd service so the gateway starts on boot, restarts on failure, and brings up can0 and can1 before it starts.
2. Reboot the Pi and prove that the gateway comes up by itself and the frames flow.
3. Show me how to read the logs with journalctl and how to stop the service safely.

## Step 6: Real battery profile (paper work only)

Do not connect any real battery. Do this as documentation and code, with tests, nothing more.

1. Ask me which exact battery pack we would target.
2. Find the documentation for that pack's CAN messages, including IDs, byte layout and scaling. Cite the source for every field. If a field cannot be sourced, mark it as unverified in the profile instead of guessing.
3. Write a profile file for that pack, separate from the fake battery profile, with tests that decode recorded sample frames if we have any.
4. Add a clear warning in the README and in the profile file: no real pack may be connected until the profile is verified against real recordings, the hardware fuse and service disconnect are fitted, and someone with high voltage training is present.

## Step 7: Sources for the claims

Find real, checkable sources for each claim below and list them in the README with links. If you cannot find a source, say so plainly and propose softer wording for the slides.

- GitHub issue 1959 in the dalathegreat Battery-Emulator repository.
- The Jaguar I-PACE early pouch tab defect.
- The SMA Battery\_Undervoltage fault and the 1500 ms pre charge timing.
- The 0x610 isolation test collision.
- The Pylontech frame layout for 0x351, 0x355, 0x356 and 0x359. Compare it with the Battery-Emulator code.
- The 72 kg CO2e per kWh embodied carbon figure and the cost per kWh figures.

## Step 8: Demo readiness for Sunday

1. Write a one page demo script for the jury: dashboard first, then the cable loopback with candump on screen, then the relay LEDs reacting to the 1500 ms delay, then the fault cases.
2. Add a backup plan: a short screen recording of demo mode, and a one command way to switch the dashboard to pure simulation if the Pi or network fails.
3. Run the full test suite, the demo mode and the live loopback one last time, and report what actually ran and what did not.
4. Open or update the pull request into main with a clear description and give me the link.

## Rules that always apply

- No hyphens and no em dashes in comments, docstrings, UI text or docs. File names and code syntax are exempt.
- Human sounding, plain, factual wording. No marketing words.
- No TODO placeholders. Everything must run.
- Small commits with clear messages. Never force push.
- If a push fails because of authentication or permissions, stop and tell me exactly what to run.
- Do not claim anything works unless you ran it. At the end, tell me what you did, what you ran, what you changed, and what you could not verify.
- Be honest in all text: the gateway is our own Python implementation, a Linux board is not a safety rated controller, and the Pi must never be the only safeguard. The real protection is the hardware fuse, the battery's own BMS and the contactors.
