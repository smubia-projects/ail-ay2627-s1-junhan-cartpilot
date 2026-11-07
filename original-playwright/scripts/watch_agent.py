"""Watch the browser agents work on the mock stores (before the web UI exists).

    .venv/bin/python scripts/watch_agent.py                 # real browser windows, default items
    .venv/bin/python scripts/watch_agent.py --headless      # no windows, just the log
    .venv/bin/python scripts/watch_agent.py egg butter milk # your own items

It starts its own copy of the web server, so you don't need uvicorn running.
"""
import argparse
import asyncio
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from app.agent.agent import Run  # noqa: E402
from app.agent.stores.mock_store import MockStoreAdapter  # noqa: E402
from app.main import app  # noqa: E402
from mock_stores.products import STORES  # noqa: E402

DEFAULT = ["plain flour", "caster sugar", "cocoa powder", "egg", "butter", "milk", "baking powder"]


def start_server() -> str:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}"


async def main(items, headless, parallel):
    base = start_server()
    adapters = [MockStoreAdapter(slug, base) for slug in STORES]
    for a in adapters:
        a.delay_range = (0.8, 1.6)  # slowed down so you can follow along

    def show(e):
        if e["type"] == "log":
            flag = "  ⛔ BLOCKED" if e["status"] == "blocked" else ""
            print(f"  [w{e['worker']}] {e['store'] or '':11} {e['action']:6} {e['detail'][:80]}{flag}")
        elif e["type"] == "result":
            r = e["result"]
            pick = r["chosen"]["name"] + " " + r["chosen"]["size"] if r["chosen"] else r["message"]
            print(f"✔ {r['store']:11} {r['ingredient']:14} {r['status']:9} {pick}")

    run = Run(items, adapters, approved=True, emit=show, max_parallel=parallel, headless=headless)
    print(f"Searching {len(items)} items in {len(adapters)} stores with {parallel} browser windows…\n")
    run.start()
    try:
        await run.wait()
    except KeyboardInterrupt:
        run.stop()
    print(f"\nRun {run.state}. Results: {len(run.results)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("items", nargs="*", default=DEFAULT)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--parallel", type=int, default=3)
    a = ap.parse_args()
    asyncio.run(main(a.items, a.headless, a.parallel))
