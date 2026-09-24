/**
 * /ask - natural-language questions over the ball-by-ball corpus
 * (SPEC.md §10.1).
 *
 * Deliberately plain. The interesting thing here is the fact base behind the
 * answers, not the chat chrome, and a convincing-looking chat UI over a weak
 * one would be the wrong thing to build well.
 *
 * Two states: signed out (a password field) and signed in (a question box).
 * The password is exchanged server-side for an HttpOnly cookie, so nothing
 * on this page ever holds it after the request, and no script can read the
 * session - see lib/ask-gate.ts for why that shape and not Vercel's
 * deployment protection or a header secret.
 *
 * Every refusal from the API is rendered as prose, because all of them are
 * things a person needs to read: a wrong password, a session that ran out of
 * messages, and a day that ran out of budget are not errors in the sense
 * that something broke.
 */

"use client";

import { useState } from "react";

type Exchange = { question: string; answer?: string; note?: string };

export default function AskPage() {
  const [signedIn, setSignedIn] = useState(false);
  const [password, setPassword] = useState("");
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<Exchange[]>([]);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function signIn(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setNotice("");
    try {
      const response = await fetch("/api/ask/session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (response.ok) {
        setSignedIn(true);
        setPassword("");
      } else {
        const body = await response.json().catch(() => ({}));
        setNotice(body.error ?? "Incorrect password.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function ask(event: React.FormEvent) {
    event.preventDefault();
    const asked = question.trim();
    if (!asked || busy) return;
    setBusy(true);
    setNotice("");
    setQuestion("");
    setHistory((prior) => [...prior, { question: asked }]);
    try {
      const response = await fetch("/api/agent", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: asked }),
      });
      const body = await response.json().catch(() => ({}));
      if (response.status === 401) {
        // The session lapsed or was never valid. Back to the password field
        // rather than a dead-end message.
        setSignedIn(false);
        setNotice(body.error ?? "Enter the password to continue.");
        setHistory((prior) => prior.slice(0, -1));
        return;
      }
      setHistory((prior) =>
        prior.map((item, index) =>
          index === prior.length - 1
            ? { ...item, answer: body.answer, note: body.answer ? undefined : body.error }
            : item
        )
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="ask">
      <h1 className="page-title">Ask</h1>
      <p>
        Questions answered from 3.78 million deliveries of ball-by-ball data and the
        model&rsquo;s own outputs. Every number comes with the sample size behind it; where
        the data cannot answer something, the agent says so rather than estimating.
      </p>

      {!signedIn ? (
        <form onSubmit={signIn} className="ask-form">
          <label htmlFor="password">
            This demo is password-protected, because it calls a paid API.
          </label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            className="ask-input ask-input-password"
          />
          <button type="submit" disabled={busy || !password} className="ask-button">
            {busy ? "Checking…" : "Enter"}
          </button>
        </form>
      ) : (
        <form onSubmit={ask} className="ask-form">
          <input
            aria-label="Your question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="How does Virat Kohli fare against Mitchell Starc?"
            className="ask-input"
          />
          <button type="submit" disabled={busy || !question.trim()} className="ask-button">
            {busy ? "Thinking…" : "Ask"}
          </button>
        </form>
      )}

      {notice ? <p role="status">{notice}</p> : null}

      {/* §4.7 asks for tool calls to render as a collapsed line the visitor
          can expand - "queried 3.78M deliveries" opening to the SQL. It is
          not built, and the reason is in the same paragraph that asks for it:
          §4.7 also says do not modify the route handler, and the route
          returns `{ answer }` and nothing else. The tool calls happen inside
          its agentic loop and never reach the client, so there is no way to
          render them without changing that route - which is a logic change
          wearing a visual change's clothes, and this session's whole
          acceptance criterion is that those are different things.

          Said here rather than silently omitted (§0.2). */}
      {history.length > 0 ? (
        <p className="soft ask-transparency">
          The SQL each answer ran is not shown yet. The agent logs every query
          it makes, but the route returns only the answer, and wiring the two
          together is a change to a proven path rather than a visual one.
        </p>
      ) : null}

      {history.map((item, index) => (
        <section key={index} className="ask-exchange">
          <p className="ask-question">{item.question}</p>
          {item.answer ? (
            <p className="ask-answer">{item.answer}</p>
          ) : item.note ? (
            <p role="status" className="soft">{item.note}</p>
          ) : (
            <p aria-live="polite" className="soft">Working…</p>
          )}
        </section>
      ))}
    </main>
  );
}
