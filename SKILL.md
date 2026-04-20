---
name: gemini-web-image
description: Generate and download images through the logged-in Gemini web app instead of direct APIs. Use whenever the user wants Gemini网页生图, wants to reuse an existing local browser login such as Brave or Chrome, needs the browser choice to be configurable, or wants the workflow to run headlessly and save downloaded image files.
---

# Gemini Web Image

Use Gemini's web UI to generate and download images from a browser session that reuses an already logged-in desktop profile.

This skill is for browser-based Gemini image generation, not Gemini API calls.

## When To Use It

Use this skill when the user:

- wants Gemini web app image generation or download
- wants to reuse an already logged-in browser account
- wants the browser to be configurable, such as `brave`, `chrome`, or `chromium`
- wants the workflow to run headlessly without showing a page window

## Required Tools

- `agent-browser`
- a local desktop browser profile that is already logged into `https://gemini.google.com/app`
- `python3` with the `websocket-client` package available for the CDP export fallback

## Defaults

- Default browser: `brave`
- Default desktop user: `user`
- Default profile directory inside the browser data root: `Default`

## Important Constraints

- Always launch the browser as the desktop user that owns the logged-in browser profile. Do not launch the copied profile as `root`; that breaks GNOME keyring cookie decryption.
- Always clone the source browser profile into a temporary directory first. Do not point headless automation at the live profile directory because the user's visible browser may already be running and holding profile locks.
- Always set `XDG_RUNTIME_DIR` and `DBUS_SESSION_BUS_ADDRESS` for the target desktop user before launching headless Chromium. That is what lets the cloned profile reuse encrypted Google session cookies.
- Default to headless execution. Only switch to headed mode if the user explicitly asks.

## Browser Selection

Use `scripts/start-headless-browser.sh` to resolve browser settings.

Supported browser names:

- `brave`
- `chrome`
- `chromium`

If the user does not specify a browser, use `brave`.

## Workflow

1. Prefer the robust end-to-end runner for normal use:

```bash
python3 {baseDir}/scripts/run_gemini_image_generation.py \
  --browser brave \
  --desktop-user user \
  --prompt "<image prompt>" \
  --output /abs/output/image.png \
  --artifacts-dir /abs/output/run-artifacts
```

That runner is the default path for this skill. It handles:

- browser startup and cleanup
- logged-in session reuse checks
- `Pro` + `制作图片` preparation with retries
- page reload/open recovery when Gemini does not fully render
- polling until the generated image is actually ready
- export fallback when the web download button is flaky in headless mode
- artifact capture for debugging on both success and failure

2. Start a headless browser instance manually only when debugging:

```bash
{baseDir}/scripts/start-headless-browser.sh \
  --browser brave \
  --desktop-user user \
  --session gemini-image-run \
  --download-dir /abs/output/dir
```

The script prints JSON. Read these fields:

- `port`
- `pid`
- `temp_profile_root`
- `download_dir`
- `session`

3. Connect `agent-browser` to the returned CDP port:

```bash
agent-browser --session gemini-image-run connect <port>
```

4. Open Gemini and verify that login state was reused:

```bash
agent-browser --session gemini-image-run open https://gemini.google.com/app
agent-browser --session gemini-image-run snapshot -i -c
```

Logged-in state should show an account button similar to `Google 账号： ...`.

If the snapshot shows `登录` instead, stop and tell the user that the target browser profile is not logged in or could not be decrypted in the desktop session.

5. Drive the Gemini UI with the normal `agent-browser` snapshot loop:

- snapshot
- act
- snapshot again after every page change

Prefer text- and role-based interactions when the label is obvious. Fall back to snapshot refs when needed.

6. For image generation, prefer the fast CDP helper before the normal snapshot loop:

```bash
python3 {baseDir}/scripts/prepare_gemini_image_mode.py \
  --cdp-port <port> \
  --prompt "<image prompt>" \
  --submit
```

That helper uses CDP to inspect the current Gemini page and runs the full fast path in one pass:

- if `Pro` is already active, it does not reopen the mode selector
- if `制作图片` is already selected, it does not click it again
- if either state is missing, it performs the minimum clicks needed to make the page ready
- it fills the main Gemini prompt box directly
- it clicks `发送` and waits for generation to start
- it supports both Chinese and English Gemini UI labels
- it prints JSON with `mode_action`, `draw_action`, `prompt_action`, `submit_action`, and `elapsed_ms`

If the helper fails, fall back to the normal `agent-browser` snapshot loop:

- open the mode selector and switch to `Pro`
- verify the page now shows `Pro` as the active mode before prompt submission
- select `制作图片` if it is not already selected
- enter the image prompt in the main textbox
- submit the prompt
- wait for generation to finish

For this skill, treat `Pro` + `制作图片` as the required path for `Nano Banana Pro`.
Do not rely on Gemini's current default mode.
Do not reopen the mode selector immediately before submission if the helper already completed the fast path successfully.

Useful waits:

- `agent-browser wait --text "Pro"`
- `agent-browser wait --text "制作图片"`
- `agent-browser wait --text "下载"`
- `agent-browser wait 3000` only as a last resort

7. Download the generated image to the requested output directory.

Prefer a direct download button in the generated result card. If a menu is required, re-snapshot after opening it, then click the download action.

If Gemini's web download button is flaky in headless mode, use the CDP export fallback:

```bash
python3 {baseDir}/scripts/save_gemini_image_from_page.py \
  --cdp-port <port> \
  --output /abs/output/image.png
```

That script reads the largest Gemini result image from the current page, resolves the in-page `blob:` URL, and writes the binary image to disk.

8. Clean up when done:

```bash
{baseDir}/scripts/stop-headless-browser.sh \
  --pid <pid> \
  --temp-profile-root <temp_profile_root>
```

## Output Expectations

Report back with:

- which browser was used
- whether logged-in state was successfully reused
- absolute path of each downloaded image
- absolute path of the run artifacts directory when the robust runner is used
- any Gemini UI blockers encountered

## Troubleshooting

### Snapshot shows `登录`

The browser session is not authenticated. Common causes:

- wrong browser selected
- wrong desktop user selected
- missing D-Bus environment for GNOME keyring
- source browser profile is not logged into Gemini

### Browser launches but `agent-browser` cannot control it

Use the explicit CDP workflow:

```bash
agent-browser --session my-run connect <port>
agent-browser --session my-run open https://gemini.google.com/app
```

Do not rely on `agent-browser` to launch Brave directly for this workflow.

### Download does not start

- re-snapshot after opening the image result card
- look for a dedicated `下载` button or a menu button near the generated image
- if the page uses a popup menu, re-snapshot after opening the menu
- if headless download still fails, use `scripts/save_gemini_image_from_page.py` against the active CDP port
- prefer `scripts/run_gemini_image_generation.py` for normal use because it automatically falls back to export

### Wrong model path

- run `scripts/prepare_gemini_image_mode.py` again first
- if the helper cannot confirm the state, re-open the mode selector and select `Pro` manually
- only then proceed with `制作图片` prompt submission
