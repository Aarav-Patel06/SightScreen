/**
 * The shared shell (UI-PHASE.md §3.1).
 *
 * Wordmark left, navigation right, nothing else. No search, no theme toggle,
 * no call to action.
 *
 * THE NAV CARRIES ONLY WHAT EXISTS. §3.1 lists four items — Matches, Players,
 * Ask, Accuracy — and each joined this list in the commit that gave it a
 * page: Matches in session 3, Players in session 4. A link that 404s, or a
 * dimmed item with no stated reason, is the thing §0.2 forbids: a gap hidden
 * behind something that looks functional.
 */

import Link from "next/link";

import { ActiveNav } from "@/components/active-nav";

import { BallMark } from "@/components/ball-mark";
import { LiveSlot } from "@/components/live-slot";

/** Complete as of session 4: §3.1's four items all have pages. */
const NAV = [
  { href: "/matches", label: "Matches" },
  { href: "/players", label: "Players" },
  { href: "/ask", label: "Ask" },
  { href: "/accuracy", label: "Accuracy" },
];

export function SiteHeader() {
  return (
    <header className="site-header">
      <div className="site-header-inner">
        {/*
          Mark plus wordmark (UI-PHASE-2 section 4.2). The wordmark is INK, not
          the teal/crimson split from the logo, and that is a measurement
          rather than a preference: the header sits on --chrome, where teal is
          3.25:1 and crimson 3.78:1. Both fail 4.5:1 for text.

          The split is not lost - it moves to the landing page, where the
          wordmark sits on --paper and both halves clear comfortably (teal
          5.14:1, crimson 5.99:1). The alternative considered was a
          paper-coloured band behind the wordmark inside the chrome header,
          which would make the split legal; it was rejected because a cream
          rectangle floating in a tan bar reads as a badge, and inventing a
          container to make a colour legal is how decoration gets in.
        */}
        <Link href="/" className="wordmark">
          <BallMark size={22} />
          <span>SightScreen</span>
        </Link>

        <LiveSlot />

        {/* The active item is marked with aria-current, not only with colour
            (step 6 item 6). ActiveNav is a client component purely because
            usePathname needs one; the links themselves are unchanged. */}
        <ActiveNav items={NAV} />
      </div>
    </header>
  );
}

/**
 * The GitHub mark. Inline rather than an <img>, for the same reason the ball
 * mark is: the footer renders on every page and a logo that can 404 is worse
 * than no logo. `currentColor` so it inherits the footer's ink and cannot
 * drift into an unclassified colour.
 */
function GitHubMark() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path
        fill="currentColor"
        d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38
           0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13
           -.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66
           .07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15
           -.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27
           c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15
           0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2
           0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"
      />
    </svg>
  );
}

export function SiteFooter() {
  return (
    <footer className="site-footer">
      {/* Three sections, pipe-separated, per the reference. The mark sits
          before the link rather than beside the text, so it reads as part of
          the link target rather than as decoration. */}
      <p>
        <a href="https://github.com/Aarav-Patel06/SightScreen" className="footer-source">
          <GitHubMark />
          Source on GitHub
        </a>
        <span className="footer-sep" aria-hidden="true">|</span>
        Ball-by-ball data from <a href="https://cricsheet.org">Cricsheet</a>, used under its{" "}
        <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a> licence.
        <span className="footer-sep" aria-hidden="true">|</span>
        Analytics, not betting advice.
      </p>
    </footer>
  );
}
