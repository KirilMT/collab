const {
  matchesGlob,
  matchesFilter,
  filterLocks,
  sortLocks,
  hasActiveFilters,
  countActiveFilters,
  parseQueryString,
  buildQueryString,
} = require("../../../collab/dashboard/dashboard-filters");

// ---------------------------------------------------------------------------
// Sample lock fixtures
// ---------------------------------------------------------------------------

function makeLock(overrides) {
  return Object.assign(
    {
      file_path: "src/main.py",
      developer_id: "alice",
      agent_label: "feat: add login",
      agent_id: "agent-1",
      agent_kind: "copilot",
      origin: "agent",
      branch_name: "main",
      reason: "Working on login",
      acquired_at: "2026-06-08T10:00:00.000Z",
      released_at: null,
      is_ephemeral: false,
    },
    overrides || {},
  );
}

function makeHistoryLock(overrides) {
  return makeLock(
    Object.assign(
      {
        released_at: "2026-06-08T11:00:00.000Z",
        outcome: "released",
      },
      overrides || {},
    ),
  );
}

// ---------------------------------------------------------------------------
// matchesGlob
// ---------------------------------------------------------------------------

describe("matchesGlob", function () {
  test("empty pattern matches everything", function () {
    expect(matchesGlob(makeLock(), "")).toBe(true);
    expect(matchesGlob(makeLock(), null)).toBe(true);
    expect(matchesGlob(makeLock(), undefined)).toBe(true);
  });

  test("literal match", function () {
    expect(
      matchesGlob(makeLock({ file_path: "src/main.py" }), "src/main.py"),
    ).toBe(true);
  });

  test("literal mismatch", function () {
    expect(
      matchesGlob(makeLock({ file_path: "src/main.py" }), "src/test.py"),
    ).toBe(false);
  });

  test("wildcard *", function () {
    expect(matchesGlob(makeLock({ file_path: "src/main.py" }), "src/*")).toBe(
      true,
    );
    expect(matchesGlob(makeLock({ file_path: "src/main.py" }), "*.py")).toBe(
      true,
    );
    expect(matchesGlob(makeLock({ file_path: "src/main.py" }), "*.js")).toBe(
      false,
    );
  });

  test("wildcard ?", function () {
    expect(matchesGlob(makeLock({ file_path: "a.py" }), "?.py")).toBe(true);
    expect(matchesGlob(makeLock({ file_path: "ab.py" }), "?.py")).toBe(false);
  });

  test("case-insensitive", function () {
    expect(
      matchesGlob(makeLock({ file_path: "SRC/MAIN.PY" }), "src/main.py"),
    ).toBe(true);
  });

  test("path with dots", function () {
    expect(
      matchesGlob(
        makeLock({ file_path: "collab/__init__.py" }),
        "collab/__init__.py",
      ),
    ).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// matchesFilter
// ---------------------------------------------------------------------------

describe("matchesFilter", function () {
  test("empty / null filters match everything", function () {
    expect(matchesFilter(makeLock(), {})).toBe(true);
    expect(matchesFilter(makeLock(), null)).toBe(true);
  });

  // -- search ----------------------------------------------------------------

  test("search matches file_path", function () {
    expect(
      matchesFilter(makeLock({ file_path: "src/login.py" }), {
        search: "login",
      }),
    ).toBe(true);
  });

  test("search matches developer_id", function () {
    expect(
      matchesFilter(makeLock({ developer_id: "bob" }), { search: "bob" }),
    ).toBe(true);
  });

  test("search is case-insensitive", function () {
    expect(
      matchesFilter(makeLock({ file_path: "SRC/LOGIN.PY" }), {
        search: "login",
      }),
    ).toBe(true);
  });

  test("search mismatch", function () {
    expect(matchesFilter(makeLock(), { search: "zzznotfound" })).toBe(false);
  });

  // -- developerId -----------------------------------------------------------

  test("developerId exact match", function () {
    expect(
      matchesFilter(makeLock({ developer_id: "alice" }), {
        developerId: "alice",
      }),
    ).toBe(true);
    expect(
      matchesFilter(makeLock({ developer_id: "alice" }), {
        developerId: "bob",
      }),
    ).toBe(false);
  });

  // -- agentLabel ------------------------------------------------------------

  test("agentLabel substring match", function () {
    expect(
      matchesFilter(makeLock({ agent_label: "feat: add login" }), {
        agentLabel: "login",
      }),
    ).toBe(true);
    expect(
      matchesFilter(makeLock({ agent_label: "feat: add login" }), {
        agentLabel: "signup",
      }),
    ).toBe(false);
  });

  test("agentLabel case-insensitive", function () {
    expect(
      matchesFilter(makeLock({ agent_label: "FEAT: ADD LOGIN" }), {
        agentLabel: "login",
      }),
    ).toBe(true);
  });

  // -- glob ------------------------------------------------------------------

  test("glob matches file_path", function () {
    expect(
      matchesFilter(makeLock({ file_path: "src/main.py" }), {
        glob: "src/*.py",
      }),
    ).toBe(true);
    expect(
      matchesFilter(makeLock({ file_path: "src/main.py" }), { glob: "*.js" }),
    ).toBe(false);
  });

  // -- status ----------------------------------------------------------------

  test("status active", function () {
    expect(
      matchesFilter(makeLock({ released_at: null }), { status: "active" }),
    ).toBe(true);
    expect(matchesFilter(makeHistoryLock(), { status: "active" })).toBe(false);
  });

  test("status released", function () {
    expect(matchesFilter(makeHistoryLock(), { status: "released" })).toBe(true);
    expect(
      matchesFilter(makeLock({ released_at: null }), { status: "released" }),
    ).toBe(false);
  });

  test("status all", function () {
    expect(matchesFilter(makeLock(), { status: "all" })).toBe(true);
    expect(matchesFilter(makeHistoryLock(), { status: "all" })).toBe(true);
  });

  // -- date range ------------------------------------------------------------

  test("dateFrom includes locks acquired on or after", function () {
    expect(
      matchesFilter(makeLock({ acquired_at: "2026-06-08T10:00:00Z" }), {
        dateFrom: "2026-06-08",
      }),
    ).toBe(true);
    expect(
      matchesFilter(makeLock({ acquired_at: "2026-06-07T23:59:59Z" }), {
        dateFrom: "2026-06-08",
      }),
    ).toBe(false);
  });

  test("dateTo includes locks acquired on or before", function () {
    expect(
      matchesFilter(makeLock({ acquired_at: "2026-06-08T10:00:00Z" }), {
        dateTo: "2026-06-08",
      }),
    ).toBe(true);
    expect(
      matchesFilter(makeLock({ acquired_at: "2026-06-09T00:00:00Z" }), {
        dateTo: "2026-06-08",
      }),
    ).toBe(false);
  });

  test("date range excludes locks with no timestamp", function () {
    expect(
      matchesFilter(makeLock({ acquired_at: null, released_at: null }), {
        dateFrom: "2026-06-01",
      }),
    ).toBe(false);
  });

  // -- combined filters ------------------------------------------------------

  test("combined filters (AND logic)", function () {
    var lock = makeLock({
      file_path: "src/auth.py",
      developer_id: "alice",
      agent_label: "fix: auth bug",
    });
    expect(
      matchesFilter(lock, {
        search: "auth",
        developerId: "alice",
        agentLabel: "bug",
      }),
    ).toBe(true);

    // One mismatch breaks it
    expect(
      matchesFilter(lock, {
        search: "auth",
        developerId: "bob",
      }),
    ).toBe(false);
  });

  // -- status: conflict -----------------------------------------------------

  test("status conflict matches any lock with outcome=conflict", function () {
    // History lock with conflict outcome
    expect(
      matchesFilter(makeHistoryLock({ outcome: "conflict" }), {
        status: "conflict",
      }),
    ).toBe(true);
    // History lock with normal release
    expect(
      matchesFilter(makeHistoryLock({ outcome: "released" }), {
        status: "conflict",
      }),
    ).toBe(false);
    // Active lock with conflict outcome also matches
    expect(
      matchesFilter(makeLock({ outcome: "conflict" }), { status: "conflict" }),
    ).toBe(true);
    // Active lock without conflict outcome does not match
    expect(matchesFilter(makeLock(), { status: "conflict" })).toBe(false);
  });

  // -- role filter ----------------------------------------------------------

  test("role agent matches locks with agent_id", function () {
    expect(
      matchesFilter(makeLock({ agent_id: "agent-1" }), { role: "agent" }),
    ).toBe(true);
  });

  test("role agent rejects locks without agent_id", function () {
    expect(matchesFilter(makeLock({ agent_id: null }), { role: "agent" })).toBe(
      false,
    );
  });

  test("role human matches locks without agent_id", function () {
    expect(matchesFilter(makeLock({ agent_id: null }), { role: "human" })).toBe(
      true,
    );
  });

  test("role human rejects locks with agent_id", function () {
    expect(
      matchesFilter(makeLock({ agent_id: "agent-1" }), { role: "human" }),
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// filterLocks
// ---------------------------------------------------------------------------

describe("filterLocks", function () {
  var locks;

  beforeEach(function () {
    locks = [
      makeLock({ file_path: "src/main.py", developer_id: "alice" }),
      makeLock({ file_path: "src/test.py", developer_id: "bob" }),
      makeLock({ file_path: "docs/readme.md", developer_id: "alice" }),
      makeHistoryLock({ file_path: "src/old.py", developer_id: "charlie" }),
    ];
  });

  test("empty filters returns all locks", function () {
    expect(filterLocks(locks, {})).toHaveLength(4);
  });

  test("filters by developerId", function () {
    var result = filterLocks(locks, { developerId: "alice" });
    expect(result).toHaveLength(2);
    result.forEach(function (l) {
      expect(l.developer_id).toBe("alice");
    });
  });

  test("filters by status active", function () {
    var result = filterLocks(locks, { status: "active" });
    expect(result).toHaveLength(3);
    result.forEach(function (l) {
      expect(l.released_at).toBeNull();
    });
  });

  test("filters by status released", function () {
    var result = filterLocks(locks, { status: "released" });
    expect(result).toHaveLength(1);
  });

  test("does not mutate original array", function () {
    var original = locks.slice();
    filterLocks(locks, { developerId: "alice" });
    expect(locks).toEqual(original);
  });
});

// ---------------------------------------------------------------------------
// sortLocks
// ---------------------------------------------------------------------------

describe("sortLocks", function () {
  var locks;

  beforeEach(function () {
    locks = [
      makeLock({ file_path: "c.py", developer_id: "zeta" }),
      makeLock({ file_path: "a.py", developer_id: "alpha" }),
      makeLock({ file_path: "b.py", developer_id: "gamma" }),
    ];
  });

  test("sorts asc by file_path", function () {
    var result = sortLocks(locks, "file_path", "asc");
    expect(result[0].file_path).toBe("a.py");
    expect(result[1].file_path).toBe("b.py");
    expect(result[2].file_path).toBe("c.py");
  });

  test("sorts desc by file_path", function () {
    var result = sortLocks(locks, "file_path", "desc");
    expect(result[0].file_path).toBe("c.py");
    expect(result[1].file_path).toBe("b.py");
    expect(result[2].file_path).toBe("a.py");
  });

  test("sorts by developer_id asc", function () {
    var result = sortLocks(locks, "developer_id", "asc");
    expect(result[0].developer_id).toBe("alpha");
    expect(result[2].developer_id).toBe("zeta");
  });

  test("nulls sort last asc", function () {
    var withNull = [
      makeLock({ file_path: "a.py", developer_id: null }),
      makeLock({ file_path: "b.py", developer_id: "bob" }),
    ];
    var result = sortLocks(withNull, "developer_id", "asc");
    expect(result[0].developer_id).toBe("bob");
    expect(result[1].developer_id).toBeNull();
  });

  test("nulls sort last desc", function () {
    var withNull = [
      makeLock({ file_path: "a.py", developer_id: null }),
      makeLock({ file_path: "b.py", developer_id: "bob" }),
    ];
    var result = sortLocks(withNull, "developer_id", "desc");
    expect(result[0].developer_id).toBe("bob");
    expect(result[1].developer_id).toBeNull();
  });

  test("does not mutate original array", function () {
    var original = locks.slice();
    sortLocks(locks, "file_path", "desc");
    expect(locks).toEqual(original);
  });

  test("sorts by acquired_at date asc", function () {
    var dated = [
      makeLock({ file_path: "a.py", acquired_at: "2026-06-08T15:00:00Z" }),
      makeLock({ file_path: "b.py", acquired_at: "2026-06-08T10:00:00Z" }),
    ];
    var result = sortLocks(dated, "acquired_at", "asc");
    expect(result[0].file_path).toBe("b.py");
    expect(result[1].file_path).toBe("a.py");
  });

  test("sorts by acquired_at date desc", function () {
    var dated = [
      makeLock({ file_path: "a.py", acquired_at: "2026-06-08T10:00:00Z" }),
      makeLock({ file_path: "b.py", acquired_at: "2026-06-08T15:00:00Z" }),
    ];
    var result = sortLocks(dated, "acquired_at", "desc");
    expect(result[0].file_path).toBe("b.py");
    expect(result[1].file_path).toBe("a.py");
  });

  test("sorts by released_at date", function () {
    var dated = [
      makeHistoryLock({
        file_path: "a.py",
        released_at: "2026-06-08T15:00:00Z",
      }),
      makeHistoryLock({
        file_path: "b.py",
        released_at: "2026-06-08T10:00:00Z",
      }),
    ];
    var result = sortLocks(dated, "released_at", "asc");
    expect(result[0].file_path).toBe("b.py");
    expect(result[1].file_path).toBe("a.py");
  });

  test("invalid dates sort to end", function () {
    var dated = [
      makeLock({ file_path: "a.py", acquired_at: "invalid" }),
      makeLock({ file_path: "b.py", acquired_at: "2026-01-01T00:00:00Z" }),
    ];
    var result = sortLocks(dated, "acquired_at", "asc");
    expect(result[0].file_path).toBe("b.py");
    expect(result[1].file_path).toBe("a.py");
  });

  test("sorts numeric duration_minutes", function () {
    var dur = [
      makeHistoryLock({ file_path: "a.py", duration_minutes: 30 }),
      makeHistoryLock({ file_path: "b.py", duration_minutes: 10 }),
    ];
    var result = sortLocks(dur, "duration_minutes", "asc");
    expect(result[0].file_path).toBe("b.py");
    expect(result[1].file_path).toBe("a.py");
  });
});

// ---------------------------------------------------------------------------
// hasActiveFilters / countActiveFilters
// ---------------------------------------------------------------------------

describe("hasActiveFilters", function () {
  test("empty filters", function () {
    expect(hasActiveFilters({})).toBe(false);
    expect(hasActiveFilters(null)).toBe(false);
  });

  test("all-default values treated as inactive", function () {
    expect(
      hasActiveFilters({ search: "", status: "all", developerId: "" }),
    ).toBe(false);
  });

  test("detects active filter", function () {
    expect(hasActiveFilters({ search: "foo" })).toBe(true);
  });
});

describe("countActiveFilters", function () {
  test("zero for empty", function () {
    expect(countActiveFilters({})).toBe(0);
  });

  test("counts active criteria", function () {
    expect(
      countActiveFilters({ search: "foo", status: "active", developerId: "" }),
    ).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// parseQueryString / buildQueryString
// ---------------------------------------------------------------------------

describe("parseQueryString", function () {
  test("empty string", function () {
    expect(parseQueryString("")).toEqual({});
  });

  test("parses single param", function () {
    expect(parseQueryString("?search=foo")).toEqual({ search: "foo" });
  });

  test("parses multiple params", function () {
    expect(parseQueryString("?search=foo&status=active")).toEqual({
      search: "foo",
      status: "active",
    });
  });

  test("handles hash prefix", function () {
    expect(parseQueryString("#search=foo")).toEqual({ search: "foo" });
  });

  test("ignores malformed pairs", function () {
    expect(parseQueryString("?a&b=c")).toEqual({ b: "c" });
  });
});

describe("buildQueryString", function () {
  test("empty filters", function () {
    expect(buildQueryString({})).toBe("");
    expect(buildQueryString(null)).toBe("");
  });

  test("builds query string", function () {
    expect(buildQueryString({ search: "foo", status: "active" })).toBe(
      "?search=foo&status=active",
    );
  });

  test("skips empty and 'all' values", function () {
    expect(
      buildQueryString({ search: "", status: "all", developerId: "alice" }),
    ).toBe("?developerId=alice");
  });
});
