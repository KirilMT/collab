/**
 * Filtering, sorting, and search helpers for the Collaborative Lock Dashboard.
 * Loaded in the browser (global DashboardFilters) and in Jest (module.exports).
 *
 * Pure functions are fully testable. DOM-aware helpers are browser-only and
 * guarded by typeof document checks.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.DashboardFilters = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  /**
   * Case-insensitive substring match.  Returns true when *needle* is empty
   * (no filter applied).
   */
  function _contains(haystack, needle) {
    if (!needle) return true;
    return (
      String(haystack || "")
        .toLowerCase()
        .indexOf(needle.toLowerCase()) !== -1
    );
  }

  /**
   * Simple glob → regex conversion.  Supports ``*`` (any chars) and ``?``
   * (single char).  Other regex-special characters are escaped so users can
   * type literal dots, slashes, etc.
   */
  function _globToRegex(pattern) {
    var escaped = String(pattern || "").replace(/[.+^${}()|[\]\\]/g, "\\$&");
    var regexStr = escaped.replace(/\*/g, ".*").replace(/\?/g, ".");
    return new RegExp("^" + regexStr + "$", "i");
  }

  // ---------------------------------------------------------------------------
  // Pure filter / sort functions
  // ---------------------------------------------------------------------------

  /**
   * Check whether *lock* matches a single glob pattern.  An empty pattern
   * matches everything.
   *
   * @param {object}  lock
   * @param {string}  pattern  Glob pattern for the file_path field.
   * @returns {boolean}
   */
  function matchesGlob(lock, pattern) {
    if (!pattern) return true;
    return _globToRegex(pattern).test(String(lock.file_path || ""));
  }

  /**
   * Check whether *lock* matches all criteria in *filters*.
   *
   * @param {object} lock     A single lock row (from Supabase).
   * @param {object} filters  {
   *   search       ?: string   – full-text across file_path and developer_id
   *   developerId  ?: string   – exact developer_id match
   *   agentLabel   ?: string   – substring match on agent_label
   *   glob         ?: string   – glob pattern against file_path
   *   status       ?: string   – 'active' | 'released' | 'all'
   *   dateFrom     ?: string   – ISO date string lower bound (acquired_at)
   *   dateTo       ?: string   – ISO date string upper bound (acquired_at)
   * }
   * @returns {boolean}
   */
  function matchesFilter(lock, filters) {
    if (!filters || !lock) return true;

    // Full-text search across file_path + developer_id
    if (filters.search) {
      var s = filters.search.toLowerCase();
      if (
        String(lock.file_path || "")
          .toLowerCase()
          .indexOf(s) === -1 &&
        String(lock.developer_id || "")
          .toLowerCase()
          .indexOf(s) === -1
      ) {
        return false;
      }
    }

    // Developer ID (exact match)
    if (filters.developerId) {
      if (String(lock.developer_id || "") !== filters.developerId) return false;
    }

    // Agent label (substring match)
    if (filters.agentLabel) {
      if (!_contains(lock.agent_label, filters.agentLabel)) return false;
    }

    // File path glob
    if (filters.glob && !matchesGlob(lock, filters.glob)) return false;

    // Status ('active' = no released_at; 'released' = has released_at; 'conflict' = outcome is conflict)
    if (filters.status) {
      var isReleased = !!lock.released_at;
      if (filters.status === "active" && isReleased) return false;
      if (filters.status === "released" && !isReleased) return false;
      if (filters.status === "conflict" && lock.outcome !== "conflict")
        return false;
    }

    // Role ('agent' = has agent_id, 'human' = no agent_id)
    if (filters.role) {
      var isAgent = !!lock.agent_id;
      if (filters.role === "agent" && !isAgent) return false;
      if (filters.role === "human" && isAgent) return false;
    }

    // Date range (applied to acquired_at for active locks, or released_at fallback)
    if (filters.dateFrom || filters.dateTo) {
      var dateField = lock.acquired_at || lock.released_at;
      if (!dateField) {
        // Can't filter by date if there is no timestamp at all
        if (filters.dateFrom || filters.dateTo) return false;
      } else {
        var dt = new Date(dateField).getTime();
        if (isNaN(dt)) return false;
        if (filters.dateFrom) {
          var from = new Date(filters.dateFrom).getTime();
          if (!isNaN(from) && dt < from) return false;
        }
        if (filters.dateTo) {
          var to = new Date(filters.dateTo).getTime();
          if (!isNaN(to) && dt > to + 86399999) return false; // end of day
        }
      }
    }

    return true;
  }

  /**
   * Filter an array of locks using *filters*.
   *
   * @param {Array<object>} locks
   * @param {object}        filters  (see matchesFilter)
   * @returns {Array<object>}
   */
  function filterLocks(locks, filters) {
    if (!filters || !Object.keys(filters).length) return locks.slice();
    return locks.filter(function (lock) {
      return matchesFilter(lock, filters);
    });
  }

  /**
   * Sort an array of locks by *field* in *direction*.
   *
   * @param {Array<object>} locks
   * @param {string}        field     Column name (file_path, developer_id, etc.)
   * @param {string}        direction 'asc' or 'desc'
   * @returns {Array<object>}  New sorted array (does not mutate input).
   */
  function sortLocks(locks, field, direction) {
    var sorted = locks.slice();
    var dir = direction === "desc" ? -1 : 1;

    sorted.sort(function (a, b) {
      var va = a[field];
      var vb = b[field];

      // Nulls always sort last regardless of direction
      var aNull = va === null || va === undefined;
      var bNull = vb === null || vb === undefined;
      if (aNull && bNull) return 0;
      if (aNull) return 1;
      if (bNull) return -1;

      // Date fields
      if (field === "acquired_at" || field === "released_at") {
        var da = new Date(va).getTime();
        var db = new Date(vb).getTime();
        if (isNaN(da) && isNaN(db)) return 0;
        if (isNaN(da)) return 1;
        if (isNaN(db)) return -1;
        return (da - db) * dir;
      }

      // Numeric duration (computed field)
      if (field === "duration_minutes") {
        return ((Number(va) || 0) - (Number(vb) || 0)) * dir;
      }

      // Default string comparison
      return String(va || "").localeCompare(String(vb || "")) * dir;
    });

    return sorted;
  }

  /**
   * Return true when *filters* has at least one active criterion.
   *
   * @param {object} filters
   * @returns {boolean}
   */
  function hasActiveFilters(filters) {
    if (!filters) return false;
    var keys = Object.keys(filters);
    for (var i = 0; i < keys.length; i++) {
      var v = filters[keys[i]];
      if (v !== null && v !== undefined && v !== "" && v !== "all") return true;
    }
    return false;
  }

  /**
   * Count how many individual filter criteria are active.
   *
   * @param {object} filters
   * @returns {number}
   */
  function countActiveFilters(filters) {
    if (!filters) return 0;
    var count = 0;
    var keys = Object.keys(filters);
    for (var i = 0; i < keys.length; i++) {
      var v = filters[keys[i]];
      if (v !== null && v !== undefined && v !== "" && v !== "all") count++;
    }
    return count;
  }

  // ---------------------------------------------------------------------------
  // Query-string helpers (for shareable-filtered URLs — future use)
  // ---------------------------------------------------------------------------

  /**
   * Parse URL query string into a filters object.
   *
   * @param {string} qs  e.g. "search=foo&status=active"
   * @returns {object}
   */
  function parseQueryString(qs) {
    var result = {};
    var raw = String(qs || "").replace(/^[?#]/, "");
    if (!raw) return result;
    var pairs = raw.split("&");
    for (var i = 0; i < pairs.length; i++) {
      var parts = pairs[i].split("=");
      if (parts.length === 2) {
        var key = decodeURIComponent(parts[0]);
        var val = decodeURIComponent(parts[1]);
        if (key && val) result[key] = val;
      }
    }
    return result;
  }

  /**
   * Build a query string from a filters object (only non-empty values).
   *
   * @param {object} filters
   * @returns {string}
   */
  function buildQueryString(filters) {
    if (!filters) return "";
    var parts = [];
    var keys = Object.keys(filters);
    for (var i = 0; i < keys.length; i++) {
      var v = filters[keys[i]];
      if (v !== null && v !== undefined && v !== "" && v !== "all") {
        parts.push(
          encodeURIComponent(keys[i]) + "=" + encodeURIComponent(String(v)),
        );
      }
    }
    return parts.length ? "?" + parts.join("&") : "";
  }

  // ---------------------------------------------------------------------------
  // DOM helpers (browser-only — guarded for Jest)
  // ---------------------------------------------------------------------------

  /**
   * Read the current filter state from the dashboard filter bar DOM elements.
   * Returns an empty object when running outside a browser (Jest).
   *
   * @returns {object}
   */
  function getActiveFiltersFromDOM() {
    if (typeof document === "undefined") return {};

    var filters = {};

    var searchEl = document.getElementById("filter-search");
    if (searchEl && searchEl.value.trim()) {
      filters.search = searchEl.value.trim();
    }

    var devEl = document.getElementById("filter-developer");
    if (devEl && devEl.value && devEl.value !== "all") {
      filters.developerId = devEl.value;
    }

    var agentEl = document.getElementById("filter-agent");
    if (agentEl && agentEl.value.trim()) {
      filters.agentLabel = agentEl.value.trim();
    }

    var globEl = document.getElementById("filter-glob");
    if (globEl && globEl.value.trim()) {
      filters.glob = globEl.value.trim();
    }

    var statusEl = document.getElementById("filter-status");
    if (statusEl && statusEl.value && statusEl.value !== "all") {
      filters.status = statusEl.value;
    }

    var roleEl = document.getElementById("filter-role");
    if (roleEl && roleEl.value && roleEl.value !== "all") {
      filters.role = roleEl.value;
    }

    var dateFromEl = document.getElementById("filter-date-from");
    if (dateFromEl && dateFromEl.value) {
      filters.dateFrom = dateFromEl.value;
    }

    var dateToEl = document.getElementById("filter-date-to");
    if (dateToEl && dateToEl.value) {
      filters.dateTo = dateToEl.value;
    }

    return filters;
  }

  /**
   * Clear all filter inputs in the DOM and return an empty filters object.
   *
   * @returns {object}  Empty filters.
   */
  function clearFilterInputs() {
    if (typeof document === "undefined") return {};

    var ids = [
      "filter-search",
      "filter-developer",
      "filter-agent",
      "filter-glob",
      "filter-status",
      "filter-date-from",
      "filter-date-to",
    ];
    for (var i = 0; i < ids.length; i++) {
      var el = document.getElementById(ids[i]);
      if (!el) continue;
      if (el.tagName === "SELECT") {
        el.value = "all";
      } else {
        el.value = "";
      }
    }

    return {};
  }

  /**
   * Populate the developer filter dropdown with unique developer IDs from
   * current locks + history.
   *
   * @param {Array<object>} locks
   * @param {Array<object>} history
   */
  function populateDeveloperFilter(locks, history) {
    if (typeof document === "undefined") return;

    var seen = {};
    (locks || []).forEach(function (row) {
      if (row.developer_id) seen[row.developer_id] = true;
    });
    (history || []).forEach(function (row) {
      if (row.developer_id) seen[row.developer_id] = true;
    });

    // Populate both locks-page and history-page developer dropdowns
    var devIds = ["filter-developer", "filter-developer-history"];
    devIds.forEach(function (devId) {
      var devEl = document.getElementById(devId);
      if (!devEl) return;
      var currentVal = devEl.value;
      devEl.innerHTML = '<option value="all">All Developers</option>';
      Object.keys(seen)
        .sort()
        .forEach(function (dev) {
          var opt = document.createElement("option");
          opt.value = dev;
          opt.textContent = dev;
          if (dev === currentVal) opt.selected = true;
          devEl.appendChild(opt);
        });
    });
  }

  // ---------------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------------

  return {
    matchesGlob: matchesGlob,
    matchesFilter: matchesFilter,
    filterLocks: filterLocks,
    sortLocks: sortLocks,
    hasActiveFilters: hasActiveFilters,
    countActiveFilters: countActiveFilters,
    parseQueryString: parseQueryString,
    buildQueryString: buildQueryString,
    getActiveFiltersFromDOM: getActiveFiltersFromDOM,
    clearFilterInputs: clearFilterInputs,
    populateDeveloperFilter: populateDeveloperFilter,
  };
});
