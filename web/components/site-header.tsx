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
        <Link href="/" className="wordmark">
          SightScreen
        </Link>

        <LiveSlot />

        <nav aria-label="Main">
          <ul>
            {NAV.map((item) => (
              <li key={item.href}>
                <Link href={item.href}>{item.label}</Link>
              </li>
            ))}
          </ul>
        </nav>
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
