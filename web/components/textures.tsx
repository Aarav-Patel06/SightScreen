/**
 * Uncertainty as texture, not colour (UI-PHASE.md §1.3).
 *
 * Colour carries who - which team, which side of a comparison. Texture
 * carries how well evidenced. The payoff is that a pre-toss prediction and a
 * 19th-over prediction are visibly different objects at the same percentage,
 * and that the confidence signal survives greyscale and every common
 * colour-vision deficiency, because it is not a hue.
 *
 * TWO CORRECTIONS TO §1.3, both load-bearing.
 *
 * 1. TEXTURE ENCODES EVIDENCE CLASS, NOT INTERVAL WIDTH. §1.3's table names
 *    "wide interval" and "sample below threshold" as the states. For win
 *    probability neither exists: predictions.payload carries nine keys and
 *    none of them is a confidence, an interval, or a sample size (see
 *    lib/prediction.ts). The only available signal is `phase`. Hatching a
 *    pre-toss prediction and letting it read as an interval would be exactly
 *    what SPEC.md §12.2's 2026-09-18 clarification forbids - "an interval
 *    derived from an aggregate metric is not uncertainty, it is decoration
 *    that looks principled" - and what §6 of this phase restates.
 *
 *    So every surface names its own basis, and TextureLegend requires it as a
 *    prop. A surface cannot ship texture without saying what the texture is
 *    derived from. That is §6 rule 2 enforced by the type checker rather than
 *    by review. The bases in use: match phase on the strip and match page,
 *    sample size on the player page, bootstrapped 95% CI on accuracy.
 *
 * 2. HATCH IS A LARGE-AREA TREATMENT, AT A 10px PITCH, NOT 8px. Two reasons.
 *    At 8px, 50% browser zoom gives a 2px stripe against a 2px gap, which
 *    mushes into a flat tint on a 1x display; 10px holds. And a diagonal
 *    hatch of any pitch needs roughly 24px in its minor dimension to show
 *    enough repeats to read as a texture rather than as a stray line - inside
 *    a 2.5px strip mark it shows one stripe or none depending where the mark
 *    happens to fall. Hatch is therefore available on bands, table cells,
 *    legend swatches and the hero, and on nothing in the strip at any tier.
 *    lib/ball-strip.ts carries the corresponding floors.
 */

"use client";

import { useId } from "react";

// Imported, not declared. These are read by /design, which is a Server
// Component, and a constant exported from a "use client" module reaches one as
// an opaque reference rather than a number - see lib/ball-strip.ts.
import { HATCH_MIN_PX, HATCH_PITCH_PX } from "@/lib/ball-strip";

/**
 * NEVER PLACE HATCH AND HOLLOW ADJACENT WITHOUT A THIRD CUE.
 *
 * Measured greyscale separation between the three treatments: solid to hollow
 * 4.77:1, solid to hatch 3.05:1, and hatch to hollow **1.56:1**. That last
 * pair clears the rough 1.3:1 floor for telling two flat tints apart, but only
 * just, and the margin is carried almost entirely by hollow's 1px outline -
 * which is the first thing someone removes when tidying a component.
 *
 * Put a label, a gap, or an ordering between them, or use only one of the two
 * on a given surface. The legend below is safe because every swatch is
 * labelled; a bare row of swatches would not be.
 */
export type Texture = "solid" | "hatch" | "hollow";

/**
 * Pattern definitions for one SVG.
 *
 * Ids are scoped with useId() rather than being global constants. SVG pattern
 * ids live in a document-wide namespace, so two strips on one page with the
 * same id silently share whichever definition rendered last - and /design
 * renders six strips at once, which is precisely where that would first bite.
 *
 * Returns both the <defs> element and the fill values that reference it, so a
 * caller cannot construct a url(#...) that does not match.
 */
export function useTexture(hue: string) {
  const id = useId().replace(/[^a-zA-Z0-9]/g, "");
  const hatchId = `hatch-${id}`;

  const defs = (
    <defs>
      <pattern
        id={hatchId}
        width={HATCH_PITCH_PX}
        height={HATCH_PITCH_PX}
        patternUnits="userSpaceOnUse"
        patternTransform="rotate(45)"
      >
        {/* Half coverage. In greyscale this reads as a lighter solid, which is
            why nothing in this system also uses a reduced-opacity solid - the
            two would collide and the collision would be invisible in colour. */}
        <rect width={HATCH_PITCH_PX / 2} height={HATCH_PITCH_PX} fill={hue} />
      </pattern>
    </defs>
  );

  function fillFor(texture: Texture): string {
    if (texture === "solid") return hue;
    if (texture === "hatch") return `url(#${hatchId})`;
    return "none";
  }

  return { defs, fillFor };
}

/**
 * The legend. `basis` is required: see correction 1 above.
 *
 * `omitted` is the fourth state in §1.3's table - not measured, rendered as an
 * empty slot with a hairline and a reason. It takes the reason as a string for
 * the same rationale as `basis`: the honest-gap principle (§0.2) says to state
 * why in plain language where the thing would have been, and a component that
 * accepts a blank reason invites a blank.
 */
export function TextureLegend({
  basis,
  hue,
  labels,
  omitted,
}: {
  basis: string;
  hue: string;
  labels: { solid: string; hatch?: string; hollow?: string };
  omitted?: string;
}) {
  const { defs, fillFor } = useTexture(hue);

  const entries: Array<[Texture, string]> = [["solid", labels.solid]];
  if (labels.hatch) entries.push(["hatch", labels.hatch]);
  if (labels.hollow) entries.push(["hollow", labels.hollow]);

  return (
    <div className="tex-legend">
      <p className="soft" style={{ fontSize: "var(--t-xs)", margin: "0 0 6px" }}>
        Confidence shown as texture, from {basis}.
      </p>
      <ul className="tex-legend-list">
        {entries.map(([texture, label]) => (
          <li key={texture}>
            <svg width={HATCH_MIN_PX} height={HATCH_MIN_PX} aria-hidden="true">
              {defs}
              <rect
                x={0.5}
                y={0.5}
                width={HATCH_MIN_PX - 1}
                height={HATCH_MIN_PX - 1}
                fill={fillFor(texture)}
                stroke={hue}
                strokeWidth={1}
              />
            </svg>
            <span>{label}</span>
          </li>
        ))}
        {omitted ? (
          <li>
            <svg width={HATCH_MIN_PX} height={HATCH_MIN_PX} aria-hidden="true">
              <line
                x1={0}
                y1={HATCH_MIN_PX - 0.5}
                x2={HATCH_MIN_PX}
                y2={HATCH_MIN_PX - 0.5}
                stroke="var(--rule)"
                strokeWidth={1}
              />
            </svg>
            <span className="soft">{omitted}</span>
          </li>
        ) : null}
      </ul>
    </div>
  );
}
