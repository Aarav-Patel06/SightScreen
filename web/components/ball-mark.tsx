/**
 * The simplified mark, inline (UI-PHASE-2 section 4.1).
 *
 * Inline rather than <img src="/icon.svg">: the header renders on every page,
 * and a mark that can 404 or arrive after first paint is a worse header than
 * one with no mark. Inline SVG also inherits nothing from the cascade, which
 * matters on --chrome where almost no colour is legible.
 *
 * THE GEOMETRY IS DUPLICATED FROM app/icon.svg, AND A TEST ASSERTS THEY
 * MATCH. Duplication is the lesser evil here - importing SVG as a component
 * needs @svgr/webpack, which is a build dependency for one 600-character
 * string - but silent divergence between the favicon and the header mark is
 * exactly the kind of drift this project keeps finding, so
 * components/ball-mark.test.ts reads both files and compares.
 */

/** Sampled from public/logo.png, not from the CSS tokens. See app/icon.svg. */
export const BALL_FILL = "#98374B";
export const BALL_SEAM = "#F7F6E9";
export const BALL_SEAM_PATH =
  "M 23.04 2.76 L 22.81 2.67 L 22.55 2.64 L 22.26 2.67 L 21.95 2.75 L 21.60 2.89 L 21.24 3.09 L 20.85 3.34 L 20.44 3.65 L 20.02 4.01 L 19.57 4.42 L 19.11 4.88 L 18.64 5.39 L 18.15 5.94 L 17.66 6.54 L 17.16 7.18 L 16.65 7.85 L 16.15 8.56 L 15.64 9.31 L 15.13 10.08 L 14.62 10.87 L 14.13 11.69 L 13.64 12.53 L 13.16 13.38 L 12.69 14.24 L 12.24 15.11 L 11.80 15.98 L 11.38 16.86 L 10.98 17.73 L 10.60 18.59 L 10.25 19.44 L 9.92 20.28 L 9.61 21.10 L 9.33 21.89 L 9.09 22.67 L 8.87 23.41 L 8.68 24.12 L 8.52 24.80 L 8.40 25.44 L 8.31 26.03 L 8.25 26.59 L 8.22 27.10 L 8.23 27.56 L 8.27 27.98 L 8.34 28.34 L 8.45 28.65 L 8.59 28.90 L 8.76 29.10 L 8.96 29.24";

export function BallMark({ size = 24 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      // Decorative: the wordmark beside it already says "SightScreen", and a
      // screen reader announcing "SightScreen logo SightScreen" is noise.
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="16" cy="16" r="15" fill={BALL_FILL} />
      <path
        d={BALL_SEAM_PATH}
        fill="none"
        stroke={BALL_SEAM}
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}
