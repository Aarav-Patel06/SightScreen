/**
 * jsdom implements no layout, so it has no ResizeObserver - and Recharts'
 * ResponsiveContainer constructs one on mount. Stubbing it lets the page
 * render; the chart itself measures 0x0 and draws nothing, which is fine.
 * These tests assert the honesty rules around the number, not the SVG.
 */

class NoopResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

globalThis.ResizeObserver ??= NoopResizeObserver as unknown as typeof ResizeObserver;
