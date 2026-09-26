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
