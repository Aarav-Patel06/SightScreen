/**
 * One model version per match, on every page (SPEC.md section 12.2).
 *
 * The predictions table's unique key includes model_version, so a match can
 * hold a full chase from two models - the served one and a shadow, or an old
 * one and its regenerated backfill. Every reader used to take every row, so
 * such a match drew both chases interleaved into one curve. The rule: the
 * ACTIVE version if the match has rows from it, otherwise the NEWEST version
 * (latest trained_at) it has rows from.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import { fakeSupabase } from "./fake-postgrest";
import { chooseVersion, isShownVersion, oneSource, oneVersionPerMatch, type ModelVersionInfo } from "./model-version";

let db: ReturnType<typeof fakeSupabase>;
vi.mock("./supabase-server", () => ({ supabaseServer: () => db }));

const { loadHeroMatch } = await import("./hero-match");
const { loadLandingPreviews } = await import("./landing-previews");
const { loadMatchIndex } = await import("./match-index");
const { loadMatchPredictions } = await import("./match-predictions");

const OLD = "winprob2-20260910";
const NEW = "winprob2-20260927";
const versions = (active: string | null): ModelVersionInfo[] => [
  { model_version: OLD, is_active: active === OLD, trained_at: "2026-09-10T00:57:20Z" },
  { model_version: NEW, is_active: active === NEW, trained_at: "2026-09-27T12:00:00Z" },
];

const ENGLAND = 29;
const INDIA = 20;
const MATCH = {
  match_id: 8429, competition: "India tour of England", format: "ODI",
  start_time: "2026-07-19T00:00:00+00:00", team_a: ENGLAND, team_b: INDIA, venue_id: null,
  winner: ENGLAND, status: "complete", result_method: "normal", target_runs: 388,
  win_by_runs: 27, win_by_wickets: null, outcome_method: null, tie_winner: null, tie_decided_by: null,
};

/** One chase of `n` balls from `version`, every p equal to `p` so the version is visible in the marks. */
function chase(version: string, p: number, firstId: number, n = 6) {
  return Array.from({ length: n }, (_, i) => ({
    prediction_id: firstId + i, match_id: MATCH.match_id, created_at: "2026-07-19T18:00:00+00:00",
    model_version: version, prediction_type: "win_prob", innings: 2, source: "backfill",
    batting_team_id: INDIA,
    payload: { p, innings: 2, balls_bowled: i, balls_remaining: 300 - i,
      runs_required: 388 - i, score: i, wickets: 0, target: 388, phase: "powerplay" },
  }));
}

function seed(active: string | null) {
  db = fakeSupabase({
    teams: [
      { team_id: ENGLAND, name: "England", full_member: true },
      { team_id: INDIA, name: "India", full_member: true },
    ],
    matches: [MATCH],
    // The old model's chase was written first, the new one's regenerated later.
    predictions: [...chase(OLD, 0.3, 1), ...chase(NEW, 0.7, 101)],
    model_versions: versions(active) as unknown as Record<string, unknown>[],
    prediction_outcomes: [],
    player_index: [],
  });
}

beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

describe("chooseVersion", () => {
  const rows = [...chase(OLD, 0.3, 1), ...chase(NEW, 0.7, 101)];

  it("takes the active version when the match has rows from it", () => {
    expect(chooseVersion(rows, versions(OLD))).toBe(OLD);
    expect(chooseVersion(rows, versions(NEW))).toBe(NEW);
  });

  it("otherwise takes the newest version the match has rows from", () => {
    expect(chooseVersion(rows, versions(null))).toBe(NEW);
    expect(chooseVersion(chase(OLD, 0.3, 1), versions(NEW))).toBe(OLD);
  });

  it("falls back to the newest rows when the versions are unknown", () => {
    expect(chooseVersion(rows, [])).toBe(NEW);
    expect(chooseVersion([], versions(OLD))).toBeNull();
  });

  it("keeps one version per match across several matches", () => {
    const other = chase(NEW, 0.9, 500).map((r) => ({ ...r, match_id: 9000 }));
    const kept = oneVersionPerMatch([...rows, ...other], versions(OLD));
    expect(new Set(kept.filter((r) => r.match_id === 8429).map((r) => r.model_version))).toEqual(new Set([OLD]));
    expect(kept.filter((r) => r.match_id === 9000)).toHaveLength(6);
  });
});

describe("a match with rows from two versions draws one curve", () => {
  for (const active of [OLD, NEW]) {
    const p = active === OLD ? 0.3 : 0.7;

    it(`the landing hero, ${active} active`, async () => {
      seed(active);
      const hero = await loadHeroMatch();
      expect(hero.marks).toHaveLength(6);
      expect(new Set(hero.marks!.map((m) => m.p))).toEqual(new Set([p]));
    });

    it(`the landing previews, ${active} active`, async () => {
      seed(active);
      const previews = await loadLandingPreviews();
      const match = previews.matches!.find((m) => m.matchId === 8429)!;
      expect(match.marks).toHaveLength(6);
      expect(new Set(match.marks!.map((m) => m.p))).toEqual(new Set([p]));
    });

    it(`/matches, ${active} active`, async () => {
      seed(active);
      const index = await loadMatchIndex();
      const row = index.matches.find((m) => m.matchId === 8429)!;
      expect(row.marks).toHaveLength(6);
      expect(new Set(row.marks!.map((m) => m.p))).toEqual(new Set([p]));
    });

    it(`the match page, ${active} active`, async () => {
      seed(active);
      const { predictions, modelVersion } = await loadMatchPredictions(8429);
      expect(predictions).toHaveLength(6);
      expect(new Set(predictions.map((x) => x.model_version))).toEqual(new Set([active]));
      expect(modelVersion).toBe(active);
    });
  }

  it("the match page, with no active version among the match's rows, shows the newest", async () => {
    seed(null);
    const { predictions, modelVersion } = await loadMatchPredictions(8429);
    expect(new Set(predictions.map((x) => x.model_version))).toEqual(new Set([NEW]));
    expect(modelVersion).toBe(NEW);
  });

  it("the match page, before a live match has any rows, locks onto the active version", async () => {
    seed(NEW);
    const { predictions, modelVersion } = await loadMatchPredictions(424242);
    expect(predictions).toHaveLength(0);
    expect(modelVersion).toBe(NEW);
  });
});

/**
 * Session 3: once the daily Cricsheet job merges a match the live worker
 * predicted, the match holds the live chase AND the complete Cricsheet
 * version under the SAME model version, told apart only by `source`. The
 * page shows the Cricsheet version (complete, exact); the live rows stay the
 * live cohort for /accuracy and are never drawn beside it.
 */
describe("a match with live and backfill rows of the same version draws one curve", () => {
  const live = (p: number, firstId: number) => chase(OLD, p, firstId).map((r) => ({ ...r, source: "live" }));

  function seedSources(predictions: ReturnType<typeof chase>) {
    db = fakeSupabase({
      teams: [
        { team_id: ENGLAND, name: "England", full_member: true },
        { team_id: INDIA, name: "India", full_member: true },
      ],
      matches: [MATCH],
      predictions,
      model_versions: versions(OLD) as unknown as Record<string, unknown>[],
      prediction_outcomes: [],
      player_index: [],
    });
  }

  it("oneSource keeps the backfill rows when there are any, else the live ones", () => {
    const both = [...live(0.3, 1), ...chase(OLD, 0.7, 101)];
    expect(new Set(oneSource(both).map((r) => r.source))).toEqual(new Set(["backfill"]));
    expect(oneSource(live(0.3, 1))).toHaveLength(6);
  });

  // The live chase was written first; the Cricsheet version arrives later.
  const merged = () => [...live(0.3, 1), ...chase(OLD, 0.7, 101)];

  it("the match page", async () => {
    seedSources(merged());
    const { predictions } = await loadMatchPredictions(8429);
    expect(predictions).toHaveLength(6);
    expect(new Set(predictions.map((x) => x.p))).toEqual(new Set([0.7]));
  });

  it("the landing hero", async () => {
    seedSources(merged());
    const hero = await loadHeroMatch();
    expect(hero.marks).toHaveLength(6);
    expect(new Set(hero.marks!.map((m) => m.p))).toEqual(new Set([0.7]));
  });

  it("the landing previews", async () => {
    seedSources(merged());
    const previews = await loadLandingPreviews();
    const match = previews.matches!.find((m) => m.matchId === 8429)!;
    expect(match.marks).toHaveLength(6);
    expect(new Set(match.marks!.map((m) => m.p))).toEqual(new Set([0.7]));
  });

  it("/matches", async () => {
    seedSources(merged());
    const index = await loadMatchIndex();
    const row = index.matches.find((m) => m.matchId === 8429)!;
    expect(row.marks).toHaveLength(6);
    expect(new Set(row.marks!.map((m) => m.p))).toEqual(new Set([0.7]));
  });

  it("a live-only match still draws its live curve", async () => {
    seedSources(live(0.3, 1));
    const { predictions } = await loadMatchPredictions(8429);
    expect(predictions).toHaveLength(6);
    expect(new Set(predictions.map((x) => x.p))).toEqual(new Set([0.3]));
  });
});

describe("the live match page's stream and resync keep to the version the server chose", () => {
  it("drops a streamed row from any other version", () => {
    expect(isShownVersion(OLD, OLD)).toBe(true);
    expect(isShownVersion(NEW, OLD)).toBe(false);
  });

  it("accepts any version only when the server had nothing to choose from", () => {
    expect(isShownVersion(NEW, null)).toBe(true);
  });
});
