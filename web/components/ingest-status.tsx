/**
 * When the daily Cricsheet ingest last succeeded, from `pipeline_runs`.
 *
 * The job can stop without failing - GitHub disables a scheduled workflow in
 * a public repository after 60 days without activity, and then nothing runs
 * and nothing turns red - so this line is where that absence shows. Older
 * than STALE_AFTER_MS, or never, and it is flagged in --flag and announced
 * (role="alert"), in words rather than only by an old date.
 *
 * Two days, not one: Cricsheet releases irregularly and the job is daily, so a
 * single missed or late run is normal; two is not.
 */

const STALE_AFTER_MS = 2 * 24 * 60 * 60 * 1000;

export function ingestIsStale(lastSucceededAt: string | null, now: number): boolean {
  if (lastSucceededAt === null) return true;
  const age = now - Date.parse(lastSucceededAt);
  // NaN is stale: a timestamp nobody can read proves nothing ran.
  return !(age <= STALE_AFTER_MS);
}

export function IngestStatus({
  lastSucceededAt,
  now,
}: {
  lastSucceededAt: string | null;
  now: number;
}) {
  if (lastSucceededAt === null) {
    return (
      <p className="tiny flag" role="alert">
        Cricsheet ingest has not succeeded yet - new matches are not being added.
      </p>
    );
  }
  const when = `${lastSucceededAt.slice(0, 16).replace("T", " ")} UTC`;
  if (ingestIsStale(lastSucceededAt, now)) {
    return (
      <p className="tiny flag" role="alert">
        Cricsheet ingest last succeeded {when} - more than 2 days ago. New matches
        are not being added; the daily job may be failing or disabled.
      </p>
    );
  }
  return <p className="tiny muted">Cricsheet ingest last succeeded {when}.</p>;
}
