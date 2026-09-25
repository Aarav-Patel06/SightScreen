/**
 * The honesty rules on the accuracy page, asserted (SPEC.md §9.3, §12.2).
 *
 * These are the claims that are easy to break by accident and impossible to
 * notice by looking: a baseline described as "better" when its interval
 * includes zero, a decile with 12 matches rendered at the same weight as one
 * with 100, an error bar drawn from the wrong end of the interval.
 */

import { describe, expect, it } from "vitest";

import {
  LOGISTIC_PRIOR,
  baselineVerdict,
  compareSegments,
  isThin,
  parseReport,
  percent,
  populatedDeciles,
  reliabilityPoints,
  score,
  type BaselineComparison,
  type Decile,
  withInterval,
  withSignedInterval,
} from "./accuracy";

function decile(overrides: Partial<Decile> = {}): Decile {
  return {
    bin_low: 0.5,
    bin_high: 0.6,
    n: 682,
    n_matches: 49,
    mean_predicted: 0.549,
    observed_rate: 0.764,
    ci_low: 0.573,
    ci_high: 0.899,
    contains_predicted: false,
    ...overrides,
  };
}

describe("parseReport", () => {
  it("returns null rather than throwing on a shape it does not recognise", () => {
    // An older monitor's report must not blank the page.
    expect(parseReport(null)).toBeNull();
    expect(parseReport({})).toBeNull();
    expect(parseReport({ model_version: "v1" })).toBeNull();
    expect(parseReport("not an object")).toBeNull();
  });

  it("accepts a well formed report", () => {
    const parsed = parseReport({ model_version: "winprob2-20260910", populations: {} });
    expect(parsed?.model_version).toBe("winprob2-20260910");
  });
});

describe("populatedDeciles", () => {
  it("drops empty bins, which have nothing to plot", () => {
    const empty = decile({ n: 0, n_matches: 0, mean_predicted: null, observed_rate: null });
    expect(populatedDeciles([decile(), empty])).toHaveLength(1);
  });

  it("is safe on a report with no reliability table at all", () => {
    expect(populatedDeciles(undefined)).toEqual([]);
  });
});

describe("thin samples", () => {
  it("judges thinness by matches, not by rows", () => {
    // §9.3: balls within a match are correlated, so 2,377 rows can be 47
    // effective observations. A row count cannot tell you this.
    expect(isThin({ n_matches: 12 })).toBe(true);
    expect(isThin({ n_matches: 49 })).toBe(false);
  });
});

describe("baselineVerdict", () => {
  it("claims the model is better only when the interval excludes zero", () => {
    const verdict = baselineVerdict({
      baseline_brier: 0.1169,
      model_brier: 0.102,
      improvement: 0.0149,
      ci_low: 0.0014,
      ci_high: 0.0284,
      n_matches: 100,
      model_is_better: true,
    });
    expect(verdict).toContain("model is better by 0.0149");
    expect(verdict).toContain("100 matches");
  });

  it("refuses to say better when the interval includes zero", () => {
    const verdict = baselineVerdict({
      baseline_brier: 0.105,
      model_brier: 0.102,
      improvement: 0.003,
      ci_low: -0.004,
      ci_high: 0.01,
      n_matches: 40,
      model_is_better: false,
    });
    expect(verdict).toContain("no significant difference");
    expect(verdict).toContain("includes zero");
    expect(verdict).not.toContain("model is better");
  });

  it("passes through an unavailable comparison instead of inventing one", () => {
    expect(baselineVerdict({ unavailable: "no baseline artifact registered" })).toBe(
      "no baseline artifact registered"
    );
  });
});

describe("reliabilityPoints", () => {
  it("converts the interval into offsets from the point, not absolute bounds", () => {
    // Getting this backwards draws bars of the wrong length in the wrong
    // direction, and the chart still renders perfectly.
    const [point] = reliabilityPoints([decile()]);
    expect(point.observed).toBeCloseTo(0.764);
    expect(point.error[0]).toBeCloseTo(0.764 - 0.573);
    expect(point.error[1]).toBeCloseTo(0.899 - 0.764);
  });

  it("carries the pass/fail verdict and both sample counts through", () => {
    const [point] = reliabilityPoints([decile()]);
    expect(point.ok).toBe(false);
    expect(point.n).toBe(682);
    expect(point.matches).toBe(49);
    expect(point.label).toBe("50%-60%");
  });

  it("treats a tri-state contains_predicted of null as not a pass", () => {
    const [point] = reliabilityPoints([decile({ contains_predicted: null })]);
    expect(point.ok).toBe(false);
  });
});

describe("formatting", () => {
  it("shows whole percents, never spurious precision", () => {
    expect(percent(0.7378)).toBe("74%");
    expect(percent(0.549)).toBe("55%");
  });

  it("quotes Brier at four decimals, the project convention", () => {
    expect(score(0.10198726751839225)).toBe("0.1020");
  });

  it("never renders a point estimate without its interval", () => {
    expect(withInterval(0.102, 0.0745, 0.1311)).toBe("0.1020  [0.0745, 0.1311]");
  });
});

// --- the segment comparison (UI Phase 2 step 1 follow-up) -----------------
//
// These exist because the segment block on /accuracy is a NULL result, and a
// null result is the easy thing to accidentally render as a finding.

describe("compareSegments", () => {
  const at = (low: number, high: number, significant: boolean): BaselineComparison => ({
    baseline_brier: 0.12,
    model_brier: 0.11,
    improvement: (low + high) / 2,
    ci_low: low,
    ci_high: high,
    n_matches: 100,
    model_is_better: significant,
  });

  it("reports overlap for the real figures, and that neither is significant", () => {
    // The measured values, 2026-09-24: Full Member vs franchise.
    const fullMember = at(-0.005682, 0.020812, false);
    const franchise = at(-0.0038, 0.026108, false);

    expect(compareSegments(fullMember, franchise)).toEqual({
      overlap: true,
      eitherSignificant: false,
    });
  });

  it("detects genuinely separated intervals", () => {
    expect(compareSegments(at(0.001, 0.01, true), at(0.05, 0.09, true))).toEqual({
      overlap: false,
      eitherSignificant: true,
    });
  });

  it("treats intervals that merely touch as overlapping", () => {
    // Conservative on purpose: this is not a significance test and must not
    // be read as one.
    expect(compareSegments(at(0.0, 0.02, false), at(0.02, 0.04, false))?.overlap).toBe(true);
  });

  it("returns null when either segment is unavailable", () => {
    expect(compareSegments({ unavailable: "no matches" }, at(0, 0.02, false))).toBeNull();
    expect(compareSegments(undefined, at(0, 0.02, false))).toBeNull();
  });
});

describe("the committed prior for the logistic comparison", () => {
  it("is the run 9 result, and it excluded zero", () => {
    // If this stops being true the page's "did not survive a larger sample"
    // sentence is telling a story that did not happen.
    expect(LOGISTIC_PRIOR.ciLow).toBeGreaterThan(0);
    expect(LOGISTIC_PRIOR.improvement).toBeGreaterThan(LOGISTIC_PRIOR.ciLow);
    expect(LOGISTIC_PRIOR.nMatches).toBe(100);
  });
});

describe("withSignedInterval", () => {
  it("signs every bound of a wholly positive interval", () => {
    expect(withSignedInterval(0.0375, 0.0218, 0.0539)).toBe("+0.0375  [+0.0218, +0.0539]");
  });

  it("makes an interval that straddles zero obvious", () => {
    // The landing scorecard's whole job: these two must not look alike.
    expect(withSignedInterval(0.0083, -0.0027, 0.0188)).toBe("+0.0083  [-0.0027, +0.0188]");
  });

  it("signs a negative point estimate too", () => {
    expect(withSignedInterval(-0.01, -0.03, 0.01)).toBe("-0.0100  [-0.0300, +0.0100]");
  });
});
