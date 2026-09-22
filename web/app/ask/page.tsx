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
    <main style={{ maxWidth: "48rem", margin: "0 auto", padding: "2rem 1rem" }}>
      <h1>Ask</h1>
      <p>
        Questions answered from 3.78 million deliveries of ball-by-ball data and the
        model&rsquo;s own outputs. Every number comes with the sample size behind it; where
        the data cannot answer something, the agent says so rather than estimating.
      </p>

      {!signedIn ? (
        <form onSubmit={signIn} style={{ margin: "2rem 0" }}>
          <label htmlFor="password" style={{ display: "block", marginBottom: ".5rem" }}>
            This demo is password-protected, because it calls a paid API.
          </label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            style={{ padding: ".5rem", width: "18rem" }}
          />
          <button type="submit" disabled={busy || !password} style={{ marginLeft: ".5rem", padding: ".5rem 1rem" }}>
            {busy ? "Checking…" : "Enter"}
          </button>
        </form>
      ) : (
        <form onSubmit={ask} style={{ margin: "2rem 0" }}>
          <input
            aria-label="Your question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="How does Virat Kohli fare against Mitchell Starc?"
            style={{ padding: ".5rem", width: "100%" }}
          />
          <button type="submit" disabled={busy || !question.trim()} style={{ marginTop: ".5rem", padding: ".5rem 1rem" }}>
            {busy ? "Thinking…" : "Ask"}
          </button>
        </form>
      )}

      {notice ? <p role="status">{notice}</p> : null}

      {history.map((item, index) => (
        <section key={index} style={{ borderTop: "1px solid #ddd", padding: "1rem 0" }}>
          <p style={{ fontWeight: 600 }}>{item.question}</p>
          {item.answer ? (
            <p style={{ whiteSpace: "pre-wrap" }}>{item.answer}</p>
          ) : item.note ? (
            <p role="status">{item.note}</p>
          ) : (
            <p aria-live="polite">Working…</p>
          )}
        </section>
      ))}
    </main>
  );
}
