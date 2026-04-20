#!/usr/bin/env python3
"""Run a robust end-to-end Gemini web image generation workflow."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
import urllib.request

import websocket


SCRIPT_DIR = Path(__file__).resolve().parent
START_BROWSER_SCRIPT = SCRIPT_DIR / "start-headless-browser.sh"
STOP_BROWSER_SCRIPT = SCRIPT_DIR / "stop-headless-browser.sh"
PREPARE_SCRIPT = SCRIPT_DIR / "prepare_gemini_image_mode.py"
EXPORT_SCRIPT = SCRIPT_DIR / "save_gemini_image_from_page.py"

DEFAULT_PAGE_URL = "https://gemini.google.com/app"

PROMPT_TERMS = [
    "为 Gemini 输入提示",
    "Gemini 输入提示",
    "Ask Gemini",
    "Enter a prompt",
    "Message Gemini",
]
MODE_SELECTOR_TERMS = [
    "打开模式选择器",
    "mode selector",
    "mode picker",
    "models and tools",
]
LOGIN_TERMS = ["登录", "Sign in", "Log in"]
SHARE_IMAGE_TERMS = ["分享图片", "Share image"]
COPY_IMAGE_TERMS = ["复制图片", "Copy image"]
DOWNLOAD_IMAGE_TERMS = [
    "下载完整尺寸的图片",
    "Download full-sized image",
    "Download full size image",
]
STOP_TERMS = ["停止回答", "Stop response", "Stop responding", "Stop generating"]
GENERATING_TERMS = ["Nano Banana", "Creating your image", "正在加载"]


class RunnerError(RuntimeError):
    """Failure raised for actionable workflow problems."""


@dataclass
class BrowserSession:
    browser: str
    desktop_user: str
    port: int
    pid: int
    session: str
    temp_profile_root: str
    download_dir: str
    log_path: str


class ArtifactRecorder:
    """Persist snapshots, screenshots, and JSON diagnostics for each stage."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._counter = 1

    def _prefix(self, label: str) -> Path:
        prefix = self.directory / f"{self._counter:02d}-{label}"
        self._counter += 1
        return prefix

    def write_json(self, label: str, payload: Any) -> Path:
        path = self.directory / label
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return path

    def append_jsonl(self, label: str, payload: Any) -> Path:
        path = self.directory / label
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return path

    def write_text(self, label: str, content: str) -> Path:
        path = self.directory / label
        path.write_text(content, encoding="utf-8")
        return path

    def capture(self, session_name: str, label: str) -> dict[str, str]:
        prefix = self._prefix(label)
        snapshot_path = prefix.with_name(prefix.name + "-snapshot.txt")
        screenshot_path = prefix.with_suffix(".png")

        snapshot = agent_browser(session_name, "snapshot", "-i", "-c", check=False, timeout=45)
        snapshot_path.write_text(snapshot.stdout or "", encoding="utf-8")

        screenshot = agent_browser(
            session_name,
            "screenshot",
            os_fspath(screenshot_path),
            check=False,
            timeout=45,
        )
        if screenshot.returncode != 0 and not screenshot_path.exists():
            screenshot_path.write_text(screenshot.stderr or "", encoding="utf-8")

        return {
            "snapshot_path": os_fspath(snapshot_path),
            "screenshot_path": os_fspath(screenshot_path),
        }


def os_fspath(path: Path) -> str:
    return str(path)


def command_display(args: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in args)


def run_command(
    args: list[str],
    *,
    timeout: int = 60,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunnerError(f"Command timed out after {timeout}s: {command_display(args)}") from exc

    if check and result.returncode != 0:
        raise RunnerError(
            f"Command failed ({result.returncode}): {command_display(args)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def agent_browser(
    session_name: str,
    *args: str,
    timeout: int = 60,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run_command(["agent-browser", "--session", session_name, *args], timeout=timeout, check=check)


def _read_json(url: str) -> list[dict[str, Any]]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _pick_page(cdp_port: int, url_contains: str) -> dict[str, Any]:
    pages = _read_json(f"http://127.0.0.1:{cdp_port}/json/list")
    for page in pages:
        if page.get("type") != "page":
            continue
        if url_contains in page.get("url", ""):
            return page
    raise RunnerError(f"No page target found containing URL fragment: {url_contains}")


def _evaluate(ws_url: str, expression: str, cdp_port: int) -> Any:
    client = websocket.create_connection(
        ws_url,
        timeout=30,
        origin=f"http://127.0.0.1:{cdp_port}",
    )
    try:
        payload = {
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
                "userGesture": True,
            },
        }
        client.send(json.dumps(payload))
        while True:
            message = json.loads(client.recv())
            if message.get("id") != 1:
                continue
            if "error" in message:
                raise RunnerError(f"CDP error: {message['error']}")
            result = message.get("result", {})
            if "exceptionDetails" in result:
                raise RunnerError(json.dumps(result["exceptionDetails"], ensure_ascii=False))
            return result["result"]["value"]
    finally:
        client.close()


def inspect_gemini_state(cdp_port: int, url_contains: str) -> dict[str, Any]:
    try:
        page = _pick_page(cdp_port, url_contains)
    except RunnerError:
        return {
            "url": "about:blank",
            "is_blank": True,
            "requires_login": False,
            "has_prompt_input": False,
            "has_mode_selector": False,
            "has_download_image_button": False,
            "has_share_image_button": False,
            "has_copy_image_button": False,
            "has_stop_button": False,
            "has_active_pro": False,
            "has_draw_selected": False,
            "is_generating": False,
            "largest_image_area": 0,
            "largest_image": None,
            "body_excerpt": "",
            "visible_buttons": [],
            "has_error_text": False,
        }

    expression = f"""
    (() => {{
      const promptTerms = {json.dumps(PROMPT_TERMS, ensure_ascii=False)};
      const modeTerms = {json.dumps(MODE_SELECTOR_TERMS, ensure_ascii=False)};
      const loginTerms = {json.dumps(LOGIN_TERMS, ensure_ascii=False)};
      const shareTerms = {json.dumps(SHARE_IMAGE_TERMS, ensure_ascii=False)};
      const copyTerms = {json.dumps(COPY_IMAGE_TERMS, ensure_ascii=False)};
      const downloadTerms = {json.dumps(DOWNLOAD_IMAGE_TERMS, ensure_ascii=False)};
      const stopTerms = {json.dumps(STOP_TERMS, ensure_ascii=False)};
      const generatingTerms = {json.dumps(GENERATING_TERMS, ensure_ascii=False)};
      const drawTerms = ["制作图片", "Create image"];
      const deselectTerms = ["取消选择", "Deselect", "Remove", "Unselect"];
      const normalize = (value) => String(value || "")
        .replace(/\\s+/g, " ")
        .replace(/[“”]/g, '"')
        .trim();
      const containsAny = (value, candidates) => {{
        const normalized = normalize(value);
        const lowered = normalized.toLowerCase();
        return candidates.some((candidate) => normalized.includes(candidate) || lowered.includes(candidate.toLowerCase()));
      }};
      const isVisible = (element) => {{
        if (!element || !element.isConnected) {{
          return false;
        }}
        const style = window.getComputedStyle(element);
        if (style.visibility === "hidden" || style.display === "none") {{
          return false;
        }}
        return element.getClientRects().length > 0;
      }};
      const queryAllDeep = (selectors) => {{
        const results = [];
        const seen = new Set();
        const visit = (root) => {{
          for (const element of root.querySelectorAll(selectors)) {{
            if (seen.has(element)) {{
              continue;
            }}
            seen.add(element);
            results.push(element);
          }}
          for (const host of root.querySelectorAll("*")) {{
            if (host.shadowRoot) {{
              visit(host.shadowRoot);
            }}
          }}
        }};
        visit(document);
        return results;
      }};
      const describe = (element) => {{
        const label = normalize(
          element.getAttribute("aria-label")
          || element.getAttribute("placeholder")
          || element.innerText
          || element.textContent
        );
        return {{
          label,
          ariaPressed: element.getAttribute("aria-pressed"),
          ariaSelected: element.getAttribute("aria-selected"),
          ariaCurrent: element.getAttribute("aria-current"),
          disabled: element.disabled === true || element.getAttribute("aria-disabled") === "true",
          area: element.getBoundingClientRect().width * element.getBoundingClientRect().height,
        }};
      }};
      const buttons = queryAllDeep("button,[role='button'],a,[role='link']")
        .filter(isVisible)
        .map(describe)
        .filter((item) => item.label);
      const bodyText = normalize(document.body ? document.body.innerText : "");
      const promptCandidates = queryAllDeep("textarea,[contenteditable='true'],[role='textbox'],input[type='text']")
        .filter(isVisible)
        .filter((element) => !element.disabled && !element.readOnly)
        .map(describe);
      const promptMatches = promptCandidates.filter((item) => containsAny(item.label, promptTerms));
      const largestImage = Array.from(document.images)
        .filter((image) => image && image.src && image.naturalWidth >= 128 && image.naturalHeight >= 128)
        .map((image) => ({{
          src: image.src,
          alt: normalize(image.alt || ""),
          width: image.naturalWidth,
          height: image.naturalHeight,
          area: image.naturalWidth * image.naturalHeight,
        }}))
        .sort((left, right) => right.area - left.area)[0] || null;
      const hasPromptInput = promptMatches.length > 0 || promptCandidates.length > 0;
      const hasModeSelector = buttons.some((item) => containsAny(item.label, modeTerms));
      const hasActivePro = buttons.some((item) => {{
        const label = item.label.toUpperCase();
        return (label === "PRO" || label === "PRO MODE")
          && (item.disabled || item.ariaCurrent === "true" || item.ariaPressed === "true");
      }});
      const hasDrawSelected = buttons.some((item) => containsAny(item.label, drawTerms) && (
        containsAny(item.label, deselectTerms)
        || item.ariaPressed === "true"
        || item.ariaSelected === "true"
      ));
      const hasShareImageButton = buttons.some((item) => containsAny(item.label, shareTerms));
      const hasCopyImageButton = buttons.some((item) => containsAny(item.label, copyTerms));
      const hasDownloadImageButton = buttons.some((item) => containsAny(item.label, downloadTerms));
      const hasStopButton = buttons.some((item) => containsAny(item.label, stopTerms));
      const requiresLogin = !hasPromptInput && (
        buttons.some((item) => containsAny(item.label, loginTerms))
        || containsAny(bodyText, loginTerms)
      );
      const isGenerating = hasStopButton || containsAny(bodyText, generatingTerms);
      const hasErrorText = containsAny(bodyText, [
        "出了点问题",
        "Something went wrong",
        "An error occurred",
        "Try again",
      ]);
      return {{
        url: location.href,
        title: document.title,
        ready_state: document.readyState,
        body_excerpt: bodyText.slice(0, 1000),
        visible_buttons: buttons.slice(0, 40).map((item) => item.label),
        has_prompt_input: hasPromptInput,
        has_mode_selector: hasModeSelector,
        has_active_pro: hasActivePro,
        has_draw_selected: hasDrawSelected,
        has_share_image_button: hasShareImageButton,
        has_copy_image_button: hasCopyImageButton,
        has_download_image_button: hasDownloadImageButton,
        has_stop_button: hasStopButton,
        requires_login: requiresLogin,
        is_generating: isGenerating,
        has_error_text: hasErrorText,
        is_blank: location.href === "about:blank" || (!bodyText && buttons.length === 0 && !largestImage),
        largest_image: largestImage,
        largest_image_area: largestImage ? largestImage.area : 0,
      }};
    }})()
    """
    return _evaluate(page["webSocketDebuggerUrl"], expression, cdp_port)


def start_browser(args: argparse.Namespace) -> BrowserSession:
    result = run_command(
        [
            os_fspath(START_BROWSER_SCRIPT),
            "--browser",
            args.browser,
            "--desktop-user",
            args.desktop_user,
            "--profile-directory",
            args.profile_directory,
            "--session",
            args.session,
            "--download-dir",
            os_fspath(args.artifacts_dir),
        ],
        timeout=120,
    )
    payload = json.loads(result.stdout)
    return BrowserSession(
        browser=payload["browser"],
        desktop_user=payload["desktop_user"],
        port=int(payload["port"]),
        pid=int(payload["pid"]),
        session=payload["session"],
        temp_profile_root=payload["temp_profile_root"],
        download_dir=payload["download_dir"],
        log_path=payload["log_path"],
    )


def stop_browser(session: BrowserSession | None) -> None:
    if session is None:
        return
    agent_browser(session.session, "close", check=False, timeout=20)
    run_command(
        [
            os_fspath(STOP_BROWSER_SCRIPT),
            "--pid",
            str(session.pid),
            "--temp-profile-root",
            session.temp_profile_root,
        ],
        timeout=60,
        check=False,
    )


def connect_and_open(session: BrowserSession, page_url: str) -> None:
    agent_browser(session.session, "close", check=False, timeout=20)
    agent_browser(session.session, "connect", str(session.port), timeout=30)
    agent_browser(session.session, "open", page_url, timeout=60)
    agent_browser(session.session, "wait", "--load", "networkidle", timeout=45, check=False)


def wait_for_home_ready(
    session: BrowserSession,
    page_url: str,
    recorder: ArtifactRecorder,
    *,
    timeout_seconds: int,
    open_attempts: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_state: dict[str, Any] | None = None

    for attempt in range(1, open_attempts + 1):
        connect_and_open(session, page_url)
        recorder.capture(session.session, f"open-attempt-{attempt}")

        while time.monotonic() < deadline:
            state = inspect_gemini_state(session.port, "gemini.google.com/app")
            last_state = state
            recorder.append_jsonl(
                "state-poll.jsonl",
                {
                    "stage": "home",
                    "attempt": attempt,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "state": state,
                },
            )
            if state["requires_login"]:
                raise RunnerError("Gemini opened but the cloned browser profile is not logged in.")
            if state["has_prompt_input"] and state["has_mode_selector"] and not state["is_blank"]:
                recorder.write_json("home-ready-state.json", state)
                return state
            time.sleep(1.5)

    raise RunnerError(f"Gemini home page never became ready. Last state: {json.dumps(last_state, ensure_ascii=False)}")


def run_prepare_with_retry(
    session: BrowserSession,
    prompt: str,
    page_url: str,
    recorder: ArtifactRecorder,
    *,
    max_attempts: int,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = run_command(
                [
                    "python3",
                    os_fspath(PREPARE_SCRIPT),
                    "--cdp-port",
                    str(session.port),
                    "--prompt",
                    prompt,
                    "--submit",
                ],
                timeout=90,
            )
            payload = json.loads(result.stdout)
            recorder.write_json(f"prepare-attempt-{attempt}.json", payload)
            recorder.capture(session.session, f"after-submit-attempt-{attempt}")
            return payload
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            recorder.write_text(f"prepare-attempt-{attempt}.err.txt", str(exc) + "\n")
            recorder.capture(session.session, f"prepare-failed-{attempt}")
            connect_and_open(session, page_url)
            wait_for_home_ready(
                session,
                page_url,
                recorder,
                timeout_seconds=30,
                open_attempts=1,
            )

    raise RunnerError(f"Could not prepare and submit the Gemini prompt. Last error: {last_error}")


def wait_for_result_ready(
    session: BrowserSession,
    recorder: ArtifactRecorder,
    *,
    timeout_seconds: int,
    poll_interval_seconds: float,
    min_image_area: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_state: dict[str, Any] | None = None

    while time.monotonic() < deadline:
        state = inspect_gemini_state(session.port, "gemini.google.com/app")
        last_state = state
        recorder.append_jsonl(
            "state-poll.jsonl",
            {
                "stage": "result",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "state": state,
            },
        )

        if state["requires_login"]:
            raise RunnerError("Gemini session fell back to login before the image was ready.")

        if state["has_error_text"] and not state["is_generating"]:
            raise RunnerError(f"Gemini reported an error: {state['body_excerpt']}")

        has_ready_controls = (
            state["has_download_image_button"]
            or state["has_share_image_button"]
            or state["has_copy_image_button"]
        )
        if not state["is_generating"] and (has_ready_controls or state["largest_image_area"] >= min_image_area):
            recorder.write_json("result-ready-state.json", state)
            recorder.capture(session.session, "result-ready")
            return state

        time.sleep(poll_interval_seconds)

    raise RunnerError(f"Timed out waiting for Gemini image result. Last state: {json.dumps(last_state, ensure_ascii=False)}")


def find_download_ref(snapshot_text: str) -> str | None:
    for line in snapshot_text.splitlines():
        if not any(term in line for term in DOWNLOAD_IMAGE_TERMS):
            continue
        ref_start = line.find("ref=")
        if ref_start == -1:
            continue
        ref_end = line.find("]", ref_start)
        if ref_end == -1:
            continue
        ref_value = line[ref_start + 4 : ref_end]
        if ref_value:
            return f"@{ref_value}"
    return None


def try_browser_download(
    session: BrowserSession,
    output_path: Path,
    recorder: ArtifactRecorder,
) -> tuple[bool, str]:
    snapshot = agent_browser(session.session, "snapshot", "-i", "-c", timeout=45, check=False)
    recorder.write_text("browser-download-snapshot.txt", snapshot.stdout or "")
    ref = find_download_ref(snapshot.stdout or "")
    if ref is None:
        return False, "Could not find a download button ref in the current Gemini snapshot."

    attempt = agent_browser(
        session.session,
        "download",
        ref,
        os_fspath(output_path),
        timeout=20,
        check=False,
    )
    recorder.write_text("browser-download.log", (attempt.stdout or "") + (attempt.stderr or ""))
    if attempt.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
        return True, "Browser download succeeded."
    return False, "Browser download did not produce a file in time."


def export_image_with_retry(
    session: BrowserSession,
    output_path: Path,
    recorder: ArtifactRecorder,
    *,
    max_attempts: int,
    min_image_area: int,
) -> dict[str, Any]:
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        try:
            result = run_command(
                [
                    "python3",
                    os_fspath(EXPORT_SCRIPT),
                    "--cdp-port",
                    str(session.port),
                    "--output",
                    os_fspath(output_path),
                ],
                timeout=60,
            )
            payload = json.loads(result.stdout)
            area = int(payload.get("width", 0)) * int(payload.get("height", 0))
            recorder.write_json(f"export-attempt-{attempt}.json", payload)
            if area >= min_image_area:
                return payload
            last_error = f"Exported image area {area} was smaller than required minimum {min_image_area}."
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            recorder.write_text(f"export-attempt-{attempt}.err.txt", last_error + "\n")
        time.sleep(2)

    raise RunnerError(f"Could not export the Gemini image. Last error: {last_error}")


def write_manifest(
    recorder: ArtifactRecorder,
    *,
    session: BrowserSession,
    output_path: str | None,
    status: str,
    prepare_result: dict[str, Any] | None,
    final_state: dict[str, Any] | None,
    download_method: str,
    browser_download_message: str | None,
    error: str | None = None,
) -> Path:
    manifest = {
        "status": status,
        "browser": session.browser,
        "desktop_user": session.desktop_user,
        "session": session.session,
        "cdp_port": session.port,
        "output_path": output_path,
        "prepare_result": prepare_result,
        "final_state": final_state,
        "download_method": download_method,
        "browser_download_message": browser_download_message,
        "artifacts_dir": os_fspath(recorder.directory),
        "error": error,
    }
    return recorder.write_json("result-manifest.json", manifest)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--browser", default="brave", choices=["brave", "chrome", "chromium"])
    parser.add_argument("--desktop-user", default="user")
    parser.add_argument("--profile-directory", default="Default")
    parser.add_argument("--session", default="gemini-image-run")
    parser.add_argument("--page-url", default=DEFAULT_PAGE_URL)
    parser.add_argument("--artifacts-dir")
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--poll-interval-seconds", type=float, default=3.0)
    parser.add_argument("--open-attempts", type=int, default=3)
    parser.add_argument("--prepare-attempts", type=int, default=3)
    parser.add_argument("--export-attempts", type=int, default=3)
    parser.add_argument("--min-image-area", type=int, default=200000)
    parser.add_argument(
        "--download-strategy",
        choices=["export", "auto", "browser"],
        default="export",
    )
    parser.add_argument("--keep-browser", action="store_true")
    args = parser.parse_args()

    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output = output_path

    if args.artifacts_dir:
        artifacts_dir = Path(args.artifacts_dir).expanduser().resolve()
    else:
        artifacts_dir = output_path.parent / f"{output_path.stem}-artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    args.artifacts_dir = artifacts_dir
    return args


def main() -> int:
    args = parse_args()
    recorder = ArtifactRecorder(args.artifacts_dir)
    recorder.write_json(
        "run-config.json",
        {
            "prompt": args.prompt,
            "output": os_fspath(args.output),
            "browser": args.browser,
            "desktop_user": args.desktop_user,
            "profile_directory": args.profile_directory,
            "session": args.session,
            "page_url": args.page_url,
            "artifacts_dir": os_fspath(args.artifacts_dir),
            "timeout_seconds": args.timeout_seconds,
            "poll_interval_seconds": args.poll_interval_seconds,
            "download_strategy": args.download_strategy,
        },
    )

    session: BrowserSession | None = None
    prepare_result: dict[str, Any] | None = None
    final_state: dict[str, Any] | None = None
    browser_download_message: str | None = None
    output_path: str | None = None
    download_method = "export"

    try:
        session = start_browser(args)
        recorder.write_json("browser-session.json", session.__dict__)

        wait_for_home_ready(
            session,
            args.page_url,
            recorder,
            timeout_seconds=min(args.timeout_seconds, 60),
            open_attempts=args.open_attempts,
        )
        prepare_result = run_prepare_with_retry(
            session,
            args.prompt,
            args.page_url,
            recorder,
            max_attempts=args.prepare_attempts,
        )
        final_state = wait_for_result_ready(
            session,
            recorder,
            timeout_seconds=args.timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            min_image_area=args.min_image_area,
        )

        if args.download_strategy in {"browser", "auto"}:
            ok, browser_download_message = try_browser_download(session, args.output, recorder)
            if ok:
                output_path = os_fspath(args.output)
                download_method = "browser"
            elif args.download_strategy == "browser":
                raise RunnerError(browser_download_message)

        if output_path is None:
            export_result = export_image_with_retry(
                session,
                args.output,
                recorder,
                max_attempts=args.export_attempts,
                min_image_area=args.min_image_area,
            )
            output_path = export_result["output_path"]
            recorder.write_json("export-result.json", export_result)
            download_method = "export"

        manifest_path = write_manifest(
            recorder,
            session=session,
            output_path=output_path,
            status="ok",
            prepare_result=prepare_result,
            final_state=final_state,
            download_method=download_method,
            browser_download_message=browser_download_message,
        )
        print(
            json.dumps(
                {
                    "status": "ok",
                    "output_path": output_path,
                    "download_method": download_method,
                    "manifest_path": os_fspath(manifest_path),
                    "artifacts_dir": os_fspath(args.artifacts_dir),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        if session is not None:
            recorder.capture(session.session, "failure")
            final_state = inspect_gemini_state(session.port, "gemini.google.com/app")
        write_manifest(
            recorder,
            session=session or BrowserSession(
                browser=args.browser,
                desktop_user=args.desktop_user,
                port=-1,
                pid=-1,
                session=args.session,
                temp_profile_root="",
                download_dir=os_fspath(args.artifacts_dir),
                log_path="",
            ),
            output_path=output_path,
            status="error",
            prepare_result=prepare_result,
            final_state=final_state,
            download_method=download_method,
            browser_download_message=browser_download_message,
            error=str(exc),
        )
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if session is not None and not args.keep_browser:
            stop_browser(session)


if __name__ == "__main__":
    raise SystemExit(main())
