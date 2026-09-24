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
const server: {
  rows: unknown[];
  notify: ((status: string) => void) | null;
  /** The INSERT handler the page installs, so a test can push a row at it. */
  insert: ((message: { new: unknown }) => void) | null;
} = {
  rows: [],
  notify: null,
  insert: null,
};

vi.mock("@/lib/supabase-browser", () => ({
  supabaseBrowser: () => ({
    channel: () => {
      const channel = {
        on: (_event: string, _config: unknown, handler: (m: { new: unknown }) => void) => {
          server.insert = handler;
          return channel;
        },
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
      // Every PostgREST method the component uses. `not` was added when the
      // page started filtering on `innings IS NOT NULL` - until then this
      // list was four entries and any fifth method broke all four resync
      // tests with a confusing "not is not a function". If a query here grows
      // a method, add it here too.
      for (const method of ["select", "eq", "not", "order", "gt", "limit"]) {
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
    // The ball key, as a COLUMN. Distinct from payload.innings: the column is
    // what migration 20260918000003 added and what every reader filters on,
    // and a row without it is one the partial unique index cannot dedupe.
    innings: 2,
    over_num: Math.floor(balls_bowled / 6),
    ball_in_over: (balls_bowled % 6) + 1,
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
  // SPEC.md §12.2's confidence labelling, now carried by texture rather than
  // by a coloured chip (UI-PHASE.md §4.3, §1.3). These two used to assert
  // that the chip's className contained "chip-low", which stopped being a
  // meaningful question when the chip stopped existing: its whole signal was
  // --warn, a colour, which conveys nothing in greyscale and nothing to a
  // reader who cannot separate it from the text around it.
  //
  // They are also no longer text searches. "powerplay" now appears in three
  // places on this page - the mark, the strip's caption, and the <title> on
  // the strip's phase baseline - so getByText(/powerplay/) matches all three
  // and throws. Querying the mark directly is both narrower and closer to
  // what the rule actually requires.

  it("marks a powerplay probability low-confidence, in texture and in words", () => {
    const { container } = render(
      <LiveMatch matchId={9339} header={header} initialPredictions={[ball(1, 6, "powerplay", 0.62)]} />
    );

    const mark = container.querySelector(".phase-mark");
    expect(mark).toBeTruthy();
    expect(mark?.getAttribute("data-low")).toBe("true");

    // Texture, not hue: a hatch pattern is what distinguishes it.
    expect(mark?.querySelector("pattern")).toBeTruthy();

    // And never texture ALONE - the words say the same thing, so the signal
    // survives for anyone who cannot see the swatch at all.
    expect(mark?.textContent).toContain("low confidence");
  });

  it("does not mark a death-overs probability low-confidence", () => {
    const { container } = render(
      <LiveMatch matchId={9339} header={header} initialPredictions={[ball(1, 114, "death", 0.62)]} />
    );

    const mark = container.querySelector(".phase-mark");
    expect(mark?.getAttribute("data-low")).toBe("false");
    expect(mark?.querySelector("pattern")).toBeNull();
    expect(mark?.textContent).toContain("higher confidence");
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

describe("unkeyed rows", () => {
  // Migration 20260918000003's contract is that every Phase 3 reader filters
  // on `innings IS NOT NULL`. This page did not, and was the only
  // user-facing surface rendering rows the partial unique index cannot
  // dedupe - for match 13143, ten interleaved runs of a twelve-ball deploy
  // smoke test drawn as a curve, while /matches listed it as never replayed.
  //
  // Both queries now filter. The Realtime channel CANNOT: PostgREST takes one
  // filter and it is spent on match_id, so the drop happens in the handler,
  // and that is what this asserts.

  it("drops a streamed row with no ball key", async () => {
    render(
      <LiveMatch matchId={9339} header={header} initialPredictions={[ball(1, 60, "middle", 0.5)]} />
    );
    expect(screen.getByText("50%")).toBeTruthy();

    // A DISTINCT probability, deliberately. The first version of this test
    // used p: 0.5 - the same value as the initial prediction - so it passed
    // with the filter removed and proved nothing. The probe must be visible
    // if it gets through.
    await act(async () => {
      server.insert?.({
        new: {
          prediction_id: 999,
          created_at: "2026-04-01T10:05:00Z",
          model_version: "winprob2-20260910",
          match_id: 9339,
          innings: null,
          payload: {
            p: 0.11,
            innings: 2,
            balls_bowled: 61,
            balls_remaining: 59,
            runs_required: 40,
            score: 110,
            wickets: 4,
            target: 150,
            phase: "middle",
          },
        },
      });
    });

    // Still the initial prediction, and the probe is nowhere on the page.
    expect(screen.queryByText("11%")).toBeNull();
    expect(screen.getByText("50%")).toBeTruthy();
  });

  it("still accepts a streamed row that has one", async () => {
    render(
      <LiveMatch matchId={9339} header={header} initialPredictions={[ball(1, 60, "middle", 0.5)]} />
    );

    await act(async () => {
      server.insert?.({ new: row(2, 61, 0.83) });
    });

    expect(screen.getByText("83%")).toBeTruthy();
  });
});
