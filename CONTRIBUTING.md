# Contributing

## Team flow

1. Nobody pushes straight to `main`. The first push is the only exception.
2. One branch per person or per feature, started from an up to date `main`:
   ```bash
   git switch main
   git pull
   git switch -c yourname/short-feature-name
   ```
3. Commit small and often, with a clear message that says what changed.
4. Before you open a pull request, run `python -m pytest` and make sure it passes. The tests need no hardware.
5. Push your branch and open a pull request into `main` on GitHub.
6. The owner of each folder you touched reviews the pull request. Merge when the review is done.
7. Never force push and never rewrite history on a shared branch.
8. Never commit secrets, keys, `.env` files or passwords.

## Folder owners

| Folder | Owner |
| --- | --- |
| `backend/`, `shared/` and `tests/test_backend.py` | `<fill in name>` |
| `frontend/` | `<fill in name>` |
| `gateway/`, `docs/PI_SETUP.md` and `tests/test_gateway.py` | `<fill in name>` |
| `compose.yaml`, `README.md`, `CONTRIBUTING.md` | `<fill in name>` |

`shared/` holds the one implementation of the CAN frames and the gateway rules. The API simulation and the Pi gateway both use it, so a change there affects both owners. Ask the backend owner and the gateway owner to review it.

## Rules for the gateway

- Keep one implementation. Do not copy frame encoding or gateway rules into `gateway/` or `backend/`. Change `shared/` instead.
- Pins, CAN channel names and pack values belong in `gateway/config.toml`, not in code.
- A Linux board is not a safety rated controller. The real protection must be the hardware fuse, the battery's own BMS and the contactors. The Pi must never be the only safeguard. Do not write code, comments or documents that suggest otherwise.
- GPIO pins are 3.3 V only. Relay and contactor coils go through a relay board or driver stage, never straight to a pin.
- Describe the gateway as our own Python implementation inspired by the Battery Emulator project. It is not a port of that firmware. Do not copy code from that project without checking its license.
- Only the `demo` BMS profile exists, and its layout is made up. A new profile needs a source for the frame layout in its docstring.
- A change to relay or contactor behaviour needs a second person to review it before it is merged, and a bench test with no battery connected before it goes near one.

## Rules for text

- Money, carbon and lifetime numbers must come from the `ASSUMPTIONS` dict in `backend/main.py`. Do not type them into the UI.
- Label them as project assumptions until a source is added to the README list.
- Say which source Tab 2 is showing. Do not describe the software simulation or the demo mode as controlling real hardware.

## Optional repository setting

To make the "no direct pushes to main" rule enforced, turn on branch protection for `main` in the repository settings on GitHub and require a pull request before merging.
