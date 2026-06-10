/**
 * Chart helpers for the Collaborative Lock Dashboard.
 * Loaded in the browser (global DashboardCharts) and in Jest (module.exports).
 *
 * Uses Chart.js (loaded via CDN) for lock activity timeline visualization.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.DashboardCharts = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  // ---------------------------------------------------------------------------
  // Constants
  // ---------------------------------------------------------------------------

  var CHART_COLORS = {
    acquisitions: "rgba(79, 70, 229, 0.75)", // primary
    acquisitionsBorder: "rgba(79, 70, 229, 1)",
    releases: "rgba(34, 197, 94, 0.75)", // green
    releasesBorder: "rgba(34, 197, 94, 1)",
    grid: "rgba(226, 232, 240, 0.6)",
    text: "#64748b",
  };

  /**
   * Compute bucket label and boundaries for a time range.
   *
   * @param {string} range  "1h" | "24h" | "7d"
   * @returns {{ bucketMs: number, bucketLabel: string, totalBuckets: number }}
   */
  function _bucketConfig(range) {
    switch (range) {
      case "1h":
        return { bucketMs: 5 * 60 * 1000, bucketLabel: "5m", totalBuckets: 12 };
      case "7d":
        return {
          bucketMs: 24 * 60 * 60 * 1000,
          bucketLabel: "1d",
          totalBuckets: 7,
        };
      case "24h":
      default:
        return {
          bucketMs: 60 * 60 * 1000,
          bucketLabel: "1h",
          totalBuckets: 24,
        };
    }
  }

  /**
   * Bucket history rows into time-series counts.
   *
   * @param {Array<object>} history  Array of lock history rows.
   * @param {string}        range    "1h" | "24h" | "7d"
   * @returns {{ labels: Array<string>, acquired: Array<number>, released: Array<number> }}
   */
  function buildTimelineData(history, range) {
    var cfg = _bucketConfig(range);
    var now = Date.now();
    var start = now - cfg.totalBuckets * cfg.bucketMs;

    // Initialize buckets
    var labels = [];
    var acquired = [];
    var released = [];
    for (var i = 0; i < cfg.totalBuckets; i++) {
      var bucketTime = start + i * cfg.bucketMs;
      labels.push(_formatBucketLabel(bucketTime, range));
      acquired.push(0);
      released.push(0);
    }

    // Fill buckets from history data
    (history || []).forEach(function (row) {
      if (row.acquired_at) {
        var acqTs = new Date(row.acquired_at).getTime();
        var acqIdx = Math.floor((acqTs - start) / cfg.bucketMs);
        if (acqIdx >= 0 && acqIdx < cfg.totalBuckets) {
          acquired[acqIdx]++;
        }
      }
      if (row.released_at) {
        var relTs = new Date(row.released_at).getTime();
        var relIdx = Math.floor((relTs - start) / cfg.bucketMs);
        if (relIdx >= 0 && relIdx < cfg.totalBuckets) {
          released[relIdx]++;
        }
      }
    });

    return { labels: labels, acquired: acquired, released: released };
  }

  /**
   * Format a bucket timestamp into a human-readable label.
   */
  function _formatBucketLabel(ts, range) {
    var d = new Date(ts);
    switch (range) {
      case "1h":
        return (
          String(d.getHours()).padStart(2, "0") +
          ":" +
          String(d.getMinutes()).padStart(2, "0")
        );
      case "7d":
        return d.toLocaleDateString([], { month: "short", day: "numeric" });
      case "24h":
      default:
        return String(d.getHours()).padStart(2, "0") + ":00";
    }
  }

  // ---------------------------------------------------------------------------
  // Chart initialization
  // ---------------------------------------------------------------------------

  /**
   * Create or return an existing Chart.js bar chart for lock activity.
   *
   * @param {string} canvasId  ID of the <canvas> element.
   * @returns {object|null}    Chart.js instance, or null if Chart.js unavailable.
   */
  function initActivityChart(canvasId) {
    if (typeof document === "undefined") return null;

    var ChartCtor = (typeof window !== "undefined" && window.Chart) || null;
    if (!ChartCtor) return null;

    var canvas = document.getElementById(canvasId);
    if (!canvas) return null;

    // Destroy existing chart if re-initializing
    var existing = ChartCtor.getChart(canvas);
    if (existing) existing.destroy();

    return new ChartCtor(canvas, {
      type: "bar",
      data: {
        labels: [],
        datasets: [
          {
            label: "Acquired",
            data: [],
            backgroundColor: CHART_COLORS.acquisitions,
            borderColor: CHART_COLORS.acquisitionsBorder,
            borderWidth: 1,
            borderRadius: 4,
          },
          {
            label: "Released",
            data: [],
            backgroundColor: CHART_COLORS.releases,
            borderColor: CHART_COLORS.releasesBorder,
            borderWidth: 1,
            borderRadius: 4,
          },
        ],
      },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: "index",
          intersect: false,
        },
        hover: {
          mode: "index",
          intersect: false,
        },
        plugins: {
          legend: {
            labels: {
              usePointStyle: true,
              padding: 8,
              color: CHART_COLORS.text,
              font: { size: 12, weight: "600" },
            },
          },
          tooltip: {
            backgroundColor: "#1e293b",
            titleFont: { size: 13, weight: "700" },
            bodyFont: { size: 12 },
            padding: 10,
            cornerRadius: 8,
          },
        },
        scales: {
          x: {
            grid: { color: CHART_COLORS.grid },
            ticks: {
              color: CHART_COLORS.text,
              font: { size: 11 },
              maxRotation: 45,
            },
          },
          y: {
            beginAtZero: true,
            grid: { color: CHART_COLORS.grid },
            ticks: {
              color: CHART_COLORS.text,
              font: { size: 11 },
              stepSize: 1,
            },
            title: {
              display: true,
              text: "Lock Events",
              color: CHART_COLORS.text,
              font: { size: 11, weight: "600" },
            },
          },
        },
      },
    });
  }

  /**
   * Update an existing Chart.js instance with new timeline data.
   *
   * @param {object}        chart    Chart.js instance.
   * @param {Array<object>} history  Array of lock history rows.
   * @param {string}        range    "1h" | "24h" | "7d"
   */
  function updateActivityChart(chart, history, range) {
    if (!chart) return;

    var data = buildTimelineData(history, range);
    chart.data.labels = data.labels;
    chart.data.datasets[0].data = data.acquired;
    chart.data.datasets[1].data = data.released;
    chart.update("none"); // no animation on data update for performance
  }

  // ---------------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------------

  return {
    initActivityChart: initActivityChart,
    updateActivityChart: updateActivityChart,
    buildTimelineData: buildTimelineData,
  };
});
