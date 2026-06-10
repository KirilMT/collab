/**
 * DOM-dependent coverage for dashboard-filters.js and dashboard-charts.js.
 *
 * The browser-only helpers in these modules are guarded by
 * `typeof document === "undefined"` and therefore never execute under Jest's
 * default `node` environment. Rather than pull in a jsdom dependency, we inject
 * a minimal fake `document`/`window` that mirrors the tiny DOM surface these
 * functions actually use (getElementById, createElement, value, tagName,
 * innerHTML, appendChild). This exercises every browser-only branch in Node.
 *
 * The UMD "browser global" branch (the `else` arm that assigns to the global
 * object when `module.exports` is unavailable) is functionally verified by
 * loading each file in a fresh vm context where `module` is undefined. Note
 * that vm-loaded source is not Istanbul-instrumented, so that single boilerplate
 * line is exercised for correctness but is not reflected in line coverage.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const filters = require("../../../collab/dashboard/dashboard-filters");
const charts = require("../../../collab/dashboard/dashboard-charts");

// ---------------------------------------------------------------------------
// Fake DOM helpers
// ---------------------------------------------------------------------------

function makeElement(opts) {
  return Object.assign(
    {
      tagName: "INPUT",
      value: "",
      innerHTML: "",
      children: [],
      appendChild(child) {
        this.children.push(child);
      },
    },
    opts || {},
  );
}

function installDocument(elementsById) {
  global.document = {
    getElementById(id) {
      return Object.prototype.hasOwnProperty.call(elementsById, id)
        ? elementsById[id]
        : null;
    },
    createElement(tag) {
      return {
        tagName: String(tag).toUpperCase(),
        value: "",
        textContent: "",
        selected: false,
      };
    },
  };
}

afterEach(function () {
  delete global.document;
  delete global.window;
});

// ---------------------------------------------------------------------------
// getActiveFiltersFromDOM
// ---------------------------------------------------------------------------

describe("getActiveFiltersFromDOM", function () {
  test("returns empty object when document is unavailable", function () {
    expect(filters.getActiveFiltersFromDOM()).toEqual({});
  });

  test("collects every populated filter field", function () {
    installDocument({
      "filter-search": makeElement({ value: "  needle  " }),
      "filter-developer": makeElement({ tagName: "SELECT", value: "alice" }),
      "filter-agent": makeElement({ value: "  bot-7 " }),
      "filter-glob": makeElement({ value: " src/*.py " }),
      "filter-status": makeElement({ tagName: "SELECT", value: "active" }),
      "filter-role": makeElement({ tagName: "SELECT", value: "agent" }),
      "filter-date-from": makeElement({ value: "2024-01-01" }),
      "filter-date-to": makeElement({ value: "2024-12-31" }),
    });

    expect(filters.getActiveFiltersFromDOM()).toEqual({
      search: "needle",
      developerId: "alice",
      agentLabel: "bot-7",
      glob: "src/*.py",
      status: "active",
      role: "agent",
      dateFrom: "2024-01-01",
      dateTo: "2024-12-31",
    });
  });

  test("ignores empty, whitespace, all-valued, and missing fields", function () {
    installDocument({
      "filter-search": makeElement({ value: "   " }),
      "filter-developer": makeElement({ tagName: "SELECT", value: "all" }),
      "filter-agent": makeElement({ value: "" }),
      "filter-glob": makeElement({ value: "" }),
      "filter-status": makeElement({ tagName: "SELECT", value: "all" }),
      "filter-role": makeElement({ tagName: "SELECT", value: "all" }),
      "filter-date-from": makeElement({ value: "" }),
      "filter-date-to": makeElement({ value: "" }),
      // filter elements deliberately resolvable; none should register
    });

    expect(filters.getActiveFiltersFromDOM()).toEqual({});
  });
});

// ---------------------------------------------------------------------------
// clearFilterInputs
// ---------------------------------------------------------------------------

describe("clearFilterInputs", function () {
  test("returns empty object when document is unavailable", function () {
    expect(filters.clearFilterInputs()).toEqual({});
  });

  test("resets selects to 'all', inputs to '', skips missing nodes", function () {
    const search = makeElement({ tagName: "INPUT", value: "x" });
    const developer = makeElement({ tagName: "SELECT", value: "alice" });
    const status = makeElement({ tagName: "SELECT", value: "active" });
    const dateFrom = makeElement({ tagName: "INPUT", value: "2024-01-01" });
    const dateTo = makeElement({ tagName: "INPUT", value: "2024-02-02" });
    // "filter-glob" and "filter-agent" intentionally absent → covers `continue`
    installDocument({
      "filter-search": search,
      "filter-developer": developer,
      "filter-status": status,
      "filter-date-from": dateFrom,
      "filter-date-to": dateTo,
    });

    expect(filters.clearFilterInputs()).toEqual({});
    expect(search.value).toBe("");
    expect(dateFrom.value).toBe("");
    expect(dateTo.value).toBe("");
    expect(developer.value).toBe("all");
    expect(status.value).toBe("all");
  });
});

// ---------------------------------------------------------------------------
// populateDeveloperFilter
// ---------------------------------------------------------------------------

describe("populateDeveloperFilter", function () {
  test("no-op when document is unavailable", function () {
    expect(filters.populateDeveloperFilter([], [])).toBeUndefined();
  });

  test("tolerates null locks and history arrays", function () {
    const developer = makeElement({ tagName: "SELECT", value: "all" });
    installDocument({ "filter-developer": developer });
    expect(filters.populateDeveloperFilter(null, null)).toBeUndefined();
    expect(developer.children).toHaveLength(0);
    expect(developer.innerHTML).toContain("All Developers");
  });

  test("fills present dropdown, skips missing one, preserves selection", function () {
    const developer = makeElement({ tagName: "SELECT", value: "alice" });
    // "filter-developer-history" absent → covers the `if (!devEl) return` branch
    installDocument({ "filter-developer": developer });

    filters.populateDeveloperFilter(
      [{ developer_id: "bob" }, { developer_id: "alice" }, {}],
      [{ developer_id: "carol" }, { developer_id: "alice" }, {}],
    );

    // innerHTML reset to the "All Developers" option, then unique sorted ids.
    expect(developer.innerHTML).toContain("All Developers");
    const values = developer.children.map(function (opt) {
      return opt.value;
    });
    expect(values).toEqual(["alice", "bob", "carol"]);
    const selected = developer.children.filter(function (opt) {
      return opt.selected;
    });
    expect(selected).toHaveLength(1);
    expect(selected[0].value).toBe("alice");
  });
});

// ---------------------------------------------------------------------------
// charts: initActivityChart / updateActivityChart
// ---------------------------------------------------------------------------

function makeFakeChartCtor(getChartReturn) {
  function FakeChart(canvas, config) {
    this.canvas = canvas;
    this.config = config;
    this.data = config.data;
    this.updated = false;
    this.update = function () {
      this.updated = true;
    };
    this.destroy = function () {};
  }
  FakeChart.getChart = function () {
    return getChartReturn || null;
  };
  return FakeChart;
}

describe("initActivityChart", function () {
  test("returns null when document is unavailable", function () {
    expect(charts.initActivityChart("activity-chart")).toBeNull();
  });

  test("returns null when Chart.js is unavailable", function () {
    installDocument({ "activity-chart": makeElement({ tagName: "CANVAS" }) });
    global.window = {};
    expect(charts.initActivityChart("activity-chart")).toBeNull();
  });

  test("returns null when canvas element is missing", function () {
    installDocument({});
    global.window = { Chart: makeFakeChartCtor(null) };
    expect(charts.initActivityChart("activity-chart")).toBeNull();
  });

  test("creates a chart instance when canvas + Chart.js are present", function () {
    const canvas = makeElement({ tagName: "CANVAS" });
    installDocument({ "activity-chart": canvas });
    global.window = { Chart: makeFakeChartCtor(null) };

    const chart = charts.initActivityChart("activity-chart");
    expect(chart).not.toBeNull();
    expect(chart.canvas).toBe(canvas);
    expect(chart.config.type).toBe("bar");
    expect(chart.config.data.datasets).toHaveLength(2);
  });

  test("destroys an existing chart before re-initializing", function () {
    const canvas = makeElement({ tagName: "CANVAS" });
    installDocument({ "activity-chart": canvas });
    let destroyed = false;
    const existing = {
      destroy: function () {
        destroyed = true;
      },
    };
    global.window = { Chart: makeFakeChartCtor(existing) };

    const chart = charts.initActivityChart("activity-chart");
    expect(destroyed).toBe(true);
    expect(chart).not.toBeNull();
  });
});

describe("updateActivityChart", function () {
  test("no-op when chart is null", function () {
    expect(charts.updateActivityChart(null, [], "24h")).toBeUndefined();
  });

  test("writes timeline data into the chart and triggers update", function () {
    const chart = {
      data: { labels: [], datasets: [{ data: [] }, { data: [] }] },
      updated: false,
      update: function () {
        this.updated = true;
      },
    };

    charts.updateActivityChart(chart, [], "24h");

    expect(chart.data.labels).toHaveLength(24);
    expect(chart.data.datasets[0].data).toHaveLength(24);
    expect(chart.data.datasets[1].data).toHaveLength(24);
    expect(chart.updated).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// UMD browser-global branch (no module.exports available)
// ---------------------------------------------------------------------------

function loadAsBrowserGlobal(relPath, globalName) {
  const code = fs.readFileSync(path.join(__dirname, relPath), "utf8");
  // No `module` in the sandbox → the UMD wrapper takes the `else` branch and
  // assigns the API onto the global object (here, the vm context itself).
  const sandbox = { console: console };
  vm.runInNewContext(code, sandbox);
  return sandbox[globalName];
}

describe("UMD browser-global registration", function () {
  test("dashboard-filters attaches DashboardFilters to the global", function () {
    const api = loadAsBrowserGlobal(
      "../../../collab/dashboard/dashboard-filters.js",
      "DashboardFilters",
    );
    expect(api).toBeDefined();
    expect(typeof api.matchesFilter).toBe("function");
  });

  test("dashboard-charts attaches DashboardCharts to the global", function () {
    const api = loadAsBrowserGlobal(
      "../../../collab/dashboard/dashboard-charts.js",
      "DashboardCharts",
    );
    expect(api).toBeDefined();
    expect(typeof api.buildTimelineData).toBe("function");
  });
});
