from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8201")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.image.is_file():
        raise SystemExit(f"Image is missing: {args.image}")
    started = time.monotonic()
    with args.image.open("rb") as stream:
        response = httpx.post(
            f"{args.base_url.rstrip('/')}/v1/ocr/recognitions",
            data={
                "model": "PP-OCRv5_server",
                "frame_id": "acceptance-frame",
                "media_timestamp": "0",
                "language": "ch",
            },
            files={"file": (args.image.name, stream, "image/jpeg")},
            timeout=600,
            trust_env=False,
        )
    if response.is_error:
        raise SystemExit(
            f"OCR Gateway returned HTTP {response.status_code}: {response.text[:2000]}"
        )
    payload = response.json()
    if not payload.get("blocks") or not payload.get("text"):
        raise SystemExit("OCR Gateway returned no text blocks")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "block_count": len(payload["blocks"]),
                "elapsed_seconds": time.monotonic() - started,
                "provider_elapsed_seconds": payload.get("elapsed_seconds"),
                "output": str(args.output) if args.output else None,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
