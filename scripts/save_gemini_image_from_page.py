#!/usr/bin/env python3
"""Export the generated Gemini image from the active page via CDP."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
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


def _write_image(output_path: Path, data_url: str) -> Path:
    header, encoded = data_url.split(",", 1)
    mime = header.split(";", 1)[0].split(":", 1)[1]
    ext = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }.get(mime, "")

    final_path = output_path
    if not final_path.suffix and ext:
        final_path = final_path.with_suffix(ext)

    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(base64.b64decode(encoded))
    return final_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-port", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--url-contains", default="gemini.google.com/app")
    args = parser.parse_args()

    page = _pick_page(args.cdp_port, args.url_contains)
    expression = r"""
    (async () => {
      const candidates = Array.from(document.images)
        .filter((img) => img && img.src && img.naturalWidth >= 256 && img.naturalHeight >= 256)
        .sort((a, b) => (b.naturalWidth * b.naturalHeight) - (a.naturalWidth * a.naturalHeight));
      if (!candidates.length) {
        throw new Error("No sufficiently large image found on the page.");
      }
      const img = candidates[0];
      if (typeof img.decode === "function") {
        try {
          await img.decode();
        } catch (err) {
        }
      }
      const blobToDataUrl = (blob) => new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error || new Error("Could not read image blob."));
        reader.readAsDataURL(blob);
      });

      try {
        const response = await fetch(img.currentSrc || img.src, {credentials: "include"});
        if (!response.ok) {
          throw new Error(`Fetch failed with status ${response.status}`);
        }
        const blob = await response.blob();
        const dataUrl = await blobToDataUrl(blob);
        return {
          alt: img.alt || "",
          width: img.naturalWidth,
          height: img.naturalHeight,
          src: img.src,
          mime: blob.type || "image/png",
          strategy: "fetch",
          dataUrl,
        };
      } catch (fetchError) {
        const canvas = document.createElement("canvas");
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        const ctx = canvas.getContext("2d");
        if (!ctx) {
          throw new Error("Could not get 2D canvas context.");
        }
        ctx.drawImage(img, 0, 0);
        const dataUrl = canvas.toDataURL("image/png");
        return {
          alt: img.alt || "",
          width: img.naturalWidth,
          height: img.naturalHeight,
          src: img.src,
          mime: "image/png",
          strategy: "canvas",
          fetchError: String(fetchError),
          dataUrl,
        };
      }
    })()
    """
    image_info = _evaluate(page["webSocketDebuggerUrl"], expression, args.cdp_port)
    output_path = _write_image(Path(args.output), image_info["dataUrl"])

    print(
        json.dumps(
            {
                "output_path": os.fspath(output_path),
                "width": image_info["width"],
                "height": image_info["height"],
                "alt": image_info["alt"],
                "source_url": image_info["src"],
                "mime": image_info.get("mime", ""),
                "strategy": image_info.get("strategy", ""),
                "page_url": page["url"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
