/**
 * The agent loop (SPEC.md §10.1a).
 *
 * The loop lives here in TypeScript and tool EXECUTION lives in FastAPI,
 * because tools need the models, the feature code and sqlglot. The SQL guard
 * is never reimplemented here: two validators means two chances to disagree,
 * and the one that disagrees is the vulnerability.
 *
 * The prompt and the tool definitions are IMPORTED, not written here. They
 * are generated from api/src/agent_eval/{prompt,tools}.py, carry a
 * fingerprint, and CI fails if the committed copies drift from the Python -
 * so the deployed agent is the one the eval measured, or the build is red.
 *
 * Three gates stand in front of the model call, cheapest first:
 *
 *   1. the signed session cookie          (no I/O)
 *   2. the per-session message count      (no I/O, in the cookie)
 *   3. the daily cost cap                 (one Supabase read)
 *
 * Ordered that way on purpose: a visitor with no cookie costs one HMAC, not
 * a database round trip.
 */

import Anthropic from "@anthropic-ai/sdk";
import { NextResponse } from "next/server";

import { AGENT_TOOLS } from "@/lib/agent-tools";
import { SYSTEM_PROMPT } from "@/lib/agent-prompt";
import {
  capMessage,
  isOverDailyCap,
  recordUsage,
  type TokenUsage,
} from "@/lib/agent-usage";
import {
  ASK_COOKIE,
  cookieOptions,
  incrementCookie,
  refusalMessage,
  verifyCookie,
} from "@/lib/ask-gate";

export const runtime = "nodejs";

const MODEL = "claude-sonnet-5";
const MAX_TURNS = 16;
const MAX_TOKENS = 4096;

const ENDPOINTS: Record<string, string> = {
  resolve_entity: "/agent/resolve_entity",
  query_ball_data: "/agent/query_ball_data",
  get_matchup: "/agent/get_matchup",
  get_player_form: "/agent/get_player_form",
  get_live_prediction: "/agent/get_live_prediction",
};

type Turn = { role: "user" | "assistant"; content: unknown };

function addUsage(total: TokenUsage, usage: TokenUsage): TokenUsage {
  return {
    input_tokens: (total.input_tokens ?? 0) + (usage.input_tokens ?? 0),
    output_tokens: (total.output_tokens ?? 0) + (usage.output_tokens ?? 0),
    cache_read_input_tokens:
      (total.cache_read_input_tokens ?? 0) + (usage.cache_read_input_tokens ?? 0),
    cache_creation_input_tokens:
      (total.cache_creation_input_tokens ?? 0) + (usage.cache_creation_input_tokens ?? 0),
  };
}

export async function POST(request: Request) {
  const secret = process.env.ASK_SESSION_SECRET ?? "";
  const cookie = request.headers
    .get("cookie")
    ?.split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${ASK_COOKIE}=`))
    ?.slice(ASK_COOKIE.length + 1);

  // --- gate 1 and 2: the cookie, and the count inside it ------------------
  const gate = verifyCookie(cookie, secret);
  if (!gate.ok) {
    // 401 for "sign in", 429 for "you have had your turn" - different
    // situations for the CLIENT, which needs to know whether to show the
    // password field. The message never distinguishes tampered from missing.
    const status = gate.reason === "exhausted" ? 429 : 401;
    return NextResponse.json({ error: refusalMessage(gate.reason) }, { status });
  }

  // --- gate 3: the money --------------------------------------------------
  if (await isOverDailyCap()) {
    return NextResponse.json({ error: capMessage() }, { status: 429 });
  }

  const apiKey = process.env.ANTHROPIC_API_KEY;
  const apiBase = process.env.API_BASE_URL;
  const toolSecret = process.env.AGENT_TOOL_SHARED_SECRET;
  if (!apiKey || !apiBase || !toolSecret) {
    console.error("agent route is missing ANTHROPIC_API_KEY, API_BASE_URL or AGENT_TOOL_SHARED_SECRET");
    return NextResponse.json({ error: "The agent is not configured." }, { status: 503 });
  }

  let question = "";
  try {
    const body = await request.json();
    question = typeof body?.question === "string" ? body.question.trim() : "";
  } catch {
    question = "";
  }
  if (!question) {
    return NextResponse.json({ error: "Ask a question first." }, { status: 400 });
  }

  const anthropic = new Anthropic({ apiKey });
  const messages: Turn[] = [{ role: "user", content: question }];
  let usage: TokenUsage = {};
  let answer = "";
  let hitTurnLimit = false;

  try {
    for (let turn = 0; turn < MAX_TURNS; turn += 1) {
      const response = await anthropic.messages.create({
        model: MODEL,
        max_tokens: MAX_TOKENS,
        thinking: { type: "adaptive" },
        // The cache breakpoint sits on the system block, which with the tool
        // definitions is ~1,500 tokens re-sent every turn - about half the
        // uncached cost of a conversation.
        system: [
          { type: "text", text: SYSTEM_PROMPT, cache_control: { type: "ephemeral" } },
        ],
        tools: AGENT_TOOLS as never,
        messages: messages as never,
      });
      usage = addUsage(usage, response.usage as TokenUsage);

      const text = response.content
        .filter((block) => block.type === "text")
        .map((block) => (block as { text: string }).text)
        .join("");
      messages.push({ role: "assistant", content: response.content });

      const calls = response.content.filter((block) => block.type === "tool_use");
      if (calls.length === 0) {
        answer = text;
        hitTurnLimit = false;
        break;
      }
      // Still working: any text so far is commentary between tool calls, not
      // a conclusion. The eval harness learned this the expensive way.
      hitTurnLimit = true;

      const results = await Promise.all(
        calls.map(async (call) => {
          const use = call as { id: string; name: string; input: unknown };
          const path = ENDPOINTS[use.name];
          let payload: unknown = { error: `unknown tool ${use.name}` };
          if (path) {
            try {
              const reply = await fetch(`${apiBase}${path}`, {
                method: "POST",
                headers: {
                  "content-type": "application/json",
                  "X-Agent-Secret": toolSecret,
                },
                body: JSON.stringify(use.input),
              });
              payload = await reply.json();
            } catch (error) {
              payload = { error: "tool unavailable" };
            }
          }
          return {
            type: "tool_result" as const,
            tool_use_id: use.id,
            content: JSON.stringify(payload).slice(0, 20_000),
          };
        })
      );
      // All results in ONE user message: splitting them teaches the model to
      // stop making parallel calls.
      messages.push({ role: "user", content: results });
    }
  } catch (error) {
    console.error("agent loop failed:", error);
    // Spend that did happen is still recorded - the cap must see partial
    // conversations or a crash loop becomes a free one.
    await recordUsage(usage);
    return NextResponse.json(
      { error: "The agent could not finish that question. Try asking it again." },
      { status: 502 }
    );
  }

  await recordUsage(usage);

  if (hitTurnLimit || !answer.trim()) {
    return NextResponse.json(
      {
        error:
          "That question took more steps than the agent is allowed in one go. " +
          "Try asking for one thing at a time.",
      },
      { status: 200 }
    );
  }

  const response = NextResponse.json({ answer });
  response.cookies.set(ASK_COOKIE, incrementCookie(gate.session, secret), cookieOptions());
  return response;
}
