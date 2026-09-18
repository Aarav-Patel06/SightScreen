/**
 * The front door. Not in §12.1 - which describes the match page and nothing
 * else - but a deployed app whose root is a 404 is the first thing anyone
 * opening the Vercel URL would see, so it lists what is there.
 *
 * Server-rendered from `matches`, which on Supabase holds only live and
 * recent matches (§2.1); the historical corpus stays local. An empty list
 * here is therefore the normal state between demos, not a fault.
 */

import Link from "next/link";

import { supabaseServer } from "@/lib/supabase-server";

export const dynamic = "force-dynamic";

export default async function Home() {
  const supabase = supabaseServer();
  const { data } = await supabase
    .from("matches")
    .select("match_id, competition, format, start_time, status")
    .order("start_time", { ascending: false })
    .limit(10);

  return (
    <main>
      <div className="panel">
        <h1>SightScreen</h1>
        <p className="small muted">
          Live cricket win probability, with its own track record on display.
          Second innings only.
        </p>
      </div>

      <div className="panel">
        <h2>Matches on the serving database</h2>
        {data && data.length > 0 ? (
          <ul className="small">
            {data.map((match) => (
              <li key={match.match_id}>
                <Link href={`/match/${match.match_id}`}>
                  {match.competition} · {match.format} · {match.start_time.slice(0, 10)}
                </Link>{" "}
                <span className="muted">({match.status})</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="small muted">
            None right now. Supabase holds only live and recent matches; the
            historical corpus stays on the training machine (SPEC.md section 2.1).
          </p>
        )}
      </div>

      <p className="tiny muted">
        <Link href="/about/model">How good is this model?</Link> · analytics, not
        betting advice.
      </p>
    </main>
  );
}
