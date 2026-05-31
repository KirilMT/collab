const {
  formatDateTime24,
  formatDurationMinutes,
  routeFromHash,
} = require("../../../collab/dashboard/dashboard-format");

describe("dashboard-format", () => {
  test("formatDurationMinutes composes compound units", () => {
    expect(formatDurationMinutes(0)).toBe("0m");
    expect(formatDurationMinutes(45)).toBe("45m");
    expect(formatDurationMinutes(90)).toBe("1h 30m");
    expect(formatDurationMinutes(24 * 60)).toBe("1d");
  });

  test("formatDateTime24 uses 24h clock", () => {
    const dt = new Date("2026-01-15T14:05:00.000Z");
    const formatted = formatDateTime24(dt);
    expect(formatted).toContain("January");
    expect(formatted).toMatch(/\d{2}:\d{2}$/);
  });

  test("routeFromHash resolves history vs locks", () => {
    expect(routeFromHash("#history")).toBe("history");
    expect(routeFromHash("history")).toBe("history");
    expect(routeFromHash("#locks")).toBe("locks");
    expect(routeFromHash("")).toBe("locks");
  });
});
