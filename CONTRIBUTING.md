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
4. Before you open a pull request, run `python -m pytest` and make sure it passes.
5. Push your branch and open a pull request into `main` on GitHub.
6. The owner of each folder you touched reviews the pull request. Merge when the review is done.
7. Never force push and never rewrite history on a shared branch.
8. Never commit secrets, keys, `.env` files or serial port credentials.

## Folder owners

| Folder | Owner |
| --- | --- |
| `backend/` and `tests/` | `<fill in name>` |
| `frontend/` | `<fill in name>` |
| `firmware/` | `<fill in name>` |
| `compose.yaml`, `README.md`, `CONTRIBUTING.md` | `<fill in name>` |

## Rules for text

- Money, carbon and lifetime numbers must come from the `ASSUMPTIONS` dict in `backend/main.py`. Do not type them into the UI.
- Label them as project assumptions until a source is added to the README list.
- Do not describe the firmware or Tab 2 as controlling real hardware. Both are simulations.

## Optional repository setting

To make the "no direct pushes to main" rule enforced, turn on branch protection for `main` in the repository settings on GitHub and require a pull request before merging.
