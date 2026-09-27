/**
 * An in-memory stand-in for supabase-js's query builder, for tests only.
 *
 * It exists for one behaviour the chain-shaped mocks elsewhere cannot express:
 * PostgREST returns AT MOST `MAX_ROWS` rows per request, silently. No error, no
 * flag - the response is simply shorter than the result. Supabase's default is
 * 1000, and on 2026-09-26 it made the landing hero pick a July match over a
 * September one, because the rows proving the September match existed were
 * row 1001 onwards of an unbounded fetch.
 *
 * Supports the subset this codebase uses: select (with count/head), eq, in,
 * not(col, "is", null), order, limit, range, maybeSingle. Filters are applied,
 * then order, then range/limit, then the cap - the order PostgREST applies
 * them in.
 */

export const MAX_ROWS = 1000;

type Row = Record<string, unknown>;
type Tables = Record<string, Row[]>;

export interface FakeRequest {
  table: string;
  filters: string[];
}

export function fakeSupabase(tables: Tables, requests: FakeRequest[] = []) {
  return {
    from(table: string) {
      return new Query(table, tables[table] ?? [], requests);
    },
  };
}

class Query implements PromiseLike<{ data: unknown; error: null; count: number | null }> {
  private predicates: ((row: Row) => boolean)[] = [];
  private described: string[] = [];
  private sorts: { column: string; ascending: boolean }[] = [];
  private window: { from: number; to: number } | null = null;
  private cap: number | null = null;
  private head = false;
  private counted = false;
  private single = false;

  constructor(
    private table: string,
    private rows: Row[],
    private requests: FakeRequest[]
  ) {}

  select(_columns = "*", options: { count?: string; head?: boolean } = {}) {
    this.counted = options.count === "exact";
    this.head = options.head === true;
    return this;
  }

  eq(column: string, value: unknown) {
    this.described.push(`${column}=eq.${String(value)}`);
    this.predicates.push((row) => row[column] === value);
    return this;
  }

  in(column: string, values: readonly unknown[]) {
    this.described.push(`${column}=in.(${values.length})`);
    const set = new Set(values);
    this.predicates.push((row) => set.has(row[column]));
    return this;
  }

  not(column: string, operator: string, value: unknown) {
    if (operator !== "is" || value !== null) throw new Error(`fake: unsupported not(${operator})`);
    this.described.push(`${column}=not.is.null`);
    this.predicates.push((row) => row[column] !== null && row[column] !== undefined);
    return this;
  }

  order(column: string, options: { ascending?: boolean } = {}) {
    this.sorts.push({ column, ascending: options.ascending ?? true });
    return this;
  }

  limit(n: number) {
    this.cap = n;
    return this;
  }

  range(from: number, to: number) {
    this.window = { from, to };
    return this;
  }

  maybeSingle() {
    this.single = true;
    return this;
  }

  then<A, B>(
    onFulfilled?: ((value: { data: unknown; error: null; count: number | null }) => A | PromiseLike<A>) | null,
    onRejected?: ((reason: unknown) => B | PromiseLike<B>) | null
  ): PromiseLike<A | B> {
    return Promise.resolve(this.run()).then(onFulfilled, onRejected);
  }

  private run() {
    this.requests.push({ table: this.table, filters: this.described });
    let out = this.rows.filter((row) => this.predicates.every((p) => p(row)));
    const count = this.counted ? out.length : null;
    if (this.head) return { data: null, error: null, count };
    if (this.sorts.length) {
      // Successive order() calls are successive sort keys, as in PostgREST.
      out = [...out].sort((a, b) => {
        for (const { column, ascending } of this.sorts) {
          const x = a[column] as number | string;
          const y = b[column] as number | string;
          if (x !== y) return (x < y ? -1 : 1) * (ascending ? 1 : -1);
        }
        return 0;
      });
    }
    if (this.window) out = out.slice(this.window.from, this.window.to + 1);
    if (this.cap !== null) out = out.slice(0, this.cap);
    out = out.slice(0, MAX_ROWS);
    if (this.single) return { data: out[0] ?? null, error: null, count };
    return { data: out, error: null, count };
  }
}
