"use client";

/**
 * The main navigation, with the current page marked.
 *
 * A client component only because `usePathname` requires one. The links, the
 * labels and their order all still live in components/site-header.tsx — this
 * adds `aria-current` and nothing else.
 *
 * `aria-current="page"` rather than a class: the crimson underline is a
 * visual cue, and a screen reader needs to be told which item is current by
 * something other than a border colour. The stylesheet hangs the underline
 * off the same attribute, so the two cannot drift apart.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

export interface NavItem {
  href: string;
  label: string;
}

/** `/matches` is current on `/matches` and on `/match/8429`. */
export function isCurrent(pathname: string | null, href: string): boolean {
  if (!pathname) return false;
  if (pathname === href) return true;
  if (href === "/matches") return pathname.startsWith("/match/");
  if (href === "/players") return pathname.startsWith("/player/");
  return false;
}

export function ActiveNav({ items }: { items: readonly NavItem[] }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Main">
      <ul>
        {items.map((item) => (
          <li key={item.href}>
            <Link
              href={item.href}
              aria-current={isCurrent(pathname, item.href) ? "page" : undefined}
            >
              {item.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
