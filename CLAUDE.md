# Roastline

Use the deterministic installer when a user asks to install or set up Roastline.
Never edit Claude's `settings.json` by hand or replace an existing statusline script.

- In an interactive terminal, run `bash install.sh`. The installer asks for a tone.
- In agent/headless setup, ask the user whether they want SFW or NSFW first, then
  run `bash install.sh --non-interactive --tone sfw` or the explicitly requested
  `--tone nsfw`. If not possible to ask, use SFW.
- Use `python3 -I install.py --dry-run` for a read-only install preview.
- To manage an installation, use `roastline --help`, `--status`, `--tone`,
  `--update`, or `--uninstall`. If the launcher isn't on `PATH`, call
  `python3 -I install.py` with the corresponding flag from this checkout.
  Do not purge backups unless the user expressly requests it.
- Follow the installer output. Do not try to bypass project or managed settings.
