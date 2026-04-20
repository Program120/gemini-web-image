# gemini-web-image

Generate and export images through the logged-in Gemini web app instead of direct APIs.

This repository contains a Codex skill plus helper scripts for:

- reusing an already logged-in local browser profile
- launching Gemini in headless mode
- generating an image through the Gemini web UI
- exporting the rendered image when the normal download button is unreliable in headless mode

## What It Supports

- configurable browser selection: `brave`, `chrome`, `chromium`
- reuse of an existing desktop browser login
- headless execution by default
- CDP-based image export fallback for Gemini's in-page `blob:` images

## Repository Layout

- `SKILL.md`: Codex skill definition
- `scripts/start-headless-browser.sh`: clones the browser profile and starts a headless browser with CDP
- `scripts/stop-headless-browser.sh`: stops the browser process and removes the temporary profile copy
- `scripts/save_gemini_image_from_page.py`: exports the generated image from the active Gemini page through CDP

## Requirements

- `agent-browser`
- `python3`
- `rsync`
- `curl`
- `sudo` if the automation process is not already running as the desktop user that owns the browser profile
- Python package `websocket-client`

Install the Python dependency with:

```bash
pip install websocket-client
```

Install `agent-browser` with:

```bash
npm i -g agent-browser
agent-browser install
```

## Quick Start

1. Make sure the target desktop browser is already logged into `https://gemini.google.com/app`.
2. Start a headless browser from the copied profile:

```bash
./scripts/start-headless-browser.sh \
  --browser brave \
  --desktop-user user \
  --session gemini-image-run \
  --download-dir ./outputs
```

3. Read the returned JSON and note `port`.
4. Connect `agent-browser` to the running browser:

```bash
agent-browser --session gemini-image-run connect <port>
agent-browser --session gemini-image-run open https://gemini.google.com/app
agent-browser --session gemini-image-run snapshot -i -c
```

5. Generate an image through the Gemini UI.
   Before sending the prompt, always switch the Gemini mode selector to `Pro`.
   In this workflow, `Pro` + `制作图片` is treated as the `Nano Banana Pro` path.
6. If the built-in Gemini download button is unreliable in headless mode, export the image directly from the page:

```bash
python3 ./scripts/save_gemini_image_from_page.py \
  --cdp-port <port> \
  --output ./outputs/generated-image.png
```

7. Clean up the temporary browser profile:

```bash
./scripts/stop-headless-browser.sh --pid <pid> --temp-profile-root <temp_profile_root>
```

## Notes

- The scripts intentionally clone the browser profile into a temporary directory before launching headless mode. That avoids locking or corrupting the live profile used by the visible browser window.
- If Gemini opens but shows `登录` instead of the account button, the cloned profile could not reuse the authenticated session. Common causes are wrong browser choice, wrong desktop user, or missing D-Bus access to the desktop keyring.
- The repo does not contain cookies, account data, or browser profiles. It only contains automation logic.
