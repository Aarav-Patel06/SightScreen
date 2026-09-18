/**
 * The honesty rules, asserted (SPEC.md 12.2).
 *
 * These are the three things about this page that are easy to break by
 * accident and impossible to notice by looking: a pre-death probability
 * shown without a low-confidence marker, an invented latency figure, and a
 * history-strip cell that implies a pre-toss prediction exists when none was
 * ever produced. Each is a claim about what the model knows, so each gets a
 * test rather than a review.
 */

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WinProbPrediction } from "@/lib/prediction";

// The component opens a Realtime channel on mount. jsdom has no websocket
// worth the name, and this test is about what is rendered, not about the
// subscription - Decision 4's merge is tested as a pure function instead.
// Rows the fake server will return from a reconcile, and the subscribe
// callback the page installs - so a test can drive both halves of Decision
// 4 without a websocket.
const server: { rows: unknown[]; notify: ((status: string) => void) | null } = {
  rows: [],
  notify: null,
};

vi.mock("@/lib/supabase-browser", () => ({
  supabaseBrowser: () => ({
    channel: () => {
      const channel = {
        on: () => channel,
        subscribe: (cb: (status: string) => void) => {
          server.notify = cb;
          return channel;
        },
      };
      return channel;
    },
    // A chainable query builder that resolves to whatever the test put in
    // `server.rows`. Every method returns the same object, and the object is
    // a thenable, so .select().eq().eq().order() awaits to a result.
    from: () => {
      const builder: Record<string, unknown> = {
        then: (resolve: (value: { data: unknown[]; error: null }) => void) =>
          resolve({ data: server.rows, error: null }),
      };
      for (const method of ["select", "eq", "order", "gt"]) {
        builder[method] = () => builder;
      }
      return builder;
    },
    removeChannel: () => Promise.resolve("ok"),
  }),
}));

const { LiveMatch } = await import("./live-match");

const header = {
  matchId: 9339,
  competition: "Indian Premier League",
  format: "T20",
  teamA: "Chasers",
  teamB: "Defenders",
  venue: "Somewhere",
  startDate: "2026-04-01",
};

function ball(
  prediction_id: number,
  balls_bowled: number,
  phase: WinProbPrediction["phase"],
  p: number
): WinProbPrediction {
  return {
    prediction_id,
    created_at: "2026-04-01T10:00:00Z",
    model_version: "winprob2-20260910",
    p,
    innings: 2,
    balls_bowled,
    balls_remaining: 120 - balls_bowled,
    runs_required: 40,
    score: 110,
    wickets: 4,
    target: 150,
    phase,
  };
}

afterEach(() => {
  cleanup();
  server.rows = [];
  server.notify = null;
  vi.useRealTimers();
});

/** The wire shape: a predictions row, not the parsed one. */
function row(prediction_id: number, balls_bowled: number, p: number) {
  return {
    prediction_id,
    created_at: "2026-04-01T10:00:00Z",
    model_version: "winprob2-20260910",
    match_id: 9339,
    payload: {
      p,
      innings: 2,
      balls_bowled,
      balls_remaining: 120 - balls_bowled,
      runs_required: 40,
      score: 110,
      wickets: 4,
      target: 150,
      phase: "middle",
    },
  };
}

describe("LiveMatch", () => {
  it("marks a powerplay probability low-confidence", () => {
    render(<LiveMatch matchId={9339} header={header} initialPredictions={[ball(1, 6, "powerplay", 0.62)]} />);
    const chip = screen.getByText(/powerplay/);
    expect(chip.className).toContain("chip-low");
  });

  it("does not mark a death-overs probability low-confidence", () => {
    render(<LiveMatch matchId={9339} header={header} initialPredictions={[ball(1, 114, "death", 0.62)]} />);
    const chip = screen.getByText(/death overs/);
    expect(chip.className).not.toContain("chip-low");
  });

  it("states the polling interval and refuses to claim a provider lag", () => {
    // Per SPEC.md 4.3's 2026-09-14 edit. A figure like "42s behind live"
    // would be measuring one leg of the delay and reporting it as the whole.
    render(<LiveMatch matchId={9339} header={header} initialPredictions={[ball(1, 60, "middle", 0.5)]} />);
    expect(screen.getByText(/updates every 15s/)).toBeTruthy();
    expect(screen.queryByText(/behind live/)).toBeNull();
  });

  it("shows phases that were never produced as absent, not as numbers", () => {
    render(<LiveMatch matchId={9339} header={header} initialPredictions={[ball(1, 60, "middle", 0.5)]} />);
    for (const label of ["pre-toss", "post-toss", "innings break"]) {
      expect(screen.getByText(new RegExp(`${label}: not produced`))).toBeTruthy();
    }
  });

  it("rounds to whole percent rather than implying spurious precision", () => {
    render(<LiveMatch matchId={9339} header={header} initialPredictions={[ball(1, 60, "middle", 0.7378)]} />);
    expect(screen.getByText("74%")).toBeTruthy();
    expect(screen.queryByText(/73\.78/)).toBeNull();
  });

  it("renders without a probability rather than blanking when a match has none", () => {
    render(<LiveMatch matchId={9339} header={header} initialPredictions={[]} />);
    expect(screen.getByText(/No predictions yet/)).toBeTruthy();
    expect(screen.getByText("--")).toBeTruthy();
  });
});

describe("the resync safety net", () => {
  // Measured on a cold channel: SUBSCRIBED, then inserts dropped
  // intermittently for several seconds with nothing to show for it. These
  // two assert the page survives that rather than rendering a curve with
  // holes in it and looking fine.

  it("recovers a row the stream never delivered, and says it did", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    server.rows = [row(1, 60, 0.5)];
    render(<LiveMatch matchId={9339} header={header} initialPredictions={[]} />);

    await act(async () => {
      server.notify?.("SUBSCRIBED");
    });
    expect(screen.getByText("50%")).toBeTruthy();

    // Ball 61 is written, and the stream does not carry it.
    server.rows = [row(1, 60, 0.5), row(2, 61, 0.83)];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(screen.getByText("83%")).toBeTruthy();
    expect(screen.getByText(/1 update the stream dropped/)).toBeTruthy();
  });

  it("does not claim a drop for rows that arrived before it was listening", async () => {
    // The initial reconcile closes the server-render gap. Those rows were
    // never the stream's job, so reporting them as dropped would be crying
    // wolf on every page load.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    server.rows = [row(1, 60, 0.5), row(2, 61, 0.83)];
    render(<LiveMatch matchId={9339} header={header} initialPredictions={[]} />);

    await act(async () => {
      server.notify?.("SUBSCRIBED");
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(screen.getByText("83%")).toBeTruthy();
    expect(screen.queryByText(/dropped/)).toBeNull();
  });
});
