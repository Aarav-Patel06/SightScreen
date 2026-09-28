/**
 * Whether this site's agent can reach the ball-by-ball corpus.
 *
 * Only the owner's development server can. The corpus lives in local
 * Postgres, and hosting a copy for production is deferred on cost (SPEC.md
 * section 0a, "production has no corpus"): there, three of the agent's five
 * tools answer 503. When a hosted corpus exists, this becomes true for
 * production too, and /ask's beta line and hidden examples follow it.
 */
export function corpusOnThisSite(nodeEnv: string | undefined = process.env.NODE_ENV): boolean {
  return nodeEnv === "development";
}
