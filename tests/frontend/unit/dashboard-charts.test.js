const {
  buildTimelineData,
} = require("../../../collab/dashboard/dashboard-charts");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeHistoryRow(acquiredISO, releasedISO) {
  return {
    file_path: "src/main.py",
    developer_id: "alice",
    acquired_at: acquiredISO || null,
    released_at: releasedISO || null,
  };
}

// ---------------------------------------------------------------------------
// buildTimelineData
// ---------------------------------------------------------------------------

describe("buildTimelineData", function () {
  test("returns empty buckets for empty history", function () {
    var result = buildTimelineData([], "24h");
    expect(result.labels).toHaveLength(24);
    expect(result.acquired).toHaveLength(24);
    expect(result.released).toHaveLength(24);
    expect(
      result.acquired.every(function (v) {
        return v === 0;
      }),
    ).toBe(true);
  });

  test("24h range produces 24 hourly buckets", function () {
    var result = buildTimelineData([], "24h");
    expect(result.labels).toHaveLength(24);
    // Each label should end with ":00" (hourly)
    result.labels.forEach(function (label) {
      expect(label).toMatch(/^\d{2}:00$/);
    });
  });

  test("1h range produces 12 5-minute buckets", function () {
    var result = buildTimelineData([], "1h");
    expect(result.labels).toHaveLength(12);
    // Each label should be HH:MM format
    result.labels.forEach(function (label) {
      expect(label).toMatch(/^\d{2}:\d{2}$/);
    });
  });

  test("7d range produces 7 daily buckets", function () {
    var result = buildTimelineData([], "7d");
    expect(result.labels).toHaveLength(7);
    // Each label should look like a month-day combo (e.g. "Jun 8" or "1 Jun")
    result.labels.forEach(function (label) {
      expect(label).toMatch(/[A-Z][a-z]{2}/); // contains a 3-letter month
    });
  });

  test("counts acquisitions in correct bucket", function () {
    var now = new Date();
    // Create a lock acquired 1 hour ago (should be in bucket index 23 of 24h)
    var oneHourAgo = new Date(now.getTime() - 60 * 60 * 1000).toISOString();
    var history = [makeHistoryRow(oneHourAgo, null)];
    var result = buildTimelineData(history, "24h");

    // The last few buckets should have the acquisition
    var totalAcquired = result.acquired.reduce(function (a, b) {
      return a + b;
    }, 0);
    expect(totalAcquired).toBe(1);
  });

  test("counts releases in correct bucket", function () {
    var now = new Date();
    var oneHourAgo = new Date(now.getTime() - 60 * 60 * 1000).toISOString();
    var history = [makeHistoryRow(null, oneHourAgo)];
    var result = buildTimelineData(history, "24h");

    var totalReleased = result.released.reduce(function (a, b) {
      return a + b;
    }, 0);
    expect(totalReleased).toBe(1);
  });

  test("ignores rows outside the time window", function () {
    // Row from 2 days ago should be outside a 24h window
    var twoDaysAgo = new Date(
      Date.now() - 2 * 24 * 60 * 60 * 1000,
    ).toISOString();
    var history = [makeHistoryRow(twoDaysAgo, null)];
    var result = buildTimelineData(history, "24h");

    var totalAcquired = result.acquired.reduce(function (a, b) {
      return a + b;
    }, 0);
    expect(totalAcquired).toBe(0);
  });

  test("handles missing timestamps gracefully", function () {
    var history = [makeHistoryRow(null, null)];
    var result = buildTimelineData(history, "24h");

    var totalAcquired = result.acquired.reduce(function (a, b) {
      return a + b;
    }, 0);
    expect(totalAcquired).toBe(0);
  });

  test("null history is treated as empty", function () {
    var result = buildTimelineData(null, "24h");
    expect(result.labels).toHaveLength(24);
    expect(
      result.acquired.every(function (v) {
        return v === 0;
      }),
    ).toBe(true);
  });

  test("undefined history is treated as empty", function () {
    var result = buildTimelineData(undefined, "7d");
    expect(result.labels).toHaveLength(7);
  });

  test("multiple events in same bucket", function () {
    var now = new Date();
    var recent = new Date(now.getTime() - 30 * 60 * 1000).toISOString(); // 30 min ago
    var alsoRecent = new Date(now.getTime() - 45 * 60 * 1000).toISOString(); // 45 min ago
    var history = [
      makeHistoryRow(recent, null),
      makeHistoryRow(alsoRecent, alsoRecent),
    ];
    var result = buildTimelineData(history, "24h");

    var totalAcquired = result.acquired.reduce(function (a, b) {
      return a + b;
    }, 0);
    var totalReleased = result.released.reduce(function (a, b) {
      return a + b;
    }, 0);
    expect(totalAcquired).toBe(2);
    expect(totalReleased).toBe(1);
  });
});
