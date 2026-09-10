"""One-shot WebSocket smoke test: send a chat turn and print the frames.

Used to verify that a finished turn really writes its token-usage snapshot
(``messages.token_usage`` / ``model_id`` / ``provider_type``), which is what the
Token 用量 page reads. Not part of the app.

    python scripts/ws_smoke.py "只回复两个字:你好"
"""

from __future__ import annotations

import asyncio
import json
import sys

import websockets

URL = "ws://127.0.0.1:8765/ws"


async def main(prompt: str) -> int:
    async with websockets.connect(URL, max_size=None) as ws:
        await ws.send(json.dumps({"type": "chat", "text": prompt}))
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=120)
            except asyncio.TimeoutError:
                print("!! timed out waiting for a frame")
                return 1
            frame = json.loads(raw)
            kind = frame.get("type")
            if kind == "delta":
                continue
            print(kind, json.dumps(frame, ensure_ascii=False)[:220])
            if kind in ("done", "error"):
                return 0 if kind == "done" else 1


if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else "只回复两个字:你好"
    raise SystemExit(asyncio.run(main(text)))
