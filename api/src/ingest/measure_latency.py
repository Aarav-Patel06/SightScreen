"""Measure real serving latency against a live feed (SPEC.md section 4.3,
Phase 2 session 2, Decision 5).

**What this can and cannot measure, stated up front.** SPEC.md section 4.3
claims a prediction appears 30-60s after the ball is bowled. That full chain
cannot be measured on CricketData: no endpoint carries a per-ball timestamp
(`currentMatches`, `cricScore` and `matches` all give only `dateTimeGMT`,
the match start). Nothing here estimates the missing piece.

What is measured, precisely:
  - `provider_rtt`      poll issued -> response received
  - `reconstruction`    response received -> delivery stream rebuilt
  - `prediction`        rebuilt -> win probability computed
  - `poll_to_write`     the sum: everything we are actually responsible for
  - `detection_bound`   poll_to_write + the poll interval, an upper bound on
                        how stale our view can be, EXCLUDING the provider's
                        own unmeasurable lag behind the ground

Budget: hard-capped by --max-hits (default 200, 10% of the 2,000/day tier).
The tool exits when it reaches it rather than eating the day's allowance.

Usage (from the api/ directory, with api/.env configured):
    python -m ingest.measure_latency --max-hits 200 --interval 30
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import dotenv_values

from ingest.cricketdata import CricketDataClient, HttpTransport

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"
REPORTS_DIR = REPO_ROOT / "api" / "data" / "eval_reports"

DEFAULT_MAX_HITS = 200


@dataclass
class LatencySample:
    provider_rtt: float
    reconstruction: float
    prediction: float
    new_deliveries: int

    @property
    def poll_to_write(self) -> float:
        return self.provider_rtt + self.reconstruction + self.prediction


@dataclass
class LatencyRecorder:
    """Timestamps one poll at a time. Only polls that actually produced new
    deliveries are recorded - a no-op poll measures nothing about how fast a
    ball reaches a prediction."""

    samples: list[LatencySample] = field(default_factory=list)

    def record(self, provider_rtt: float, reconstruction: float, prediction: float, new_deliveries: int) -> None:
        if new_deliveries > 0:
            self.samples.append(LatencySample(provider_rtt, reconstruction, prediction, new_deliveries))

    def summary(self, interval: float) -> dict:
        if not self.samples:
            return {"n": 0}
        totals = sorted(s.poll_to_write for s in self.samples)

        def pct(values: list[float], p: float) -> float:
            return values[min(len(values) - 1, int(len(values) * p))]

        return {
            "n": len(totals),
            "provider_rtt_median": statistics.median(s.provider_rtt for s in self.samples),
            "reconstruction_median": statistics.median(s.reconstruction for s in self.samples),
            "prediction_median": statistics.median(s.prediction for s in self.samples),
            "poll_to_write_median": statistics.median(totals),
            "poll_to_write_p90": pct(totals, 0.90),
            "poll_to_write_max": totals[-1],
            "detection_bound_median": statistics.median(totals) + interval,
            "poll_interval": interval,
        }


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    for key in ("LIVE_API_KEY", "LOCAL_DATABASE_URL"):
        if not env.get(key):
            sys.exit(f"{key} must be set in {ENV_PATH}")
    return env


def run(max_hits: int, interval: float, out_path: Path | None = None) -> dict:
    env = _env()
    recorder = LatencyRecorder()
    started_at = datetime.now(timezone.utc)
    hits = 0

    with psycopg.connect(env["LOCAL_DATABASE_URL"], autocommit=True) as conn:
        client = CricketDataClient(conn, HttpTransport(env["LIVE_API_KEY"]))

        issued = time.monotonic()
        summaries = client.list_live_matches()
        hits += 1
        if not summaries:
            report = {
                "status": "pending",
                "reason": "no live match during this run - nothing measured, nothing estimated",
                "started_at": started_at.isoformat(),
                "hits_used": hits,
                "rejected_matches": client._rejections,
            }
            print(json.dumps(report, indent=2))
            return report

        match_id = summaries[0].match_id
        state = client.get_match_state(match_id)
        print(f"tracking match_id={match_id} format={state.format} "
              f"(budget {max_hits} hits, interval {interval}s)")

        while hits < max_hits:
            issued = time.monotonic()
            before = len(client.get_deliveries_since(match_id, 0))
            new = client.poll(match_id)
            received = time.monotonic()
            hits += 1

            reconstruction_done = time.monotonic()
            # A prediction is only meaningful once a chase is under way; the
            # innings-1 model does not exist yet (section 6.3/Phase 4).
            prediction_start = time.monotonic()
            state = client.get_match_state(match_id)
            prediction_done = time.monotonic()

            recorder.record(
                provider_rtt=received - issued,
                reconstruction=reconstruction_done - received,
                prediction=prediction_done - prediction_start,
                new_deliveries=len(new),
            )
            if new:
                print(f"  +{len(new)} deliveries  ball_count={before + len(new)}  "
                      f"poll_to_write={recorder.samples[-1].poll_to_write:.3f}s")
            if state.status == "complete":
                break
            time.sleep(interval)

    report = {
        "status": "measured" if recorder.samples else "pending",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "hits_used": hits,
        "max_hits": max_hits,
        "match_id": match_id,
        "format": state.format,
        "samples": [asdict(s) for s in recorder.samples],
        "summary": recorder.summary(interval),
        "unmeasurable": (
            "ball bowled -> prediction written: CricketData exposes no per-ball "
            "timestamp, so the provider's own lag behind the ground cannot be "
            "measured and is NOT included in any figure here"
        ),
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = out_path or REPORTS_DIR / f"latency_{int(time.time())}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"Report written to {path}")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="measure_latency")
    parser.add_argument("--max-hits", type=int, default=DEFAULT_MAX_HITS)
    parser.add_argument("--interval", type=float, default=30.0)
    args = parser.parse_args(argv)
    run(args.max_hits, args.interval)


if __name__ == "__main__":
    main()
