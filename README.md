# gemini-web-image

Generate and export images through the logged-in Gemini web app instead of direct APIs.

This repository contains a Codex skill plus helper scripts for:

- reusing an already logged-in local browser profile
- launching Gemini in headless mode
- generating an image through the Gemini web UI
- retrying through transient UI failures and capturing diagnostics
- exporting the rendered image when the normal download button is unreliable in headless mode

## What It Supports

- configurable browser selection: `brave`, `chrome`, `chromium`
- reuse of an existing desktop browser login
- headless execution by default
- CDP-based image export fallback for Gemini's in-page `blob:` images

## Repository Layout

- `SKILL.md`: Codex skill definition
- `scripts/run_gemini_image_generation.py`: robust end-to-end runner with retries, state polling, and export fallback
- `scripts/start-headless-browser.sh`: clones the browser profile and starts a headless browser with CDP
- `scripts/stop-headless-browser.sh`: stops the browser process and removes the temporary profile copy
- `scripts/prepare_gemini_image_mode.py`: quickly ensures the Gemini page is in `Pro` mode with `制作图片` selected
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
2. Run the robust end-to-end workflow:

```bash
python3 ./scripts/run_gemini_image_generation.py \
  --browser brave \
  --desktop-user user \
  --prompt "画一张极简中文系统架构图：用户端、API网关、服务层、Redis、MySQL，白底蓝色。" \
  --output ./outputs/generated-image.png \
  --artifacts-dir ./outputs/generated-image-artifacts
```

   This is the preferred path. It starts the copied profile, verifies Gemini loaded with the reused login, applies `Pro` + `制作图片`, submits the prompt, waits for the final image, exports it, and cleans up. If Gemini's normal download button stalls in headless mode, it falls back to CDP export automatically.

3. Start a headless browser from the copied profile only if you need to debug a specific step:

```bash
./scripts/start-headless-browser.sh \
  --browser brave \
  --desktop-user user \
  --session gemini-image-run \
  --download-dir ./outputs
```

4. Read the returned JSON and note `port`.
5. Connect `agent-browser` to the running browser:

```bash
agent-browser --session gemini-image-run connect <port>
agent-browser --session gemini-image-run open https://gemini.google.com/app
agent-browser --session gemini-image-run snapshot -i -c
```

6. Prepare and submit Gemini image generation through the fast CDP helper:

```bash
python3 ./scripts/prepare_gemini_image_mode.py \
  --cdp-port <port> \
  --prompt "画一张极简中文系统架构图：用户端、API网关、服务层、Redis、MySQL，白底蓝色。" \
  --submit
```

   The helper only opens the mode selector when `Pro` is not already active, only clicks `制作图片` when it is not already selected, fills the prompt box directly, and submits the request. It matches both Chinese and English Gemini UI labels. In this workflow, `Pro` + `制作图片` is treated as the `Nano Banana Pro` path.

7. If the built-in Gemini download button is unreliable in headless mode, export the image directly from the page:

```bash
python3 ./scripts/save_gemini_image_from_page.py \
  --cdp-port <port> \
  --output ./outputs/generated-image.png
```

8. Clean up the temporary browser profile:

```bash
./scripts/stop-headless-browser.sh --pid <pid> --temp-profile-root <temp_profile_root>
```

## Notes

- The scripts intentionally clone the browser profile into a temporary directory before launching headless mode. That avoids locking or corrupting the live profile used by the visible browser window.
- If Gemini opens but shows `登录` instead of the account button, the cloned profile could not reuse the authenticated session. Common causes are wrong browser choice, wrong desktop user, or missing D-Bus access to the desktop keyring.
- `scripts/run_gemini_image_generation.py` is the preferred entrypoint for reliability. It retries page setup, records snapshots and screenshots for every critical stage, and falls back to CDP export when the browser download path is unstable.
- `scripts/prepare_gemini_image_mode.py` is the preferred way to establish the `Pro` + `制作图片` state, fill the prompt, and submit quickly. Fall back to manual `agent-browser` clicks only if the page structure changes and the helper cannot confirm readiness.
- The repo does not contain cookies, account data, or browser profiles. It only contains automation logic.
