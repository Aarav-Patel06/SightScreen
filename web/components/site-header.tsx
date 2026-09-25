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

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <p>
        <a href="https://github.com/Aarav-Patel06/SightScreen">Source on GitHub</a>. Ball-by-ball data
        from <a href="https://cricsheet.org">Cricsheet</a>, used under its{" "}
        <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a> licence. Analytics, not
        betting advice.
      </p>
    </footer>
  );
}
