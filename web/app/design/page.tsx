/**
 * The design reference (UI-PHASE.md §5, session 1).
 *
 * Every token, type size and strip variant on one page, so that a decision
 * about any of them can be made by looking rather than by imagining. Static
 * and offline: it reads a committed fixture and imports no Supabase client,
 * so it renders with Docker stopped, with no environment, and while the
 * Supabase project is paused. A reference that is sometimes blank is not a
 * reference.
 *
 * It also documents its own failures. The texture row renders hatch at 24px
 * and again at 4px, where it stops working; the strip renders at the four
 * sizes §1.2 asks for, including the two where fill cannot survive. Showing
 * the floor is the point - a design page that only showed the cases that work
 * would be how the 2.5px problem reached production.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { BallStrip } from "@/components/ball-strip";
import { TextureLegend } from "@/components/textures";
import {
  HATCH_MIN_PX,
  HATCH_PITCH_PX,
  pitchFor,
  tierFor,
  tierHasFill,
  tierHasPerMarkHit,
  toMarks,
  type Mark,
} from "@/lib/ball-strip";
import { parsePrediction, type WinProbPrediction } from "@/lib/prediction";
import {
  AA_TEXT,
  FILL_FLOOR,
  FILL_TOKENS,
  TEXT_TOKENS,
  contrast,
  parseThemeTokens,
} from "@/lib/tokens";

import fixture from "@/lib/fixtures/innings-8532.json";

export const metadata = { title: "Design reference — SightScreen" };

// --- the fixture ----------------------------------------------------------

/**
 * Parsed through parsePrediction rather than trusted, so that a fixture
 * regenerated against a changed payload shape fails here instead of rendering
 * a subtly wrong strip. created_at is supplied because the parser needs the
 * row shape; the fixture drops it deliberately (see make-strip-fixture.mjs).
 */
const predictions: WinProbPrediction[] = fixture.rows
  .map((row) => parsePrediction({ ...row, created_at: "" } as never))
  .filter((p): p is WinProbPrediction => p !== null);

const marks = toMarks(predictions);

/**
 * A synthetic 301-delivery innings, clearly labelled as such.
 *
 * Eight of the hundred replayed matches are ODIs, whose second innings runs to
 * 301 deliveries - and none of them is in the fixture, because the fixture is
 * one match. This exists only to exercise the `area` tier's geometry at a
 * length the T20 fixture cannot reach. It is a seeded walk, not a model
 * output, and nothing on this page presents it as a real innings.
 */
function syntheticOdi(): Mark[] {
  let seed = 20260922;
  const random = () => {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed / 2147483648;
  };

  let p = 0.5;
  return Array.from({ length: 301 }, (_, index) => {
    const swing = (random() - 0.5) * (index > 240 ? 0.18 : 0.05);
    const before = p;
    p = Math.min(0.98, Math.max(0.02, p + swing));
    return {
      index,
      predictionId: index,
      p: before,
      swing: p - before,
      event: random() > 0.6 ? "score" : "dot",
      phase: index < 60 ? "powerplay" : index < 240 ? "middle" : "death",
      ballsBowled: index,
      score: Math.round(index * 1.1),
      wickets: Math.floor(index / 50),
      runsRequired: Math.max(0, 280 - Math.round(index * 1.1)),
    } satisfies Mark;
  });
}

const odiMarks = syntheticOdi();

// --- contrast -------------------------------------------------------------

/**
 * Read from globals.css at build time, not retyped here.
 *
 * An earlier version of this page held its own copy of the seven hexes, which
 * is the same mistake lib/tokens.test.ts exists to prevent: the reference
 * page would keep displaying the old value after someone adjusted a hue, and
 * the display would be confidently wrong. The stylesheet is the only source.
 */
const TOKENS = parseThemeTokens(
  readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8")
);

const ROLES: Record<string, string> = {
  "--paper": "Page base. Cream.",
  "--paper-raised": "Cards, the hero panel",
  "--chrome": "Header, footer",
  "--chrome-deep": "Table header rows",
  "--ink": "Text and rules. A warm near-black.",
  "--ink-soft": "Secondary text, axis labels",
  "--rule": "Hairlines, table borders, strip baseline",
  "--bat": "Batting side — fill only",
  "--bowl": "Bowling side — fill only",
  "--bat-text": "Batting side, in words",
  "--bowl-text": "Bowling side, in words",
  "--flag": "Wickets, failed calibration bands, caps reached",
};

/** Which floor each token answers to, from the registry in lib/tokens.ts. */
function floorFor(token: string): { label: string; floor: number } | null {
  if ((TEXT_TOKENS as readonly string[]).includes(token)) {
    return { label: "text", floor: AA_TEXT };
  }
  if ((FILL_TOKENS as readonly string[]).includes(token)) {
    return { label: "fill", floor: FILL_FLOOR };
  }
  return null;
}

const PALETTE = Object.keys(ROLES)
  .filter((token) => TOKENS[token])
  .map((token) => [token, TOKENS[token], ROLES[token]] as const);

const TYPE_SCALE = [
  ["--t-3xl", "49px"],
  ["--t-2xl", "39px"],
  ["--t-xl", "31px"],
  ["--t-l", "25px"],
  ["--t-m", "20px"],
  ["--t-s", "16px"],
  ["--t-xs", "12.8px"],
] as const;

/** Padding plus border on each side of the demo frames below. */
const FRAME_PX = 18;

const STRIP_SIZES = [
  [1140, "Hero, full page width"],
  [700, "Match page column"],
  [308, "340px phone, less the 16px gutters"],
  [120, "Sparkline in a list"],
] as const;

export default function DesignPage() {
  return (
    <main className="theme-paper design">
      <header>
        <h1 style={{ fontSize: "var(--t-2xl)", margin: "0 0 8px" }}>Design reference</h1>
        <p className="prose soft" style={{ margin: 0 }}>
          Session 1 of the UI phase: tokens, type, the ball strip and the texture
          system. Everything here renders from a committed fixture — match 8532,
          in which Sharjah Warriorz chased 135 off the final ball.
        </p>
      </header>

      <Section title="Palette" note="Contrast ratios are computed from the hex values, not asserted.">
        <table className="grid tnum">
          <thead>
            <tr>
              <th>Token</th>
              <th></th>
              <th>Hex</th>
              <th>vs paper</th>
              <th>Floor</th>
              <th>Role</th>
            </tr>
          </thead>
          <tbody>
            {PALETTE.map(([token, hex, role]) => {
              const ratio = contrast(hex, TOKENS["--paper"]);
              const rule = floorFor(token);
              return (
                <tr key={token}>
                  <td style={{ fontFamily: "monospace" }}>{token}</td>
                  <td>
                    <span
                      style={{
                        display: "inline-block",
                        width: 28,
                        height: 18,
                        background: hex,
                        border: "1px solid var(--rule)",
                      }}
                    />
                  </td>
                  <td style={{ fontFamily: "monospace" }}>{hex}</td>
                  <td>{rule ? `${ratio.toFixed(2)}:1` : "—"}</td>
                  <td className={rule && ratio < rule.floor ? "flag" : undefined}>
                    {rule
                      ? `${rule.label} ${rule.floor} · ${ratio >= rule.floor ? "pass" : "FAIL"}`
                      : "exempt"}
                  </td>
                  <td className="soft">{role}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="prose soft" style={{ fontSize: "var(--t-xs)" }}>
          Each token is judged against its own floor, from the registry in{" "}
          <code>lib/tokens.ts</code>: {AA_TEXT}:1 for anything carrying words
          and {FILL_FLOOR}:1 for chart fills — the latter deliberately above
          WCAG 1.4.11&rsquo;s 3:1, because a fill at 3:1 satisfies the standard
          and flattens the texture ladder below. <code>--bowl</code> at 3.46:1 is a
          correct fill and an AA failure for text, which is why{" "}
          <code>--bowl-text</code> exists — that split was this table&rsquo;s
          first real catch. <code>--paper</code> and <code>--rule</code> are
          exempt and listed anyway, so the exemption is a visible decision
          rather than an omission. <code>lib/tokens.test.ts</code> asserts all
          of this against the same stylesheet, and fails if a token is added
          without being classified.
        </p>
      </Section>

      <Section title="Type" note="IBM Plex Sans for prose, Condensed for scoreboard figures.">
        {TYPE_SCALE.map(([token, size]) => (
          <div key={token} style={{ borderBottom: "1px solid var(--rule)", padding: "6px 0" }}>
            <span className="soft tnum" style={{ fontSize: "var(--t-xs)", marginRight: 12 }}>
              {token} · {size}
            </span>
            <span style={{ fontSize: `var(${token})` }}>Sharjah need 1 off 1</span>
          </div>
        ))}

        <h3 style={{ fontSize: "var(--t-m)", marginTop: 24 }}>Scoreboard figures</h3>
        <p className="fig" style={{ fontSize: "var(--t-3xl)", margin: "4px 0" }}>
          134/6 · 92%
        </p>

        <h3 style={{ fontSize: "var(--t-m)", marginTop: 24 }}>Tabular figures</h3>
        <p className="prose soft" style={{ fontSize: "var(--t-xs)" }}>
          §1.5 makes these load-bearing: a win probability that jitters
          horizontally as digits change width reads as broken. Checked against
          the woff2 files in the build rather than by eye — both faces ship
          uniform digit advances (Plex Sans 600 units, Condensed SemiBold 540),
          so figures are tabular by default and neither face carries a{" "}
          <code>tnum</code> feature, because there is nothing to switch away
          from. The <code>tabular-nums</code> declaration is kept anyway: it is
          a no-op on Plex but real work on the fallback stack, and the swap
          window is exactly when a jittering figure would show. The rows below
          are the visual confirmation.
        </p>
        <div className="fig" style={{ fontSize: "var(--t-l)", lineHeight: 1.1 }}>
          {["0000000000", "1111111111", "8888888888", "1234567890"].map((row) => (
            <div key={row}>{row}</div>
          ))}
        </div>
      </Section>

      <Section
        title="Texture"
        note="Texture carries how well evidenced. Colour carries who. The two never swap roles."
      >
        <TextureLegend
          basis="match phase"
          hue="var(--bat)"
          labels={{
            solid: "Death overs — most evidenced",
            hatch: "Powerplay — least evidenced",
            hollow: "Below the sample threshold",
          }}
          omitted="Not measured — reason stated in place"
        />

        <h3 style={{ fontSize: "var(--t-m)", marginTop: 24 }}>Where hatch stops working</h3>
        <p className="prose soft" style={{ fontSize: "var(--t-xs)" }}>
          A {HATCH_PITCH_PX}px diagonal needs roughly {HATCH_MIN_PX}px in its
          minor dimension to show enough repeats to read as a texture. Below
          that it is a stray line, and by 4px it is indistinguishable from a
          solid. This is why the strip carries confidence on its baseline
          instead of in its marks.
        </p>
        <HatchRamp />
      </Section>

      <Section
        title="The ball strip"
        note={`${marks.length} deliveries, ${marks.filter((m) => m.event === "wicket").length} wickets. Height is the swing that ball caused; above the axis helped the batting side.`}
      >
        {STRIP_SIZES.map(([width, label]) => {
          // The demo frame costs 8px of padding and 1px of border a side, so
          // the strip is narrower than the size it is labelled with. The pitch
          // quoted is the one actually rendered - a design page that reported
          // a pitch the component did not use would be worse than useless.
          const inner = width - FRAME_PX;
          const pitch = pitchFor(inner, marks.length);
          const tier = tierFor(pitch);
          const interactive = tier !== "area";
          return (
            <div key={width} style={{ marginBottom: 32 }}>
              <p className="soft tnum" style={{ fontSize: "var(--t-xs)", margin: "0 0 6px" }}>
                {width}px frame, {inner}px strip · {pitch.toFixed(2)}px per mark · tier{" "}
                <strong>{tier}</strong> · {tierHasFill(tier) ? "fill" : "no fill"} ·{" "}
                {!interactive
                  ? "no interaction"
                  : tierHasPerMarkHit(tier)
                    ? "one mark per pointer"
                    : "nearest-mark scrub"}{" "}
                · {label}
              </p>
              <div style={{ width, maxWidth: "100%", border: "1px solid var(--rule)", padding: 8 }}>
                <BallStrip
                  marks={marks}
                  defaultWidth={inner}
                  height={width < 200 ? 28 : 64}
                  interactive={interactive}
                />
              </div>
            </div>
          );
        })}

        <h3 style={{ fontSize: "var(--t-m)" }}>An ODI length, at a phone width</h3>
        <p className="prose soft" style={{ fontSize: "var(--t-xs)" }}>
          Synthetic, not a model output — a seeded walk over 301 deliveries,
          which is what an ODI second innings runs to and what the T20 fixture
          cannot reach. It is here only to show the <code>area</code> tier at a
          length where discrete marks would be{" "}
          {pitchFor(308 - FRAME_PX, odiMarks.length).toFixed(2)}px wide, which is
          below one device pixel on a 1x display.
        </p>
        <div style={{ width: 308, maxWidth: "100%", border: "1px solid var(--rule)", padding: 8 }}>
          <BallStrip marks={odiMarks} defaultWidth={308 - FRAME_PX} height={64} interactive={false} />
        </div>
      </Section>

      <Section title="What the strip does not know">
        <p className="prose">
          The final mark of every innings is a dashed hairline rather than a
          ball. Ball events are not stored for the browser — <code>deliveries</code>{" "}
          is empty on Supabase by design — so each delivery&rsquo;s outcome is
          reconstructed by comparing it with the prediction that follows it.
          Nothing follows the last one. In this fixture that ball is the winning
          run, which makes the most dramatic delivery of the match the one the
          strip declines to describe.
        </p>
      </Section>
    </main>
  );
}

function Section({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section style={{ marginTop: 48 }}>
      <h2 style={{ fontSize: "var(--t-xl)", margin: "0 0 4px" }}>{title}</h2>
      {note ? (
        <p className="prose soft" style={{ margin: "0 0 16px", fontSize: "var(--t-xs)" }}>
          {note}
        </p>
      ) : null}
      <hr className="hr" style={{ marginBottom: 16 }} />
      {children}
    </section>
  );
}

/** The same hatch at shrinking heights, so the floor is visible rather than argued. */
function HatchRamp() {
  return (
    <div style={{ display: "flex", gap: 24, alignItems: "flex-end", flexWrap: "wrap" }}>
      {[32, 24, 12, 8, 4].map((size) => (
        <figure key={size} style={{ margin: 0 }}>
          <svg width={72} height={size} aria-hidden="true">
            <defs>
              <pattern
                id={`ramp-${size}`}
                width={HATCH_PITCH_PX}
                height={HATCH_PITCH_PX}
                patternUnits="userSpaceOnUse"
                patternTransform="rotate(45)"
              >
                <rect width={HATCH_PITCH_PX / 2} height={HATCH_PITCH_PX} fill="var(--bat)" />
              </pattern>
            </defs>
            <rect width={72} height={size} fill={`url(#ramp-${size})`} />
          </svg>
          <figcaption className="soft tnum" style={{ fontSize: "var(--t-xs)", marginTop: 4 }}>
            {size}px{size < HATCH_MIN_PX ? " — below floor" : ""}
          </figcaption>
        </figure>
      ))}
    </div>
  );
}
