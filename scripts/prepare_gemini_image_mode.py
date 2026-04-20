#!/usr/bin/env python3
"""Quickly ensure Gemini is ready for image generation via Pro + 制作图片."""

from __future__ import annotations

import argparse
import json
import urllib.request

import websocket


def _read_json(url: str) -> list[dict]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _pick_page(cdp_port: int, url_contains: str) -> dict:
    pages = _read_json(f"http://127.0.0.1:{cdp_port}/json/list")
    for page in pages:
        if page.get("type") != "page":
            continue
        if url_contains in page.get("url", ""):
            return page
    raise RuntimeError(f"No page target found containing URL fragment: {url_contains}")


def _evaluate(ws_url: str, expression: str, cdp_port: int) -> dict:
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
                raise RuntimeError(f"CDP error: {message['error']}")
            result = message.get("result", {})
            if "exceptionDetails" in result:
                raise RuntimeError(json.dumps(result["exceptionDetails"], ensure_ascii=False))
            return result["result"]["value"]
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-port", type=int, required=True)
    parser.add_argument("--url-contains", default="gemini.google.com/app")
    parser.add_argument("--prompt")
    parser.add_argument("--submit", action="store_true")
    args = parser.parse_args()
    if args.submit and args.prompt is None:
        parser.error("--submit requires --prompt")

    page = _pick_page(args.cdp_port, args.url_contains)
    prompt_json = json.dumps(args.prompt, ensure_ascii=False)
    submit_json = "true" if args.submit else "false"
    expression = r"""
    (async () => {
      const startedAt = performance.now();
      const desiredPrompt = PROMPT_JSON_PLACEHOLDER;
      const shouldSubmit = SUBMIT_PLACEHOLDER;
      const drawTerms = ["制作图片", "Create image"];
      const deselectTerms = ["取消选择", "Deselect", "Remove", "Unselect"];
      const promptTerms = [
        "为 Gemini 输入提示",
        "Gemini 输入提示",
        "Ask Gemini",
        "Enter a prompt",
        "Message Gemini",
      ];
      const sendTerms = ["发送", "Send"];
      const stopTerms = ["停止回答", "Stop response", "Stop responding", "Stop generating"];
      const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const normalize = (value) => String(value || "")
        .replace(/\s+/g, " ")
        .replace(/[“”]/g, '"')
        .trim();
      const containsAny = (value, candidates) => candidates.some((candidate) => value.includes(candidate));
      const isVisible = (element) => {
        if (!element || !element.isConnected) {
          return false;
        }
        const style = window.getComputedStyle(element);
        if (style.visibility === "hidden" || style.display === "none") {
          return false;
        }
        return element.getClientRects().length > 0;
      };
      const queryAllDeep = (selectors) => {
        const results = [];
        const seen = new Set();
        const visit = (root) => {
          for (const element of root.querySelectorAll(selectors)) {
            if (seen.has(element)) {
              continue;
            }
            seen.add(element);
            results.push(element);
          }
          for (const host of root.querySelectorAll("*")) {
            if (host.shadowRoot) {
              visit(host.shadowRoot);
            }
          }
        };
        visit(document);
        return results;
      };
      const describe = (element) => ({
        node: element,
        label: normalize(
          element.getAttribute("aria-label")
          || element.getAttribute("placeholder")
          || element.innerText
          || element.textContent
        ),
        text: normalize(element.innerText || element.textContent),
        ariaLabel: normalize(element.getAttribute("aria-label")),
        placeholder: normalize(element.getAttribute("placeholder")),
        area: element.getBoundingClientRect().width * element.getBoundingClientRect().height,
      });
      const findFirst = (selectors, predicate) => {
        for (const element of queryAllDeep(selectors)) {
          if (!isVisible(element)) {
            continue;
          }
          const item = describe(element);
          if (predicate(item)) {
            return item;
          }
        }
        return null;
      };
      const clickElement = async (element) => {
        element.scrollIntoView({block: "center", inline: "center"});
        await sleep(50);
        const pointer = {bubbles: true, cancelable: true, composed: true, button: 0, buttons: 1, pointerId: 1, pointerType: "mouse"};
        const mouse = {bubbles: true, cancelable: true, composed: true, button: 0, buttons: 1};
        if (typeof PointerEvent === "function") {
          element.dispatchEvent(new PointerEvent("pointerdown", pointer));
          element.dispatchEvent(new PointerEvent("pointerup", pointer));
        }
        element.dispatchEvent(new MouseEvent("mousedown", mouse));
        element.dispatchEvent(new MouseEvent("mouseup", mouse));
        element.click();
        await sleep(150);
      };
      const findActivePro = () => findFirst("button,[role='button'],[role='tab']", (item) => {
        const label = item.label.toUpperCase();
        if (label !== "PRO" && label !== "PRO MODE") {
          return false;
        }
        const node = item.node;
        return (
          node.disabled
          || node.getAttribute("aria-disabled") === "true"
          || node.getAttribute("aria-current") === "true"
          || node.getAttribute("aria-pressed") === "true"
        );
      });
      const findModeSelector = () => findFirst("button,[role='button']", (item) => {
        const label = item.label;
        return (
          label.includes("打开模式选择器")
          || label.toLowerCase().includes("mode selector")
          || label.toLowerCase().includes("mode picker")
          || label.toLowerCase().includes("models and tools")
        );
      });
      const findProMenuItem = () => findFirst("button,[role='menuitemradio'],[role='menuitem'],[role='option']", (item) => {
        const label = item.label;
        return (
          label === "Pro"
          || label === "PRO"
          || label.startsWith("Pro ")
          || label.startsWith("PRO ")
        );
      });
      const isDrawLabel = (label) => containsAny(label, drawTerms);
      const isDeselectLabel = (label) => containsAny(label, deselectTerms);
      const isSelectedToggle = (node) => (
        node.getAttribute("aria-pressed") === "true"
        || node.getAttribute("aria-selected") === "true"
      );
      const findSelectedDraw = () => findFirst("button,[role='button']", (item) => (
        isDrawLabel(item.label) && (isDeselectLabel(item.label) || isSelectedToggle(item.node))
      ));
      const findDrawButton = () => findFirst("button,[role='button']", (item) => (
        isDrawLabel(item.label) && !isDeselectLabel(item.label) && !isSelectedToggle(item.node)
      ));
      const findPromptInput = () => {
        const candidates = [];
        for (const element of queryAllDeep("textarea,[contenteditable='true'],[role='textbox'],input[type='text']")) {
          if (!isVisible(element)) {
            continue;
          }
          if (element.disabled || element.readOnly) {
            continue;
          }
          candidates.push(describe(element));
        }
        const matching = candidates
          .filter((item) => {
            const combined = normalize([item.label, item.text, item.ariaLabel, item.placeholder].join(" "));
            return containsAny(combined, promptTerms);
          })
          .sort((left, right) => right.area - left.area);
        if (matching.length) {
          return matching[0];
        }
        return candidates.sort((left, right) => right.area - left.area)[0] || null;
      };
      const readPromptValue = (element) => {
        if ("value" in element && typeof element.value === "string") {
          return normalize(element.value);
        }
        return normalize(element.innerText || element.textContent);
      };
      const setPromptValue = async (element, value) => {
        element.scrollIntoView({block: "center", inline: "center"});
        element.focus();
        await sleep(50);
        if ("value" in element && typeof element.value === "string") {
          const prototype = Object.getPrototypeOf(element);
          const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
          if (descriptor && descriptor.set) {
            descriptor.set.call(element, value);
          } else {
            element.value = value;
          }
        } else {
          element.textContent = value;
        }
        element.dispatchEvent(new InputEvent("input", {
          bubbles: true,
          cancelable: true,
          data: value,
          inputType: "insertText",
        }));
        element.dispatchEvent(new Event("change", {bubbles: true}));
        await sleep(120);
      };
      const findSendButton = () => findFirst("button,[role='button']", (item) => (
        containsAny(item.label, sendTerms)
      ));
      const hasGenerationStarted = () => (
        Boolean(findFirst("button,[role='button']", (item) => (
          containsAny(item.label, stopTerms)
        )))
        || normalize(document.body.innerText).includes("Nano Banana")
        || normalize(document.body.innerText).includes("Creating your image")
      );
      const pressEscape = async () => {
        for (const type of ["keydown", "keyup"]) {
          document.dispatchEvent(new KeyboardEvent(type, {
            key: "Escape",
            code: "Escape",
            keyCode: 27,
            which: 27,
            bubbles: true,
          }));
        }
        await sleep(120);
      };
      const waitFor = async (predicate, timeoutMs, errorMessage) => {
        const deadline = Date.now() + timeoutMs;
        while (Date.now() < deadline) {
          const value = predicate();
          if (value) {
            return value;
          }
          await sleep(120);
        }
        throw new Error(errorMessage);
      };

      let modeAction = "already-pro";
      let drawAction = "already-selected";
      let promptAction = "skipped";
      let submitAction = "skipped";

      if (!findActivePro()) {
        const selector = findModeSelector();
        if (!selector) {
          throw new Error("Could not find the Gemini mode selector.");
        }
        await clickElement(selector.node);
        const proItem = await waitFor(
          () => findProMenuItem(),
          5000,
          "Could not find the Pro mode entry in the Gemini selector."
        );
        if (proItem.node.getAttribute("aria-current") !== "true") {
          await clickElement(proItem.node);
          modeAction = "selected-pro";
        } else {
          modeAction = "menu-already-pro";
        }
        await waitFor(
          () => findActivePro() || !findProMenuItem(),
          5000,
          "Gemini did not switch to Pro mode."
        );
        if (findProMenuItem()) {
          await pressEscape();
        }
        await waitFor(
          () => findActivePro(),
          5000,
          "Gemini did not show Pro as the active mode."
        );
      }

      if (!findSelectedDraw()) {
        const drawButton = findDrawButton();
        if (!drawButton) {
          throw new Error("Could not find the 制作图片 control.");
        }
        await clickElement(drawButton.node);
        await waitFor(
          () => findSelectedDraw(),
          5000,
          "Gemini did not confirm 制作图片 as selected."
        );
        drawAction = "selected-draw";
      }

      if (desiredPrompt !== null) {
        const promptInput = await waitFor(
          () => findPromptInput(),
          5000,
          "Could not find the Gemini prompt input."
        );
        await setPromptValue(promptInput.node, desiredPrompt);
        await waitFor(
          () => {
            const current = findPromptInput();
            return current && readPromptValue(current.node) === normalize(desiredPrompt);
          },
          5000,
          "Gemini did not accept the prompt text."
        );
        promptAction = "filled-prompt";
      }

      if (shouldSubmit) {
        const sendButton = await waitFor(
          () => {
            const candidate = findSendButton();
            if (!candidate) {
              return null;
            }
            const node = candidate.node;
            if (node.disabled || node.getAttribute("aria-disabled") === "true") {
              return null;
            }
            return candidate;
          },
          5000,
          "Could not find an enabled Gemini send button."
        );
        await clickElement(sendButton.node);
        await waitFor(
          () => hasGenerationStarted(),
          5000,
          "Gemini did not enter image generation after submission."
        );
        submitAction = "submitted";
      }

      const selectedDraw = findSelectedDraw();
      const elapsedMs = Math.round(performance.now() - startedAt);
      return {
        ok: true,
        mode_action: modeAction,
        draw_action: drawAction,
        prompt_action: promptAction,
        submit_action: submitAction,
        active_mode: findActivePro() ? "Pro" : "unknown",
        draw_selected: Boolean(selectedDraw),
        draw_label: selectedDraw ? selectedDraw.label : "",
        prompt_length: desiredPrompt === null ? 0 : desiredPrompt.length,
        generation_started: shouldSubmit ? hasGenerationStarted() : false,
        elapsed_ms: elapsedMs,
      };
    })()
    """
    expression = expression.replace("PROMPT_JSON_PLACEHOLDER", prompt_json)
    expression = expression.replace("SUBMIT_PLACEHOLDER", submit_json)
    result = _evaluate(page["webSocketDebuggerUrl"], expression, args.cdp_port)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
