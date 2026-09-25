/**
 * The ball strip (UI-PHASE.md §1.2) - the signature element.
 *
 * An innings as a horizontal run of discrete marks, one per delivery. Height
 * is the absolute win-probability swing that ball caused, direction is which
 * side it helped, and fill is what happened. Read at a glance the strip is the
 * match narrative: a flat run of dots, a spike where a wicket fell, a widening
 * band at the death.
 *
 * THE TIER IS MEASURED, NOT PASSED. §1.2 describes "the same component at four
 * sizes". It is not quite that - it is the same data at four sizes with a
 * declared encoding ladder, because the encodings do not all survive the
 * smallest one. At a 340px viewport a 125-delivery innings gets 2.5px per
 * mark, where a hollow mark's two 1px strokes overlap and it renders solid.
 * So the component measures its own width and drops encodings below their
 * legibility floor, rather than accepting a `size` prop that can be passed
 * wrongly. lib/ball-strip.ts holds the floors and the reasoning.
 *
 * WHAT EACH TIER DRAWS:
 *
 *   full     >=6px   height, direction, fill, wickets, pointing at one mark
 *   reduced  >=4px   height, direction, fill, wickets, nearest-mark scrub
 *   minimal  >=2px   height, direction, wickets as hairlines. No fill.
 *   area     <2px    a filled swing envelope. Not marks - a shape.
 *
 * The phase baseline under the strip carries confidence at every tier, because
 * it costs no mark width: dotted under the powerplay, dashed through the
 * middle, solid at the death. That is the §1.3 signal the marks themselves
 * cannot carry, and its basis is named in the legend rather than implied.
 */

"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import {
  describeStrip,
  peakSwing,
  pitchFor,
  tierFor,
  tierHasFill,
  type Mark,
  type Tier,
  describeEvent,
} from "@/lib/ball-strip";
import type { Phase } from "@/lib/prediction";

/** Dash pattern per phase, ordered so that "more dashes" reads as less certain. */
const PHASE_DASH: Record<Phase, string | undefined> = {
  powerplay: "1 3",
  middle: "6 3",
  death: undefined,
};

const PHASE_LABEL: Record<Phase, string> = {
  powerplay: "powerplay, lowest confidence",
  middle: "middle overs",
  death: "death overs, highest confidence",
};

export interface BallStripProps {
  marks: readonly Mark[];
  /** Strip height in px, excluding the baseline. */
  height?: number;
  /**
   * Width assumed for the server render, before the ResizeObserver reports.
   * Height is fixed either way, so a wrong guess corrects without layout shift.
   */
  defaultWidth?: number;
  /** Set false for the sparkline case, where a readout has nowhere to go. */
  interactive?: boolean;
  /**
   * Hide from assistive technology entirely.
   *
   * The text alternative describes the innings - 235 characters of "starting
   * at 79% and ending at 92%, 6 wickets, 48 dot balls". That is the right
   * thing when the strip IS the content, on the hero and the match page.
   * In a table cell it is noise: /matches renders 102 of them beside rows
   * that already state the date, the teams and the result as text, which
   * added ~24,300 characters to that page's accessibility tree for
   * information already present. A screen reader would hear a paragraph per
   * row in the column whose whole purpose is to be glanceable.
   *
   * So the caller says which case it is. Decorative is never the default:
   * a strip that nobody told about is described.
   */
  decorative?: boolean;
  /**
   * Whose win probability the strip shows, for the readout's second line.
   *
   * Optional: the hero and the match page know the batting side, the /design
   * page and the small strips do not. Without it the line reads "Win
   * probability", which is true but less useful - never a guessed name.
   *
   * teams.short_name is NULL for all 347 rows, so this is a full name rather
   * than an abbreviation.
   */
  battingTeam?: string;
  /**
   * Draw the strip left to right once on load (§1.7). The landing hero only -
   * it is the page's single piece of non-user-triggered motion, and a second
   * one anywhere would make it ordinary.
   */
  draw?: boolean;
  className?: string;
}

export function BallStrip({
  marks,
  height = 64,
  defaultWidth = 640,
  interactive = true,
  draw = false,
  decorative = false,
  battingTeam,
  className,
}: BallStripProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(defaultWidth);
  const [active, setActive] = useState<number | null>(null);

  // Scoped per instance: clipPath ids share one document-wide namespace, and
  // the landing page renders the hero alongside the header's slot.
  const clipId = `strip-clip-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const observer = new ResizeObserver(([entry]) => {
      const next = entry.contentRect.width;
      if (next > 0) setWidth(next);
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  const pitch = pitchFor(width, marks.length);
  const tier = tierFor(pitch);
  const peak = peakSwing(marks);
  const axis = height / 2;
  const half = axis - 1;

  const description = useMemo(() => describeStrip(marks), [marks]);

  /**
   * Nearest mark to a pointer x. Used at every tier that interacts, rather
   * than per-mark hit areas: §1.2 asks for hovering a mark, but the phase is
   * mobile-first, touch has no hover, and a 2.5px target against a 44px
   * minimum is off by a factor of seventeen. One handler across the whole
   * strip resolving to the nearest mark works at 2.5px and at 9px alike.
   */
  const scrub = useCallback(
    (clientX: number) => {
      const host = hostRef.current;
      if (!host || marks.length === 0) return;
      const { left, width: hostWidth } = host.getBoundingClientRect();
      const ratio = (clientX - left) / hostWidth;
      const index = Math.floor(ratio * marks.length);
      setActive(Math.min(marks.length - 1, Math.max(0, index)));
    },
    [marks.length]
  );

  const canInteract = interactive && tier !== "area" && marks.length > 0;

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (!canInteract) return;
      const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      if (step === 0) return;
      event.preventDefault();
      setActive((current) => {
        const next = (current ?? -1) + step;
        return Math.min(marks.length - 1, Math.max(0, next));
      });
    },
    [canInteract, marks.length]
  );

  if (marks.length === 0) {
    // §0.2: say so where the thing would have been, rather than rendering an
    // empty chart that looks like a loading state.
    return (
      <div className={className}>
        <hr className="hr" />
        <p className="soft" style={{ fontSize: "var(--t-xs)", margin: "6px 0 0" }}>
          No predictions for this innings.
        </p>
      </div>
    );
  }

  const activeMark = active === null ? null : marks[active];

  return (
    <div className={className}>
      <div
        ref={hostRef}
        role={decorative ? undefined : "img"}
        aria-label={decorative ? undefined : description}
        aria-hidden={decorative ? true : undefined}
        tabIndex={canInteract ? 0 : -1}
        onKeyDown={onKeyDown}
        onPointerMove={canInteract ? (e) => scrub(e.clientX) : undefined}
        onPointerLeave={canInteract ? () => setActive(null) : undefined}
        style={{ width: "100%", touchAction: "pan-y" }}
      >
        <svg
          width="100%"
          height={height + 6}
          viewBox={`0 0 ${width} ${height + 6}`}
          preserveAspectRatio="none"
          style={{ display: "block", overflow: "visible" }}
        >
          {/* The axis. Marks sit above it if the ball helped the batting side,
              below if it helped the bowling side.

              Outside the clip deliberately: the baseline is the page's
              promise that a strip is coming, so it is drawn in full while the
              marks sweep across it. */}
          <line
            x1={0}
            y1={axis}
            x2={width}
            y2={axis}
            stroke="var(--rule)"
            strokeWidth={1}
            vectorEffect="non-scaling-stroke"
          />

          {/* A clip that sweeps across, rather than 125 individually delayed
              marks. One animation, no library, and the mark geometry is
              untouched - so every tier draws the same way and nothing can
              animate into a different layout.

              DRIVEN BY CSS, NOT STATE, AND THAT IS THE WHOLE POINT. The
              obvious version starts the clip at width 0 and opens it on
              mount, which puts `width="0"` in the server-rendered HTML - so
              the hero is invisible until hydration, and permanently invisible
              without JavaScript. For the one element on the site that must
              always render, that is the wrong failure. Here the rect is
              always full width and CSS scales it from zero, so the finished
              strip is what the HTML says, the animation is decoration on top,
              and `prefers-reduced-motion` is a media query rather than a
              branch nobody runs. */}
          <defs>
            <clipPath id={clipId}>
              <rect
                x={0}
                y={-4}
                height={height + 14}
                width={width}
                className={draw ? "strip-draw" : undefined}
              />
            </clipPath>
          </defs>

          <g clipPath={`url(#${clipId})`}>
            {tier === "area" ? (
              <AreaTier marks={marks} width={width} axis={axis} half={half} peak={peak} />
            ) : (
              <MarkTier
                marks={marks}
                pitch={pitch}
                axis={axis}
                half={half}
                peak={peak}
                height={height}
                tier={tier}
                active={active}
              />
            )}

            <PhaseBaseline marks={marks} pitch={pitch} width={width} y={height + 3} tier={tier} />
          </g>

          {activeMark ? (
            <line
              x1={(activeMark.index + 0.5) * pitch}
              y1={0}
              x2={(activeMark.index + 0.5) * pitch}
              y2={height}
              stroke="var(--ink)"
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
              pointerEvents="none"
            />
          ) : null}
        </svg>
      </div>

      {canInteract ? <Readout mark={activeMark} peak={peak} battingTeam={battingTeam} /> : null}
    </div>
  );
}

/** Discrete marks: full, reduced and minimal. */
function MarkTier({
  marks,
  pitch,
  axis,
  half,
  peak,
  height,
  tier,
  active,
}: {
  marks: readonly Mark[];
  pitch: number;
  axis: number;
  half: number;
  peak: number;
  height: number;
  tier: Tier;
  active: number | null;
}) {
  const withFill = tierHasFill(tier);
  // A 1px gap only once there is room for one. Below 3px the gap would eat
  // more of the mark than it separates.
  const gap = pitch >= 3 ? 1 : 0;
  const barWidth = Math.max(0.5, pitch - gap);

  return (
    <g>
      {marks.map((mark) => {
        const x = mark.index * pitch + gap / 2;
        const centre = x + barWidth / 2;

        // The final delivery has no successor, so its outcome was never
        // recorded. A dashed full-height hairline says that in place, at every
        // tier, rather than drawing a zero-height mark that disappears.
        if (mark.event === "unknown") {
          return (
            <line
              key={mark.index}
              x1={centre}
              y1={0}
              x2={centre}
              y2={height}
              stroke="var(--ink-soft)"
              strokeWidth={1}
              strokeDasharray="2 2"
              vectorEffect="non-scaling-stroke"
            />
          );
        }

        const magnitude = peak > 0 ? Math.abs(mark.swing) / peak : 0;
        const barHeight = Math.max(0.5, magnitude * half);
        const up = mark.swing >= 0;
        const y = up ? axis - barHeight : axis;
        const hue = mark.event === "wicket" ? "var(--flag)" : up ? "var(--bat)" : "var(--bowl)";

        // Hollow means dot ball here. §1.3 also uses hollow for a
        // below-threshold sample, but that state belongs to the player page,
        // where there are no dot balls - the two never meet on one surface,
        // and each surface's legend names which it means.
        const hollow = withFill && mark.event === "dot";

        return (
          <g key={mark.index}>
            {/* A wicket is a full-height hairline as well as a coloured mark.
                The hairline is what survives at `minimal`, where fill does
                not, so wickets stay findable at 2.5px per ball. */}
            {mark.event === "wicket" ? (
              <line
                x1={centre}
                y1={0}
                x2={centre}
                y2={height}
                stroke="var(--flag)"
                strokeWidth={1}
                vectorEffect="non-scaling-stroke"
              />
            ) : null}
            <rect
              x={x}
              y={y}
              width={barWidth}
              height={barHeight}
              fill={hollow ? "none" : hue}
              stroke={hollow ? hue : "none"}
              strokeWidth={hollow ? 1 : 0}
              vectorEffect={hollow ? "non-scaling-stroke" : undefined}
              opacity={active === null || active === mark.index ? 1 : 0.45}
            />
          </g>
        );
      })}
    </g>
  );
}

/**
 * Below 2px a mark is sub-pixel, so drawing marks would be a lie about the
 * rendering. The honest object is the swing envelope: two filled areas, one
 * per side, which keeps the semantic identical to the other tiers.
 */
function AreaTier({
  marks,
  width,
  axis,
  half,
  peak,
}: {
  marks: readonly Mark[];
  width: number;
  axis: number;
  half: number;
  peak: number;
}) {
  const step = width / marks.length;

  function path(side: 1 | -1): string {
    const points = marks.map((mark, index) => {
      const signed = side === 1 ? Math.max(0, mark.swing) : Math.min(0, mark.swing);
      const magnitude = peak > 0 ? Math.abs(signed) / peak : 0;
      const x = (index + 0.5) * step;
      const y = axis - side * magnitude * half;
      return `${x.toFixed(2)} ${y.toFixed(2)}`;
    });
    return `M 0 ${axis} L ${points.join(" L ")} L ${width} ${axis} Z`;
  }

  return (
    <g>
      <path d={path(1)} fill="var(--bat)" />
      <path d={path(-1)} fill="var(--bowl)" />
    </g>
  );
}

/**
 * Confidence by match phase, on the baseline (§1.3).
 *
 * The marks cannot carry it - a hatch needs about 24px in its minor dimension
 * to read as a texture, and a mark is 2.5px wide at a phone width. The
 * baseline has the full strip length to work with and costs no mark width, so
 * the signal survives at every tier including `area`.
 */
function PhaseBaseline({
  marks,
  pitch,
  width,
  y,
  tier,
}: {
  marks: readonly Mark[];
  pitch: number;
  width: number;
  y: number;
  tier: Tier;
}) {
  const step = tier === "area" ? width / marks.length : pitch;
  const runs: Array<{ phase: Phase; from: number; to: number }> = [];

  for (const mark of marks) {
    const last = runs[runs.length - 1];
    if (last && last.phase === mark.phase) last.to = mark.index + 1;
    else runs.push({ phase: mark.phase, from: mark.index, to: mark.index + 1 });
  }

  return (
    <g>
      {runs.map((run) => (
        <line
          key={`${run.phase}-${run.from}`}
          x1={run.from * step}
          y1={y}
          x2={run.to * step}
          y2={y}
          stroke="var(--ink-soft)"
          strokeWidth={2}
          strokeDasharray={PHASE_DASH[run.phase]}
          vectorEffect="non-scaling-stroke"
        >
          <title>{PHASE_LABEL[run.phase]}</title>
        </line>
      ))}
    </g>
  );
}

/**
 * The detail for the mark under the pointer.
 *
 * Reserves its line whether or not a mark is active, so scrubbing does not
 * reflow the page under the pointer.
 */
function Readout({
  mark,
  peak,
  battingTeam,
}: {
  mark: Mark | null;
  peak: number;
  battingTeam?: string;
}) {
  /*
   * TWO LINES, and the split is by question rather than by length.
   *
   * Line one is the match state: where the score is, what is still needed,
   * and what this ball did. Line two is the model: what it thought, and how
   * far this ball moved it. A reader scrubbing the strip is asking one or the
   * other, and interleaving them on one line made both harder to find.
   *
   * The block reserves both lines whether or not a mark is active, so
   * scrubbing never reflows the page under the pointer.
   */
  const subject = battingTeam ? `${battingTeam} win probability` : "Win probability";

  return (
    <div
      className="soft tnum strip-readout"
      style={{ fontSize: "var(--t-xs)", margin: "6px 0 0", minHeight: "2.8em" }}
      aria-live="polite"
    >
      {mark === null ? (
        <>
          <span>Largest swing {Math.round(peak * 100)} percentage points</span>
          <span>Hover to see ball-by-ball</span>
        </>
      ) : mark.event === "unknown" ? (
        <>
          <span>
            {mark.score}/{mark.wickets} · outcome not recorded
          </span>
          <span>No prediction follows this ball, so what it did is not in the data</span>
        </>
      ) : (
        <>
          <span>
            {mark.score}/{mark.wickets}
            {mark.runsRequired > 0 ? (
              <> · {mark.runsRequired} required off {mark.ballsRemaining}</>
            ) : null}{" "}
            · {describeEvent(mark)}
          </span>
          <span>
            {subject} {Math.round(mark.p * 100)}% · moved {mark.swing >= 0 ? "+" : "−"}
            {Math.abs(Math.round(mark.swing * 1000) / 10)} percentage points
          </span>
        </>
      )}
    </div>
  );
}
