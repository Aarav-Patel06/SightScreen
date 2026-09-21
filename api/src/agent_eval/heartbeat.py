"""Liveness for paid long-running jobs, written down rather than inferred.

Three times now, the apparatus has lied about whether a process was alive:

  1. A buffered `grep` pipe left a task's output file empty, so a running
     job looked like it had produced nothing.
  2. `ps` under Git Bash did not list a Windows process that was running.
  3. Together, those two made a COMPLETED 48-conversation eval look dead,
     and it was re-run. That one cost about $0.70.

The fix is a mechanism, not more care. **A job says it is alive by writing
that down.** Liveness is then a fact on disk with a timestamp on it, not an
inference from a process table that does not see across the Windows/MSYS
boundary, or from a stream that a pipe is free to buffer for as long as it
likes.

    beat = Heartbeat.start("thinking-ab", total=48)
    ...
    beat.tick(done=12, dollars=0.31)     # after each unit of work
    beat.finish()

    python -m agent_eval.heartbeat --status

`--status` is the thing to run before starting a paid job. It reports
RUNNING, STALE or FINISHED, and STALE is deliberately distinct from
FINISHED: a job that stopped writing without calling `finish()` crashed or
was killed, and that is a different situation from one that ended cleanly.

The PID is recorded for diagnosis only. **It is never consulted to decide
liveness** - that is exactly the question `ps` answered wrongly. The age of
`updated_at` is the answer, because a process that cannot write a file every
few seconds is not doing useful work either way.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HEARTBEAT_DIR = Path(__file__).resolve().parents[2] / "data" / "agent_eval"
HEARTBEAT_PATH = HEARTBEAT_DIR / ".heartbeat.json"

# A job is STALE if it has not written for this long. Generous: one eval
# conversation with thinking on runs 15-40 seconds, and a tick only happens
# between conversations, so anything under a couple of minutes would report
# a healthy job as dead - which is the false alarm that started all this.
STALE_AFTER_SECONDS = 180


@dataclass
class State:
    label: str
    pid: int
    started_at: str
    updated_at: str
    total: int = 0
    done: int = 0
    dollars: float = 0.0
    finished: bool = False
    note: str = ""
    extra: dict = field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Heartbeat:
    """Writes liveness to disk. Cheap enough to call between every unit."""

    def __init__(self, state: State, path: Path):
        self.state = state
        self.path = path

    @classmethod
    def start(cls, label: str, *, total: int = 0, path: Path | None = None) -> "Heartbeat":
        path = path or HEARTBEAT_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        state = State(
            label=label, pid=os.getpid(), started_at=_now(), updated_at=_now(), total=total
        )
        beat = cls(state, path)
        beat._write()
        return beat

    def tick(self, *, done: int | None = None, dollars: float | None = None, note: str = "") -> None:
        if done is not None:
            self.state.done = done
        if dollars is not None:
            self.state.dollars = dollars
        if note:
            self.state.note = note
        self.state.updated_at = _now()
        self._write()

    def finish(self, note: str = "") -> None:
        self.state.finished = True
        self.state.updated_at = _now()
        if note:
            self.state.note = note
        self._write()

    def _write(self) -> None:
        # Write-then-replace, so a reader never sees a half-written file. A
        # --status that reported "corrupt" because it caught a partial write
        # would be one more instrument lying about liveness.
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self.state), indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def __enter__(self) -> "Heartbeat":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.finish(note="" if exc is None else f"exited with {exc_type.__name__}: {exc}")


def read(path: Path | None = None) -> State | None:
    path = path or HEARTBEAT_PATH
    if not path.exists():
        return None
    try:
        return State(**json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError):
        return None


def status(path: Path | None = None) -> tuple[str, str]:
    """(state, human-readable line). Never consults the process table."""
    state = read(path)
    if state is None:
        return "NONE", "no heartbeat file - no paid job has run, or it was cleared"

    age = time.time() - datetime.fromisoformat(state.updated_at).timestamp()
    progress = f"{state.done}/{state.total}" if state.total else str(state.done)
    money = f"${state.dollars:.4f}"
    base = f"{state.label} pid={state.pid} {progress} {money}"

    if state.finished:
        return "FINISHED", f"FINISHED  {base} (ended {age:.0f}s ago) {state.note}".strip()
    if age > STALE_AFTER_SECONDS:
        return "STALE", (
            f"STALE     {base} - last wrote {age:.0f}s ago, over the "
            f"{STALE_AFTER_SECONDS}s threshold. It crashed or was killed; it did NOT "
            "finish cleanly. Check the results file before re-running - a partial "
            "run has already spent money."
        )
    return "RUNNING", f"RUNNING   {base} - last wrote {age:.0f}s ago"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--clear", action="store_true")
    args = parser.parse_args(argv)

    if args.clear:
        HEARTBEAT_PATH.unlink(missing_ok=True)
        print("cleared")
        return 0

    state, line = status()
    print(line)
    # RUNNING exits 1 so a script can refuse to start a second paid job:
    #   python -m agent_eval.heartbeat --status || echo "already running"
    return 1 if state == "RUNNING" else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
