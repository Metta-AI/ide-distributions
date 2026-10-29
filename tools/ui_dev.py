"""A model-free stack for looking at the client page: ``uv run python tools/ui_dev.py``.

Starts ``research serve``'s :class:`SeamServer` in a temporary workspace against the fake
platform and a :class:`FakeHermesRuns` whose runs are played by :class:`ScriptedAgent`
(``research_core.scripted_agent``), and ticks the watcher every few seconds on a thread, as the
cron job would. Nothing calls a model and nothing touches the network, so the page at the
printed URL can be opened, poked and screenshotted at will. Ctrl-C stops everything; the
temporary workspace is removed on exit.

    uv run python tools/ui_dev.py [--port 8650] [--tick-seconds 5]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# The fake Coworld's source repository is test data (``core/tests/fixtures``), reused here for
# development because it is the only Coworld the fake platform knows.
sys.path.insert(0, str(REPO / "core"))

from tests.fixtures.fakegame_repo import COWORLD_ID, make_fakegame_repo  # noqa: E402

from research_core import layout  # noqa: E402 - after the sys.path change
from research_core.scripted_agent import REQUEST, TRIGGERS, ScriptedAgent  # noqa: E402
from research_core.serve import DEFAULT_PORT, SeamServer  # noqa: E402
from research_core.watcher import context as watcher_context  # noqa: E402
from research_core.watcher import tick as watcher_tick  # noqa: E402
from research_core.watcher.hermes_api import FakeHermesRuns  # noqa: E402

DEFAULT_TICK_S = 5.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"default {DEFAULT_PORT}")
    parser.add_argument(
        "--tick-seconds", type=float, default=DEFAULT_TICK_S, help="seconds between watcher ticks"
    )
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="ui-dev-") as root_text:
        root = Path(root_text)
        env = {
            "IDE_WORKSPACE": str(root / "ws"),
            "IDE_PLATFORM": "fake",
            "IDE_RESEARCHER_PROFILES": "researcher",
            "SOFTMAX_PLAYER_ID": "ply_dev",
        }
        ws = layout.from_env(env)
        ws.ensure_dirs()
        source, commit = make_fakegame_repo(root / "upstream")
        agent = ScriptedAgent(env, source, commit, coworld_id=COWORLD_ID)
        hermes = FakeHermesRuns(script=agent)
        agent.hermes = hermes
        server = SeamServer(ws=ws, hermes=hermes, port=args.port)
        threading.Thread(target=server.serve_forever, daemon=True, name="seam").start()
        stop = threading.Event()
        ticker = threading.Thread(
            target=tick_forever, args=(env, hermes, args.tick_seconds, stop), daemon=True
        )
        ticker.start()

        print(f"client page:  {server.url}/")
        print(f"workspace:    {ws.root}  (removed on exit)")
        print(f"watcher tick: every {args.tick_seconds:g} s on a thread; Ctrl-C stops")
        print("try these in the page:")
        print(f"  {REQUEST!r:46} switch to the fake Coworld, upload and submit the starter")
        for word, effect in TRIGGERS.items():
            print(f"  a message containing {word!r:14} {effect}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            server.shutdown()
            ticker.join(timeout=args.tick_seconds + 5)
    return 0


def tick_forever(env: dict[str, str], hermes: FakeHermesRuns, every_s: float, stop) -> None:
    """One watcher tick every ``every_s`` seconds until ``stop`` is set; one line per tick."""
    number = 0
    while not stop.is_set():
        number += 1
        ctx = watcher_context.build(env, hermes=hermes)
        try:
            report = watcher_tick.tick(ctx).to_dict()
        except Exception as error:  # a dev tool keeps ticking and shows the failure
            report = {
                "has_work": None,
                "paused": None,
                "errors": [f"{type(error).__name__}: {error}"],
            }
        finally:
            ctx.wstore.close()
            if ctx.store is not None:
                ctx.store.close()
        stamp = datetime.now().astimezone().strftime("%H:%M:%S %Z")
        errors = report.get("errors") or []
        print(
            f"[{stamp}] tick {number}: has_work={report.get('has_work')}"
            f" paused={report.get('paused')} errors={errors if errors else 'none'}",
            flush=True,
        )
        stop.wait(every_s)


if __name__ == "__main__":
    sys.exit(main())
