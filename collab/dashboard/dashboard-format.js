/**
 * Pure formatting and routing helpers for the Collaborative Lock Dashboard.
 * Loaded in the browser (global DashboardFormat) and in Jest (module.exports).
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.DashboardFormat = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function formatDateLong(dt) {
    return dt.toLocaleDateString([], {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  }

  function formatTime24(dt) {
    return dt.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  }

  function formatDateTime24(dt) {
    return formatDateLong(dt) + " " + formatTime24(dt);
  }

  function formatDurationMinutes(totalMinutes) {
    const rounded = Math.max(0, Math.round(Number(totalMinutes) || 0));
    if (!Number.isFinite(rounded) || rounded <= 0) {
      return "0m";
    }

    const units = [
      { label: "mo", minutes: 30 * 24 * 60 },
      { label: "d", minutes: 24 * 60 },
      { label: "h", minutes: 60 },
      { label: "m", minutes: 1 },
    ];

    let remaining = rounded;
    const parts = [];

    units.forEach((unit) => {
      if (remaining >= unit.minutes) {
        const value = Math.floor(remaining / unit.minutes);
        remaining -= value * unit.minutes;
        parts.push(String(value) + unit.label);
      }
    });

    return parts.length ? parts.join(" ") : "0m";
  }

  function routeFromHash(hashLike) {
    const h = String(hashLike || "")
      .replace("#", "")
      .toLowerCase();
    return h === "history" ? "history" : "locks";
  }

  return {
    formatDateLong,
    formatTime24,
    formatDateTime24,
    formatDurationMinutes,
    routeFromHash,
  };
});
