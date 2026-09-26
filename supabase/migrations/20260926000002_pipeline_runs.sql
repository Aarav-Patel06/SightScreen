-- pipeline_runs: one row per scheduled-pipeline run, success or failure.
--
-- WHY. GitHub disables scheduled workflows in a public repository after 60
-- days without repository activity, and a disabled workflow does not fail -
-- it simply stops. Nothing red appears anywhere. The only signal is absence,
-- so absence has to be visible: /accuracy reads the latest successful row
-- and says when it was, flagged once it is more than two days old.
--
-- Written by the job itself at the end of every run (ingest/daily_cricsheet
-- main), best effort: a run that cannot reach Supabase cannot record that it
-- failed, which is exactly the case the staleness flag exists to catch.
--
-- counts holds the job's public summary - counts and timings only, the same
-- object its public log prints. error_class is the exception's class name,
-- never its message: the repository and its logs are public, and so, via the
-- page, is this table's latest row.

CREATE TABLE IF NOT EXISTS pipeline_runs (
  run_id       BIGSERIAL   PRIMARY KEY,
  pipeline     TEXT        NOT NULL,          -- 'cricsheet_daily'
  started_at   TIMESTAMPTZ NOT NULL,
  finished_at  TIMESTAMPTZ NOT NULL,
  status       TEXT        NOT NULL CHECK (status IN ('success', 'failure')),
  counts       JSONB       NOT NULL DEFAULT '{}',
  error_class  TEXT
);
CREATE INDEX IF NOT EXISTS pipeline_runs_latest
  ON pipeline_runs (pipeline, status, finished_at DESC);

-- Read server-side with the secret key only (20260918000001's pattern A).
REVOKE ALL ON TABLE public.pipeline_runs FROM anon, authenticated;
ALTER TABLE public.pipeline_runs ENABLE ROW LEVEL SECURITY;
-- No policy. RLS with no policy denies everything, which is the intent.
