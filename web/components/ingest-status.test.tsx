/**
 * The daily Cricsheet job can stop without failing: GitHub disables a
 * scheduled workflow in a public repository after 60 days without activity,
 * and then nothing runs and nothing turns red. /accuracy's ingest line is the
 * only place that absence shows, so a stale last success must be flagged -
 * visibly, and in words - not just printed as an old date.
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { IngestStatus } from "./ingest-status";

afterEach(cleanup);

const NOW = Date.parse("2026-09-26T12:00:00Z");

describe("IngestStatus", () => {
  it("states the last success plainly when it is recent", () => {
    render(<IngestStatus lastSucceededAt="2026-09-26T02:06:00Z" now={NOW} />);
    expect(screen.getByText(/Cricsheet ingest last succeeded 2026-09-26/)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("flags a last success older than two days", () => {
    render(<IngestStatus lastSucceededAt="2026-09-23T02:06:00Z" now={NOW} />);
    const warning = screen.getByRole("alert");
    expect(warning.textContent).toMatch(/Cricsheet ingest last succeeded 2026-09-23/);
    expect(warning.textContent).toMatch(/more than 2 days ago/);
    expect(warning.className).toMatch(/\bflag\b/);
  });

  it("flags exactly-two-days-and-a-minute, not only whole days", () => {
    render(<IngestStatus lastSucceededAt="2026-09-24T11:59:00Z" now={NOW} />);
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("does not flag a success just inside two days", () => {
    render(<IngestStatus lastSucceededAt="2026-09-24T12:01:00Z" now={NOW} />);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("flags a pipeline that has never succeeded", () => {
    render(<IngestStatus lastSucceededAt={null} now={NOW} />);
    expect(screen.getByRole("alert").textContent).toMatch(/has not succeeded yet/);
  });
});

describe("IngestStatus when the last run rejected matches", () => {
  // A rejected match is one the resolver would not guess at - a near-miss team
  // name - and it is re-rejected daily until a person resolves it. The only
  // place that shows is here, so it is said as plainly as a stale run.
  it("flags the count beside a recent last success", () => {
    render(<IngestStatus lastSucceededAt="2026-09-26T02:06:00Z" rejected={3} now={NOW} />);
    const warning = screen.getByRole("alert");
    expect(warning.textContent).toMatch(/3 matches rejected in the last run/);
    expect(warning.className).toMatch(/\bflag\b/);
    // The last-succeeded line is still stated.
    expect(screen.getByText(/Cricsheet ingest last succeeded 2026-09-26/)).toBeTruthy();
  });

  it("uses the singular for one", () => {
    render(<IngestStatus lastSucceededAt="2026-09-26T02:06:00Z" rejected={1} now={NOW} />);
    expect(screen.getByRole("alert").textContent).toMatch(/1 match rejected in the last run/);
  });

  it("says nothing extra when nothing was rejected", () => {
    render(<IngestStatus lastSucceededAt="2026-09-26T02:06:00Z" rejected={0} now={NOW} />);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
