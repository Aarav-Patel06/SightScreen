/**
 * The two corner marks (UI-PHASE-2 step 6 item 5).
 *
 * Decorative and `aria-hidden`: they carry no information, and a screen
 * reader announcing "cricket ball" before the page content would be noise.
 *
 * Drawn from the tan already in the palette via `currentColor`, which the
 * stylesheet sets to --chrome — so they cannot drift into a colour nobody
 * classified, and lib/tokens.test.ts's "no token nobody decided about" check
 * still means something.
 *
 * They never sit under text. That is enforced by the media query in
 * globals.css, not by opacity: below 1280px they are `display: none`, because
 * `main` is capped at --column and only a wide viewport has margins to put
 * them in.
 */

export function CornerGraphics() {
  return (
    <>
      <svg
        className="corner-graphic corner-ball"
        viewBox="0 0 200 200"
        aria-hidden="true"
        focusable="false"
      >
        <circle cx="60" cy="150" r="88" fill="currentColor" />
        {/* The seam, as on the favicon: a tilted arc, not a straight line. */}
        <path
          d="M 6 96 A 22 88 0 0 0 78 232"
          fill="none"
          stroke="var(--paper)"
          strokeWidth="9"
          strokeLinecap="round"
          opacity="0.7"
        />
      </svg>

      <svg
        className="corner-graphic corner-batsman"
        viewBox="0 0 200 200"
        aria-hidden="true"
        focusable="false"
      >
        {/* A batsman in the follow-through, reduced to the silhouette that
            survives at this size: bat, arms, body, front leg. */}
        <g fill="currentColor">
          <circle cx="104" cy="58" r="17" />
          <path d="M96 78 h22 l10 44 -14 6 -10 -30 z" />
          <path d="M118 84 l44 -40 12 12 -44 42 z" />
          <path d="M162 40 l16 -16 10 10 -16 16 z" />
          <path d="M100 126 l26 -4 6 42 -14 4 -10 -26 z" />
          <path d="M104 168 l14 -4 14 36 -16 4 z" />
          <path d="M84 132 l20 -6 -6 46 -18 -2 z" />
        </g>
      </svg>
    </>
  );
}
