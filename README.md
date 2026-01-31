# Stash FunGen Plugin

Generates funscripts for each Stash scene using the FunGen CLI.

## Why FunGen?
- It provides a CLI for batch generation and supports model/OD mode flags.
- It skips existing funscripts by default and can overwrite when requested.
- It saves a copy of the funscript next to the video unless `--no-copy` is used.

## Files
- `fungen_stash_plugin.yml`: Stash plugin definition.
- `fungen_stash_plugin.py`: External plugin runner (raw interface).

## Install
1. Install FunGen and note the path to its `main.py` (or a wrapper script).
2. Copy `fungen_stash_plugin.yml` and `fungen_stash_plugin.py` into your Stash plugins directory.
3. Edit `fungen_stash_plugin.yml` and set `fungen_path` to your FunGen `main.py`.
4. Reload plugins in Stash.

## Usage
- Run **Install FunGen CLI** once and set `fungen_repo` to the FunGen repo URL (optional `fungen_ref`).
- Run **Generate funscripts for all scenes** from Tasks.
- Automatic generation is enabled via the **Scene.Create.Post** hook.

## Plugin repository
This repo includes an `index.yml` and a packaged zip in `dist/` so you can add it as a Stash plugin source and install from the UI.

## Notes
- Existing `.funscript` files are skipped unless `overwrite: true`.
- The funscript output is placed next to the video file by default (unless `no_copy: true`).
