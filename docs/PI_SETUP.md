# Raspberry Pi setup for the SecondPulse gateway

This guide sets up the gateway on a Raspberry Pi 4 Model B with a dual channel CAN HAT, step by step.

**Assumptions in this guide**

- Board: Raspberry Pi 4 Model B (2018). Operating system: Raspberry Pi OS Lite, 64 bit, Bookworm. The boot config file is `/boot/firmware/config.txt`.
- CAN board: a **dual channel MCP2515 HAT**, such as the Waveshare 2 channel CAN HAT. This is the default because the exact board was not confirmed. The overlay lines in step 3 are for that board family. If your board is different, read your board's documentation for the oscillator and interrupt values, and do not copy these lines blindly.
- If you use two USB CAN adapters instead, skip steps 3 and 4 and read the section "USB CAN adapters" at the end.

**Honest status**: nobody has run this guide on a real Pi yet. The gateway code is tested without hardware, and the Python dependencies were checked to install on arm64 Linux in a container. The overlay lines come from the Waveshare documentation. The first run on your Pi is the real test, so follow the checks after each step.

## Read this first: safety

The Raspberry Pi is a Linux computer. It is **not a safety rated controller**. It can crash, hang, lose power or be updated at the wrong moment.

**The Pi must never be the only safeguard.** The real protection must be:

1. a properly rated **hardware fuse** (and a manual service disconnect) on the high voltage side,
2. the **battery's own BMS** and its internal contactors,
3. the **contactors** themselves, chosen so they open when their coil loses power.

The gateway adds current limits and cutoffs on top of that. It does not replace any of it. Do not work on high voltage equipment unless you are qualified to, and do your first tests with no battery connected.

**GPIO pins are 3.3 volt only.** A pin can supply only a few milliamps. Never connect a pin to a contactor coil or a relay coil directly. Use an opto isolated relay board or a driver stage (transistor or MOSFET with a flyback diode) with its own supply for the coil, and let the relay contacts switch the contactor coil supply. Never apply more than 3.3 V to any GPIO pin, including from the inverter enable signal. If the enable signal is 5 V, 12 V or 24 V, use a level shifter or an opto coupler.

## 1. Flash the operating system

1. On your Mac install **Raspberry Pi Imager** from raspberrypi.com/software.
2. Choose device **Raspberry Pi 4**, operating system **Raspberry Pi OS (other)** then **Raspberry Pi OS Lite (64 bit)**, and your SD card.
3. Open the settings in Imager and set: hostname `secondpulse`, username `gwadmin` with a password of your choice, your wireless network if you use one, and enable SSH with password authentication.
4. Write the card, put it in the Pi and power it on. Wait two minutes.

## 2. First login and updates

On your Mac:

```bash
ssh gwadmin@secondpulse.local
```

On the Pi:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y python3-venv python3-dev build-essential rsync can-utils git
sudo reboot
```

Wait one minute, then log in again with the same `ssh` command.

## 3. Enable the CAN HAT overlays

Switch the Pi off, fit the HAT on the 40 pin header, then power on and log in. Open the boot config:

```bash
sudo nano /boot/firmware/config.txt
```

Add these lines at the end of the file (keep any existing lines):

```
dtparam=spi=on
dtoverlay=mcp2515-can1,oscillator=16000000,interrupt=25
dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=23
```

Save with Ctrl+O, Enter, then exit with Ctrl+X. Reboot:

```bash
sudo reboot
```

These are the values the Waveshare wiki gives for the original 2 channel CAN HAT. The newer "HAT+" model uses different overlays and different interrupt pins, so check which one you own. The interrupt pins 23 and 25 and the SPI pins are used by the HAT, which is why they are listed in `reserved_pins` in `gateway/config.toml`.

**Check it worked** after the reboot:

```bash
dmesg | grep -i mcp251
ip -details link show can0
ip -details link show can1
```

You should see two `mcp251x` lines in `dmesg`, and both `can0` and `can1` listed. If an interface is missing, recheck the three lines above and that the HAT is seated.

## 4. Bring up can0 and can1 at 500 kbps

Quick manual test (does not survive a reboot):

```bash
sudo ip link set can0 up type can bitrate 500000
sudo ip link set can1 up type can bitrate 500000
sudo ip link set can0 txqueuelen 1000
sudo ip link set can1 txqueuelen 1000
ip -details link show can0 | grep -E "state|bitrate"
```

Both should show `state UP` and `bitrate 500000`. Step 11 makes this permanent with a systemd unit.

## 5. Install the gateway

On your Mac, copy the project to the Pi (this skips your virtual environment and git data):

```bash
rsync -a --exclude .venv --exclude .git --exclude __pycache__ ~/secondpulse_bess/ gwadmin@secondpulse.local:/tmp/secondpulse_src/
```

On the Pi, create a service user and put the code in `/opt/secondpulse`:

```bash
sudo adduser --system --group --home /opt/secondpulse --shell /usr/sbin/nologin secondpulse
sudo usermod -aG gpio secondpulse
sudo rsync -a /tmp/secondpulse_src/ /opt/secondpulse/
sudo chown -R secondpulse:secondpulse /opt/secondpulse
```

Create the virtual environment and install the dependencies (this compiles `lgpio`, which takes a minute or two):

```bash
sudo -u secondpulse python3 -m venv /opt/secondpulse/.venv
sudo -u secondpulse /opt/secondpulse/.venv/bin/pip install --upgrade pip
sudo -u secondpulse /opt/secondpulse/.venv/bin/pip install -r /opt/secondpulse/requirements-gateway.txt
```

Check the install:

```bash
sudo -u secondpulse /opt/secondpulse/.venv/bin/python -c "import can, lgpio; from gpiozero.pins.lgpio import LGPIOFactory; print('imports ok')"
```

## 6. Test the gateway in demo mode (no wiring needed)

Demo mode uses a fake BMS and mock GPIO. It never touches a real pin. Create two virtual CAN interfaces so you can watch the traffic:

```bash
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan
sudo ip link add dev vcan1 type vcan
sudo ip link set up vcan0
sudo ip link set up vcan1
```

Terminal 1, run the gateway:

```bash
cd /opt/secondpulse
sudo -u secondpulse /opt/secondpulse/.venv/bin/python -m gateway.pi_can_gateway --mode demo
```

The log line should say `bus=vcan gpio=mock`. If it says `bus=memory`, the virtual interfaces could not be opened, and the demo still works but you cannot watch it with `candump`.

Terminal 2, watch the inverter side frames. After about 1.5 seconds you should see four frames per second (0x351, 0x355, 0x356, 0x359):

```bash
candump vcan1
```

Terminal 3, read the state page:

```bash
curl -s localhost:8100/state | python3 -m json.tool | head -30
```

Stop the gateway with Ctrl+C.

## 7. Wire the relays and the enable signal

Default pins (BCM numbers, set in `/opt/secondpulse/gateway/config.toml`):

| Function | BCM pin | Header pin |
| --- | --- | --- |
| Precharge relay output | 17 | 11 |
| Main contactor relay output | 27 | 13 |
| Inverter enable input (high means enabled) | 22 | 15 |

- Use a relay board with opto isolation and a separate coil supply (see the safety section). Connect the board's input to the pin, its ground to a Pi ground (header pin 9 or 14), and its coil supply to its own supply.
- GPIO 9 and above start low at boot, so an active high relay board stays off while the Pi boots. If your relay board is active low, set `relays_active_high = false` in the config and check what it does at boot before connecting anything.
- Fit a flyback diode across every contactor coil that is not already protected.
- Choose contactors that **open** when their coil loses power. A crashed Pi must lead to open contactors.
- If your HAT covers the header pins above, choose other free pins in `config.toml`. They must not be in `reserved_pins`. The gateway refuses to start if you pick a reserved pin or two pins that are the same.

Edit the config if you change anything:

```bash
sudo -u secondpulse nano /opt/secondpulse/gateway/config.toml
```

Also set the `[pack]` section to match your pack (`nominal_kwh`, `nominal_voltage`, `cells_series`).

## 8. The BMS profile

The gateway decodes the BMS frames with a **profile**. Only a `demo` profile exists, and its frame layout is made up for this project. It does **not** match any real vehicle. Before you connect a real battery, someone must write a profile for that battery in `gateway/profiles.py` from the vehicle's CAN documentation (or from the Battery Emulator source), register it in `PROFILES`, and set `profile = "your_name"` in `config.toml`. Until then the gateway can only run the demo chain and bench tests.

## 9. Quick bench test with candump and cansend

This tests the HAT and the gateway chain with no battery and no inverter. Connect CAN_H of channel 0 to CAN_H of channel 1 and CAN_L to CAN_L with short wires. Turn on the 120 ohm termination on both channels (jumper or switch, see your HAT). Both channels are now on one small bus.

Make sure the interfaces are up (step 4), then:

**Test A, the HAT itself.**

Terminal 1:

```bash
candump can1
```

Terminal 2:

```bash
cansend can0 123#DEADBEEF
```

Terminal 1 must print `can1  123   [4]  DE AD BE EF`. If it does not, check the wires, the termination and the bitrate.

**Test B, the gateway chain.** Wire the enable input (BCM 22, header pin 15) to 3.3 V (header pin 1) through a 1 kilohm resistor. Leave the relay outputs unconnected for this test.

Terminal 1:

```bash
candump can1
```

Terminal 2, start the gateway in live mode:

```bash
cd /opt/secondpulse
sudo -u secondpulse /opt/secondpulse/.venv/bin/python -m gateway.pi_can_gateway --mode live --require-gpio
```

Terminal 3, send fake BMS frames every 100 ms (demo profile layout):

```bash
while true; do cansend can0 700#F00E00000C035100; cansend can0 701#960F860FF2000000; sleep 0.1; done
```

Terminal 1 now shows the BMS frames (700 and 701) and, starting 1.5 seconds after the gateway saw the enable input high, the four gateway frames once per second:

```
351   [8]  73 0F E5 00 E5 00 00 0C
355   [8]  4E 00 51 00 00 00 00 00
356   [8]  60 95 00 00 F2 00 00 00
359   [8]  00 00 00 00 01 50 4E 00
```

Now change the cell frame to a 140 mV spread. Press Ctrl+C in terminal 3 to stop the loop, then run:

```bash
while true; do cansend can0 700#F00E00000C035100; cansend can0 701#B40F280FF2000000; sleep 0.1; done
```

Within a second the 351 frame shows zero currents and 359 shows the system error bit:

```
351   [8]  73 0F 00 00 00 00 00 0C
359   [8]  00 80 00 00 01 50 4E 00
```

Finally press Ctrl+C in terminal 3 and wait. After about two seconds without BMS frames the gateway cuts the limits and reports a BMS timeout alarm. Stop the gateway with Ctrl+C in terminal 2.

## 10. Connect the dashboard

The gateway serves a read only state page at `http://secondpulse.local:8100/state`. It has no login, so keep the Pi on a trusted network and do not forward the port to the internet.

On your Mac:

```bash
GATEWAY_URL=http://secondpulse.local:8100 streamlit run frontend/app.py
```

With Docker, set the same variable before `docker compose up`. Tab 2 then shows a badge: **Live Pi** when the gateway runs in live mode, **Demo bus** when it runs in demo mode, and **Pure simulation** when it cannot be reached.

## 11. Start on boot with systemd

Only do this after the bench tests passed and the wiring was checked.

```bash
sudo cp /opt/secondpulse/gateway/systemd/secondpulse-can.service /etc/systemd/system/
sudo cp /opt/secondpulse/gateway/systemd/secondpulse-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now secondpulse-can.service
sudo systemctl enable --now secondpulse-gateway.service
```

Check it:

```bash
systemctl status secondpulse-can.service secondpulse-gateway.service
journalctl -u secondpulse-gateway.service -f
curl -s localhost:8100/health
```

The first unit brings up `can0` and `can1` at 500 kbps with a larger transmit queue. The second runs the gateway as the `secondpulse` user, restarts it after a failure, and uses `--require-gpio` so it refuses to start with mock relays. Reboot once (`sudo reboot`) and confirm both services come back on their own.

Optional: let the Pi hardware watchdog reset a frozen system. Add `dtparam=watchdog=on` to `/boot/firmware/config.txt`, then in `/etc/systemd/system.conf` set `RuntimeWatchdogSec=15`, and reboot. After a reset the relay pins return to their boot state (low), but this is a convenience, not a safety function.

## USB CAN adapters instead of a HAT

If you use two USB CAN adapters, no overlay and no change to `config.txt` is needed.

1. Plug both adapters in. Adapters that use the Linux `gs_usb` driver (candleLight style firmware) appear as `can0` and `can1` on their own. Check with `ip -details link show`.
2. Bring them up exactly as in step 4 (`sudo ip link set can0 up type can bitrate 500000`, and the same for `can1`), and use the systemd unit from step 11 unchanged.
3. Which adapter becomes `can0` depends on plug order. Label the cables, and if the roles are swapped, change `oem_channel` and `inverter_channel` in `config.toml` instead of moving cables.
4. Adapters that appear as a serial port (SLCAN style) need `slcand` and create `slcan0`, not `can0`. They are not covered here. Use `gs_usb` adapters.
5. The CAN HAT pins are then free, so you may empty `reserved_pins` in `config.toml`. The relay pins stay as they are.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| No `can0` or `can1` after reboot | The three lines in `config.txt`, the HAT seating, and the output of `dmesg` for mcp251 |
| `RTNETLINK answers: Device or resource busy` | The interface is already up. Run `sudo ip link set can0 down` first |
| Gateway refuses to start with `--require-gpio` | The `secondpulse` user is not in the `gpio` group, or `lgpio` is not installed in the venv |
| Gateway says a pin is reserved | Pick other pins in `config.toml`, or empty `reserved_pins` if you use USB adapters |
| Candump shows nothing on can1 | Wiring, termination and bitrate. Run test A first |
| `BMS_TIMEOUT` alarm | No BMS frames for more than 2 seconds, or the profile does not match the frames |
| Dashboard says Pure simulation | `curl http://secondpulse.local:8100/health` from the Mac, and check `GATEWAY_URL` |
