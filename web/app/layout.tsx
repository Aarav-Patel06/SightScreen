import type { Metadata } from "next";
import type { ReactNode } from "react";
import { IBM_Plex_Sans, IBM_Plex_Sans_Condensed } from "next/font/google";

import { SiteFooter, SiteHeader } from "@/components/site-header";

import "./globals.css";

/**
 * Two faces, one family (UI-PHASE.md §1.5).
 *
 * Plex Sans for prose and UI. Plex Sans Condensed for scoreboard figures -
 * scores, win percentages, over counts - because cricket scoreboards are
 * condensed for the reason this app needs them to be: they hold a lot of
 * numbers in a fixed width.
 *
 * Exposed as CSS variables rather than applied to <body>, so nothing renders
 * differently until a page opts in by taking .theme-paper. The six pages that
 * predate this phase keep the system stack in globals.css until each is
 * retrofitted.
 *
 * `display: swap` over the default `optional`: these are self-hosted and
 * preloaded, so the swap window almost never opens, and when it does a brief
 * fallback beats silently rendering nothing.
 *
 * ON TABULAR FIGURES. §1.5 requires that every number which can change while
 * you watch it be tabular. Checked against the built woff2 rather than
 * assumed: both faces give all ten digits the same advance (600 units in Plex
 * Sans, 540 in Condensed SemiBold), so they are tabular by default and neither
 * exposes a `tnum` feature - there is nothing to switch away from. The
 * `font-variant-numeric: tabular-nums` in globals.css is therefore a no-op on
 * Plex and is kept for the fallback stack, where system-ui and Segoe UI are
 * proportional and the swap window is exactly when a figure would jitter.
 */
const plex = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
  variable: "--font-plex",
});

const plexCondensed = IBM_Plex_Sans_Condensed({
  subsets: ["latin"],
  weight: ["600"],
  display: "swap",
  variable: "--font-plex-cond",
});

export const metadata: Metadata = {
  title: "SightScreen",
  description: "Live cricket win probability, with its own track record on display",
  /*
   * BOTH ICON LINKS, DECLARED EXPLICITLY (UI-PHASE-2 section 4.1).
   *
   * The file convention alone does not do what the brief assumes. With
   * app/icon.svg and app/icon.png both present, Next builds routes for both
   * and then emits exactly ONE <link rel="icon">, choosing the PNG - verified
   * by removing the PNG and rebuilding, at which point the SVG link appears.
   * So "SVG for modern browsers, PNG as fallback" silently became "PNG only,
   * and an SVG nothing references".
   *
   * Declared here instead, SVG first so a browser that understands it takes
   * it and stays sharp on a hi-dpi tab, with the 32px PNG behind it for
   * anything that does not.
   */
  icons: {
    icon: [
      { url: "/icon.svg", type: "image/svg+xml" },
      { url: "/icon.png", type: "image/png", sizes: "32x32" },
    ],
    apple: [{ url: "/apple-icon.png", sizes: "180x180" }],
  },
};

/**
 * `.theme-paper` moves to <body> here, and this is the commit where the whole
 * app changes colour.
 *
 * Session 1 scoped the light palette to a class so /design could use it
 * without touching the six pages that predate this phase. Those pages now
 * inherit it. They are legible but not yet designed - `.panel`, `.chip` and
 * `.wp-bar` still carry dark-palette assumptions and are retrofitted in
 * sessions 3 and 5 - so there is a deliberate in-between state for two
 * sessions. The dark `:root` block stays in globals.css until the last
 * reference to it goes.
 *
 * The skip link is first in the DOM and visible only on focus: the header
 * carries a live slot and a nav, and a keyboard visitor should not have to
 * pass them on every page.
 */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${plex.variable} ${plexCondensed.variable}`}>
      <body className="theme-paper">
        <a href="#main" className="skip-link">
          Skip to content
        </a>
        <SiteHeader />
        <div id="main">{children}</div>
        <SiteFooter />
      </body>
    </html>
  );
}
