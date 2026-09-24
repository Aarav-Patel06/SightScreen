/**
 * The header's one live element (UI-PHASE.md §3.1).
 *
 * A sparkline and the two team short names if a match is being predicted, and
 * nothing at all otherwise — §3.1 is explicit that the empty state is empty,
 * not a "no live matches" label. An interface that announces the absence of
 * something on every page is noise, and it is noise on the 99.9% of days when
 * no worker is running.
 *
 * A client component because the header lives in the root layout: server
 * rendering it would freeze it into whatever cache the surrounding page uses,
 * which on the landing page is an hour. Fetching on mount keeps the shell
 * static and the slot fresh, and /api/live absorbs the traffic behind a 30s
 * cache so this costs at most one database read per 30 seconds.
 *
 * Renders nothing until it has something, so there is no layout shift from a
 * spinner resolving to empty — which is what it resolves to almost always.
 */

"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { BallStrip } from "@/components/ball-strip";
import { toMarks } from "@/lib/ball-strip";
import type { WinProbPrediction } from "@/lib/prediction";

interface LivePayload {
  matchId: number;
  battingShort: string;
  bowlingShort: string;
  prediction: WinProbPrediction;
}

export function LiveSlot() {
  const [live, setLive] = useState<LivePayload | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const response = await fetch("/api/live");
        if (!response.ok) return;
        const body = (await response.json()) as { live: LivePayload | null };
        if (!cancelled) setLive(body.live);
      } catch {
        // An empty slot is the correct rendering of a failed lookup, so
        // there is nothing to handle. See app/api/live/route.ts.
      }
    }

    void poll();
    const timer = setInterval(poll, 30_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  if (live === null) return null;

  // One prediction is one mark, and one mark has no swing to show - the strip
  // needs a history. Until the slot carries the innings so far, this renders
  // the figure and the sides, which is what the header has room for anyway.
  const marks = toMarks([live.prediction]);
  const percent = Math.round(live.prediction.p * 100);

  return (
    <Link href={`/match/${live.matchId}`} className="live-slot">
      <span className="live-dot" aria-hidden="true" />
      <span className="live-teams tnum">
        {live.battingShort} v {live.bowlingShort}
      </span>
      <span className="live-strip" aria-hidden="true">
        <BallStrip marks={marks} height={18} defaultWidth={48} interactive={false} />
      </span>
      <span className="fig live-figure">{percent}%</span>
      <span className="visually-hidden">
        Live: {live.battingShort} versus {live.bowlingShort}, batting side win probability{" "}
        {percent} percent. Open the match page.
      </span>
    </Link>
  );
}
