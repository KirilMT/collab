// Collaborative File Locks — VS Code Extension (collab repo)
// Prevents merge conflicts by locking files when you start editing them.
//
// Requires: @supabase/supabase-js (run `npm install` in this directory)
// Reads credentials from workspace .env file.

const vscode = require("vscode");
const path = require("path");
const fs = require("fs");
const { spawn, execSync } = require("child_process");
const os = require("os");
const crypto = require("crypto");

/**
 * Per-workspace state directory (outside the repo) for transient files.
 * Defaults to $TMP/collab_runtime_<hash> unless COLLAB_STATE_DIR is set.
 */
function getStateDir(workspaceRoot) {
  const env = process.env.COLLAB_STATE_DIR;
  if (env) return env;
  try {
    const normRoot = workspaceRoot.replace(/\//g, "\\").toLowerCase().replace(/\\+$/, "");
    const h = crypto
      .createHash("sha1")
      .update(normRoot)
      .digest("hex")
      .slice(0, 8);
    // Keep namespace aligned with src/lock_client.py::_get_state_dir().
    const dir = path.join(os.tmpdir(), `collab_runtime_${h}`);
    if (!fs.existsSync(dir)) {
      try {
        fs.mkdirSync(dir, { recursive: true });
      } catch (e) {
        logToCollab(`Failed to create state dir: ${e.message}`, "DEBUG");
      }
    }
    return dir;
  } catch (_e) {
    return os.tmpdir();
  }
}

let statusBarItem;
let supabaseClient = null;
let currentSubscription = null;
let summaryPoller = null;
let watcherProcess = null;

/**
 * Standardized logging to the central collab.log file.
 * Mimics Python log format: [YYYY-MM-DD HH:MM:SS] LEVEL collab.extension: message
 */
function logToCollab(message, level = "INFO") {
  try {
    const workspaceRoot = getWorkspaceRoot();
    if (!workspaceRoot) return;
    const logDir = path.join(workspaceRoot, "logs");
    if (!fs.existsSync(logDir)) fs.mkdirSync(logDir, { recursive: true });
    const logFile = path.join(logDir, "collab.log");

    const now = new Date();
    // Format: YYYY-MM-DD HH:MM:SS
    const dateStr = now.toISOString().replace(/T/, " ").replace(/\..+/, "");
    const entry = `[${dateStr}] ${level} collab.extension: ${message}\n`;
    fs.appendFileSync(logFile, entry);
  } catch (e) {
    // Best effort: don't crash the extension over logging, but don't swallow silently
    console.error(`[collab] Failed to append to collab.log: ${e.message}`);
  }
  try {
    const workspaceRoot = getWorkspaceRoot();
    if (workspaceRoot) {
      const debugLog = path.join(workspaceRoot, "logs", "extension_debug.log");
      const now = new Date();
      const dateStr = now.toISOString().replace(/T/, " ").replace(/\..+/, "");
      fs.appendFileSync(debugLog, `[${dateStr}] ${level} ${message}\n`);
    }
  } catch (e) {
    console.error(`[collab] Failed to append to extension_debug.log: ${e.message}`);
  }
}
let watcherHeartbeatInterval = null;
let watcherHeartbeatFile = null;
let watcherHeartbeatTicks = 0;
let outputChannel = null;
let startupNotificationShown = false;
let lastStartupNotificationKey = null;
let isDeactivating = false;
let watcherRestartTimer = null;
let watcherRestartAttempts = 0;

function scheduleWatcherRestart(reason) {
  if (isDeactivating) return;
  const workspaceRoot = getWorkspaceRoot();
  if (!workspaceRoot) return;
  if (watcherRestartTimer) return;

  const stateDir = getStateDir(workspaceRoot);
  const stopFile = path.join(stateDir, ".stop_request");
  const shutdownFile = path.join(stateDir, ".shutdown_complete");

  if (outputChannel) {
    outputChannel.appendLine(`[collab] Watcher exited: ${reason}. Checking if restart is needed...`);
    outputChannel.appendLine(`[collab] State directory: ${stateDir}`);
  }

  // Robust check for intentional stops: sometimes the file system takes a few
  // milliseconds to reflect the file creation/presence after process exit.
  let isIntentional = false;
  for (let i = 0; i < 3; i++) {
    if (fs.existsSync(stopFile) || fs.existsSync(shutdownFile)) {
      isIntentional = true;
      break;
    }
    // Tiny delay before retry
    const start = Date.now();
    while (Date.now() - start < 100) { /* sync sleep */ }
  }

  if (isIntentional) {
    if (outputChannel)
      outputChannel.appendLine(
        `[collab] Watcher stopped intentionally; skipping auto-restart.`,
      );
    return;
  }

  if (watcherRestartAttempts >= 3) {
    if (outputChannel)
      outputChannel.appendLine(
        `[collab] Watcher restart limit reached; not retrying.`,
      );
    return;
  }

  watcherRestartAttempts += 1;
  if (outputChannel)
    outputChannel.appendLine(
      `[collab] Watcher exited unexpectedly (${reason}); restarting (${watcherRestartAttempts}/3)...`,
    );

  watcherRestartTimer = setTimeout(() => {
    watcherRestartTimer = null;
    if (!isDeactivating) startWatcher();
  }, 1000);
}

/**
 * Show a startup notification once within a short debounce window.
 * Both the file-polling and log-parsing paths call this helper
 * to avoid duplicate VS Code popups for the same startup summary.
 */
function showStartupNotificationOnce(message, key) {
  try {
    if (startupNotificationShown) return;
    if (key && lastStartupNotificationKey === key) return;
    // Safety: do NOT show startup summaries if a stop is currently requested.
    // This prevents confusing "Startup Summary" popups during a manual daemon-stop.
    if (message.includes("Startup Summary")) {
      try {
        const workspaceRoot = getWorkspaceRoot();
        if (workspaceRoot) {
          const stateDir = getStateDir(workspaceRoot);
          if (fs.existsSync(path.join(stateDir, ".stop_request"))) {
            if (outputChannel) outputChannel.appendLine(`[collab] Suppressing startup summary because stop is requested.`);
            return;
          }
        }
      } catch (e) {
        logToCollab(`Failed to check stop request status: ${e.message}`, "DEBUG");
      }
    }

    startupNotificationShown = true;
    lastStartupNotificationKey = key || null;

    vscode.window.showInformationMessage(message);

    if (outputChannel) outputChannel.appendLine(`[collab] Notification: ${message}`);
    logToCollab(`Notification: ${message}`);

    setTimeout(() => {
      startupNotificationShown = false;
      lastStartupNotificationKey = null;
    }, 5000);
  } catch (e) {
    logToCollab(`Notification helper failed: ${e.message}`, "DEBUG");
  }
}

function getWorkspaceRoot() {
  return vscode.workspace.workspaceFolders?.[0]?.uri?.fsPath || null;
}

/* function getPythonCommand(workspaceRoot) { ... } */
function _getPythonCommand(workspaceRoot) {
  if (!workspaceRoot) return "python";
  const isWin = process.platform === "win32";
  const venvPython = isWin
    ? path.join(workspaceRoot, ".venv", "Scripts", "python.exe")
    : path.join(workspaceRoot, ".venv", "bin", "python");

  if (fs.existsSync(venvPython)) {
    return venvPython;
  }
  return "python";
}

/**
 * Detect installed collab runtime.
 * Returns { command: string, version: string | null } if found,
 * or { command: null, error: string } if not found.
 *
 * Tries:
 * 1. collab command in PATH
 * 2. collab-watcher command in PATH
 * 3. Workspace .venv/Scripts/collab.exe or collab-watcher.exe
 */
function detectCollab(workspaceRoot) {
  const candidates = [
    { name: "collab", cmd: "collab" },
    { name: "collab-watcher", cmd: "collab-watcher" },
  ];

  // If workspace has venv, check there first
  if (workspaceRoot) {
    const isWin = process.platform === "win32";
    const venvBin = isWin ? "Scripts" : "bin";
    const ext = isWin ? ".exe" : "";

    const venvCollab = path.join(workspaceRoot, ".venv", venvBin, `collab${ext}`);
    const venvWatcher = path.join(workspaceRoot, ".venv", venvBin, `collab-watcher${ext}`);

    if (fs.existsSync(venvCollab)) {
      return { command: venvCollab, version: null };
    }
    if (fs.existsSync(venvWatcher)) {
      return { command: venvWatcher, version: null };
    }
  }

  // Try each candidate in PATH
  for (const candidate of candidates) {
    try {
      const versionOut = execSync(`${candidate.cmd} --version`, {
        encoding: "utf8",
        timeout: 3000,
        stdio: ["ignore", "pipe", "pipe"],
      }).trim();
      return { command: candidate.cmd, version: versionOut || null };
    } catch (e) {
      logToCollab(`Candidate ${candidate.cmd} not found: ${e.message}`, "DEBUG");
    }
  }

  return {
    command: null,
    error:
      "collab package not found in PATH. Run 'pip install collab' to install the runtime.",
  };
}

function getVSCodeWindowPid() {
  try {
    if (process.env.VSCODE_PID) {
      const pid = parseInt(process.env.VSCODE_PID, 10);
      if (pid > 0) return pid;
    }
    if (process.platform === "win32") {
      try {
        const wmicOut = execSync(
          `wmic process where "ProcessId=${process.pid}" get ParentProcessId /value`,
          { encoding: "utf8", timeout: 5000 },
        );
        const match = wmicOut.match(/ParentProcessId=(\d+)/);
        if (match) {
          const parentPid = parseInt(match[1], 10);
          const nameOut = execSync(
            `wmic process where "ProcessId=${parentPid}" get Name /value`,
            { encoding: "utf8", timeout: 5000 },
          );
          if (
            nameOut.toLowerCase().includes("code") ||
            nameOut.toLowerCase().includes("antigravity") ||
            nameOut.toLowerCase().includes("cursor") ||
            nameOut.toLowerCase().includes("codium")
          )
            return parentPid;
        }
      } catch (e) {
        logToCollab(`WMIC parent check failed: ${e.message}`, "DEBUG");
      }
    }
    return process.pid;
  } catch (_e) {
    return process.pid;
  }
}

function parseEnvValue(rawValue) {
  const trimmed = rawValue.trim();
  if (!trimmed) {
    return "";
  }

  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1);
  }

  return trimmed.replace(/\s+#.*$/, "").trim();
}

function loadConfig() {
  const workspaceRoot = getWorkspaceRoot();
  if (!workspaceRoot) return null;

  const envPath = path.join(workspaceRoot, ".env");
  if (!fs.existsSync(envPath)) return null;

  const envContent = fs.readFileSync(envPath, "utf-8");
  const vars = {};
  for (const line of envContent.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const idx = trimmed.indexOf("=");
    if (idx <= 0) continue;
    vars[trimmed.substring(0, idx).trim()] = parseEnvValue(
      trimmed.substring(idx + 1),
    );
  }

  const url = vars.SUPABASE_URL;
  // VS Code extension runs on the client side, so prefer the anon key.
  const key = vars.SUPABASE_ANON_KEY || vars.SUPABASE_SERVICE_ROLE_KEY;

  let user = vars.DEVELOPER_ID || vars.USERNAME;
  if (!user) {
    try {
      const { execSync } = require("child_process");
      const gitName = execSync("git config user.name", {
        cwd: workspaceRoot,
        encoding: "utf8",
        timeout: 3000,
      }).trim();
      if (gitName) user = gitName;
    } catch (e) {
      logToCollab(`Git config check failed: ${e.message}`, "DEBUG");
    }
  }

  if (!user) {
    user = os.userInfo().username || "unknown";
  }

  if (!url || !key) return null;
  const result = { url, key, user: user || "unknown" };
  logToCollab(`Extension config loaded. User: ${result.user}`);
  return result;
}

function getRelativeActivePath() {
  const editor = vscode.window.activeTextEditor;
  const workspaceRoot = getWorkspaceRoot();
  if (!editor || !workspaceRoot) return null;
  return path
    .relative(workspaceRoot, editor.document.uri.fsPath)
    .replace(/\\/g, "/");
}

// =========================================================================
// Status Bar
// =========================================================================
async function updateStatusBar() {
  if (!statusBarItem) return;
  if (!supabaseClient) return;

  const activeFile = getRelativeActivePath();
  if (!activeFile) {
    statusBarItem.text = "$(unlock) Locks";
    statusBarItem.tooltip = "No file open";
    return;
  }

  try {
    const { data, error } = await supabaseClient
      .from("file_locks")
      .select("*")
      .eq("file_path", activeFile)
      .limit(1);

    logToCollab(`Status bar check for ${activeFile}: found ${data ? data.length : 0} locks. Error: ${error ? JSON.stringify(error) : "none"}`);

    if (error || !data || data.length === 0) {
      statusBarItem.text = "$(unlock) Unlocked";
      statusBarItem.tooltip = `${activeFile} is not locked`;
      return;
    }

    const lock = data[0];
    const config = loadConfig();
    const isMine = config && lock.developer_id === config.user;
    if (isMine) {
      statusBarItem.text = "$(lock) You";
      statusBarItem.tooltip = `You hold the lock on ${activeFile}`;
    } else {
      statusBarItem.text = `$(warning) Locked: @${lock.developer_id}`;
      statusBarItem.tooltip =
        `${activeFile} is locked by @${lock.developer_id}` +
        (lock.acquired_at
          ? ` since ${new Date(lock.acquired_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
          : "");
    }
  } catch (e) {
    statusBarItem.text = "$(unlock) Locks";
    logToCollab(`Status bar update failed: ${e.message}`, "DEBUG");
  }
}

// =========================================================================
// Lock-on-open notification
// =========================================================================
async function checkLockOnFileOpen() {
  if (!supabaseClient) return;
  const activeFile = getRelativeActivePath();
  if (!activeFile) return;

  try {
    const { data, error } = await supabaseClient
      .from("file_locks")
      .select("*")
      .eq("file_path", activeFile)
      .limit(1);

    if (error || !data || data.length === 0) return;
    const lock = data[0];
    const config = loadConfig();
    if (config && lock.developer_id === config.user) return;

    const lockTime = lock.acquired_at
      ? new Date(lock.acquired_at).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        })
      : null;

    vscode.window
      .showWarningMessage(
        `🔒 ${activeFile} is locked by @${lock.developer_id}` +
          (lockTime ? ` since ${lockTime}` : "") +
          ". Editing may cause conflicts.",
        "Open Dashboard",
        "Show Locks",
      )
      .then((selection) => {
        if (selection === "Open Dashboard") cmdOpenDashboard();
        else if (selection === "Show Locks") cmdShowAll();
      });
  } catch (e) {
    logToCollab(`Open check failed: ${e.message}`, "DEBUG");
  }
}

// =========================================================================
// Realtime subscription
// =========================================================================
function subscribeToChanges() {
  if (!supabaseClient) return;
  try {
    logToCollab("Initializing Supabase Realtime subscription...");
    currentSubscription = supabaseClient
      .channel("vscode-locks")
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "file_locks" },
        (payload) => {
          logToCollab(`Realtime event received: ${payload.eventType} on ${payload.new?.file_path || "unknown"}`);
          updateStatusBar();
          if (
            payload.eventType === "INSERT" ||
            payload.eventType === "UPDATE"
          ) {
            const newLock = payload.new;
            const config = loadConfig();
            if (newLock && config && newLock.developer_id !== config.user) {
              const activeFile = getRelativeActivePath();
              if (activeFile && newLock.file_path === activeFile) {
                const lockTime = newLock.acquired_at
                  ? new Date(newLock.acquired_at).toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                    })
                  : null;
                vscode.window
                  .showWarningMessage(
                    `🔒 ${newLock.file_path} is locked by @${newLock.developer_id}` +
                      (lockTime ? ` since ${lockTime}` : "") +
                      ". Editing may cause conflicts.",
                    "Open Dashboard",
                    "Show Locks",
                  )
                  .then((selection) => {
                    if (selection === "Open Dashboard") cmdOpenDashboard();
                    else if (selection === "Show Locks") cmdShowAll();
                  });
              }
            }
          }
        },
      )
      .subscribe((status) => {
        logToCollab(`Realtime subscription status: ${status}`);
      });
  } catch (e) {
    logToCollab(`Realtime subscription failed: ${e.message}`, "DEBUG");
  }
}

// =========================================================================
// Startup summary notification (log-parse path)
// =========================================================================
let collectingSummary = false;
let summaryBuffer = [];
let summaryTimeout = null;

function handleStartupSummaryFromWatcher(msg) {
  collectingSummary = true;
  summaryBuffer = [msg];
  if (summaryTimeout) clearTimeout(summaryTimeout);
  summaryTimeout = setTimeout(() => showStartupNotification(), 300);
}

function processStartupSummaryLine(msg) {
  if (!collectingSummary) return;
  if (
    msg.match(/Re-adopted:\s+\d+/) ||
    msg.match(/Stale released:\s+\d+/) ||
    msg.match(/Newly locked:\s+\d+/) ||
    msg.match(/Conflicts:\s+\d+/)
  ) {
    summaryBuffer.push(msg);
  } else {
    collectingSummary = false;
    if (summaryTimeout) {
      clearTimeout(summaryTimeout);
      summaryTimeout = null;
    }
    showStartupNotification();
  }
}

function showStartupNotification() {
  if (summaryBuffer.length === 0) return;

  const stats = {
    readopted: 0,
    staleReleased: 0,
    newlyLocked: 0,
    conflicts: 0,
  };
  for (const line of summaryBuffer) {
    const m1 = line.match(/Re-adopted:\s+(\d+)/);
    const m2 = line.match(/Stale released:\s+(\d+)/);
    const m3 = line.match(/Newly locked:\s+(\d+)/);
    const m4 = line.match(/Conflicts:\s+(\d+)/);
    if (m1) stats.readopted = parseInt(m1[1], 10);
    if (m2) stats.staleReleased = parseInt(m2[1], 10);
    if (m3) stats.newlyLocked = parseInt(m3[1], 10);
    if (m4) stats.conflicts = parseInt(m4[1], 10);
  }

  const msg =
    `Collab Locks — Startup Summary\n\n` +
    `Re-adopted: ${stats.readopted} lock(s)\n` +
    `Stale released: ${stats.staleReleased} lock(s)\n` +
    `Newly locked: ${stats.newlyLocked} file(s)\n` +
    `Conflicts: ${stats.conflicts} file(s)`;

  try {
    showStartupNotificationOnce(msg, JSON.stringify(stats));
  } catch (e) {
    try {
      vscode.window.showInformationMessage(msg);
    } catch (e2) {
      logToCollab(`Fallback notification failed: ${e2.message}`, "DEBUG");
    }
  }

  logToCollab(`Summary collection complete: ${summaryBuffer.length} items.`);
  collectingSummary = false;
  summaryBuffer = [];
  summaryTimeout = null;
}

/**
 * Parse a CONFLICT line from watcher stdout and show a warning popup.
 */
function handleConflictFromWatcher(msg) {
  const match = msg.match(/CONFLICT:\s+(.+?)\s+is locked by\s+@(\S+)/);
  if (!match) return;

  const filePath = match[1];
  const owner = match[2];

  vscode.window
    .showWarningMessage(
      `🔒 ${filePath} is locked by @${owner}. Your changes may cause a merge conflict.`,
      "Open Dashboard",
      "Show Details",
    )
    .then((selection) => {
      if (selection === "Open Dashboard") cmdOpenDashboard();
      else if (selection === "Show Details" && outputChannel)
        outputChannel.show(true);
    });
}

/**
 * Global poller for startup summaries.
 * Catches summaries from both extension-started and CLI-started watchers.
 */
function startGlobalSummaryPoller(workspaceRoot) {
  if (summaryPoller) return;
  const stateDir = getStateDir(workspaceRoot);

  summaryPoller = setInterval(() => {
    try {
      const stateSummary = path.join(stateDir, ".startup_summary.json");
      const repoSummary = path.join(workspaceRoot, ".startup_summary.json");
      let summaryFile = fs.existsSync(stateSummary)
        ? stateSummary
        : (fs.existsSync(repoSummary) ? repoSummary : null);

      if (summaryFile) {
        const data = JSON.parse(fs.readFileSync(summaryFile, "utf8"));
        const msg = `Collab Locks — Startup Summary: [` +
                    ` Re-adopted: ${data.readopted || 0} lock(s) |` +
                    ` Stale released: ${data.stale_released || 0} lock(s) |` +
                    ` Newly locked: ${data.newly_locked || 0} file(s) |` +
                    ` Conflicts: ${data.conflicts || 0} file(s) ]`;

        showStartupNotificationOnce(msg, JSON.stringify(data));

        try {
          fs.unlinkSync(summaryFile);
        } catch (e) {
          logToCollab(`Failed to delete summary file: ${e.message}`, "DEBUG");
        }
      }
    } catch (e) {
      logToCollab(`Global summary poller tick failed: ${e.message}`, "DEBUG");
    }
  }, 2000);
}

// =========================================================================
// Watcher management
// =========================================================================
function startWatcher() {
  const workspaceRoot = getWorkspaceRoot();
  if (!workspaceRoot) return;

  if (watcherProcess) {
    if (outputChannel)
      outputChannel.appendLine(`[collab] Watcher already running (PID: ${watcherProcess.pid}); skipping start.`);
    return;
  }

  if (outputChannel) {
    outputChannel.appendLine(`[collab] VS Code extension starting...`);
    outputChannel.appendLine(`[collab] Workspace: ${workspaceRoot}`);
  }

  const stateDir = getStateDir(workspaceRoot);
  const pidFile = path.join(stateDir, ".daemon.pid");

  // Detect installed collab runtime
  const collabRuntime = detectCollab(workspaceRoot);
  if (!collabRuntime.command) {
    if (outputChannel) {
      outputChannel.appendLine(
        `[collab] ERROR: ${collabRuntime.error}`,
      );
    }
    vscode.window.showErrorMessage(
      `Collab Runtime requires the Python package to be installed.`,
      "Install via pip",
      "View Setup Guide",
    ).then((selection) => {
      if (selection === "Install via pip") {
        const terminal = vscode.window.createTerminal("Collab Setup");
        terminal.show();
        terminal.sendText("pip install collab-runtime");
      } else if (selection === "View Setup Guide") {
        vscode.env.openExternal(
          vscode.Uri.parse("https://github.com/KirilMT/collab"),
        );
      }
    });
    return;
  }

  if (outputChannel && collabRuntime.version) {
    outputChannel.appendLine(
      `[collab] Using collab runtime: ${collabRuntime.version}`,
    );
  }

  // Defensive cleanup: stale marker files can survive abrupt reloads and cause
  // a freshly spawned watcher to immediately self-stop.
  try {
    const staleStopFile = path.join(stateDir, ".stop_request");
    const staleShutdownFile = path.join(stateDir, ".shutdown_complete");
    if (fs.existsSync(staleStopFile)) {
      fs.unlinkSync(staleStopFile);
      if (outputChannel)
        outputChannel.appendLine(
          `[collab] Removed stale stop request before startup`,
        );
    }
    if (fs.existsSync(staleShutdownFile)) {
      fs.unlinkSync(staleShutdownFile);
      if (outputChannel)
        outputChannel.appendLine(
          `[collab] Removed stale shutdown marker before startup`,
        );
    }
  } catch (e) {
    if (outputChannel)
      outputChannel.appendLine(
        `[collab] WARNING: failed stale marker cleanup: ${e.message}`,
      );
  }

  // Gracefully stop any existing watcher from a previous session.
  if (fs.existsSync(pidFile)) {
    try {
      const pidData = fs.readFileSync(pidFile, "utf-8").trim();
      let pid = null;
      let stopPayload = null;
      if (pidData.startsWith("{")) {
        try {
          const meta = JSON.parse(pidData);
          pid = meta.pid;
          if (meta && typeof meta.token === "string" && meta.token.trim()) {
            stopPayload = `TOKEN:${meta.token.trim()}`;
          }
        } catch (e) {
          logToCollab(`Failed to parse PID metadata: ${e.message}`, "DEBUG");
        }
      } else {
        pid = parseInt(pidData, 10);
      }
      if (pid && !stopPayload) stopPayload = `PID:${pid}`;

      if (pid) {
        if (outputChannel)
          outputChannel.appendLine(
            `[collab] Cleaning up existing watcher (PID: ${pid})`,
          );

        const stopFile = path.join(stateDir, ".stop_request");
        const shutdownFile = path.join(stateDir, ".shutdown_complete");

        const isAlive = (p) => {
          try {
            if (process.platform === "win32") {
              execSync(`tasklist /FI "PID eq ${p}"`, { stdio: "ignore" });
              return true;
            } else {
              process.kill(p, 0);
              return true;
            }
          } catch (e) {
            logToCollab(`isAlive check failed for ${p}: ${e.message}`, "DEBUG");
            return false;
          }
        };

        try {
          const fd = fs.openSync(stopFile, "w");
          try {
            fs.writeSync(fd, stopPayload || `PID:${pid}`);
            try {
              fs.fsyncSync(fd);
            } catch (e) {
              logToCollab(`fsync failed for stop_request: ${e.message}`, "DEBUG");
            }
          } finally {
            try {
              fs.closeSync(fd);
            } catch (e) {
              logToCollab(`close failed for stop_request: ${e.message}`, "DEBUG");
            }
          }
        } catch (err) {
          if (outputChannel)
            outputChannel.appendLine(
              `[collab] Failed to write stop_request: ${err.message}`,
            );
          logToCollab(`Failed to write stop_request: ${err.message}`, "WARN");
        }

        if (outputChannel)
          outputChannel.appendLine(
            `[collab] Wrote stop request payload: ${stopPayload || `PID:${pid}`}`,
          );

        let waited = 0;
        let graceful = false;
        while (waited < 8000) {
          if (!isAlive(pid)) {
            graceful = true;
            break;
          }
          if (fs.existsSync(shutdownFile)) {
            graceful = true;
            break;
          }
          try {
            if (process.platform === "win32") {
              execSync("ping 127.0.0.1 -n 1 > nul", { stdio: "ignore" });
              waited += 200;
            } else {
              execSync("sleep 0.2", { stdio: "ignore" });
              waited += 200;
            }
          } catch (e) {
            logToCollab(`Ping wait failed: ${e.message}`, "DEBUG");
            waited += 200;
          }
        }

        if (graceful) {
          if (outputChannel)
            outputChannel.appendLine(
              `[collab] Previous watcher stopped gracefully`,
            );
          try {
            if (fs.existsSync(shutdownFile)) fs.unlinkSync(shutdownFile);
          } catch (e) {
            logToCollab(`Failed to unlink shutdown file: ${e.message}`, "DEBUG");
          }
        } else {
          if (outputChannel)
            outputChannel.appendLine(
              `[collab] Force-killing stale watcher PID ${pid}`,
            );
          try {
            if (process.platform === "win32") {
              execSync(`taskkill /F /T /PID ${pid}`, { stdio: "ignore" });
            } else {
              process.kill(-pid, "SIGKILL");
            }
          } catch (e) {
            logToCollab(`Failed to force-kill stale watcher: ${e.message}`, "DEBUG");
          }
        }

        try {
          if (fs.existsSync(stopFile)) fs.unlinkSync(stopFile);
        } catch (e) {
          logToCollab(`Failed to unlink stop request file: ${e.message}`, "DEBUG");
        }

        try {
          if (fs.existsSync(pidFile)) fs.unlinkSync(pidFile);
        } catch (e) {
          logToCollab(`Failed to unlink pid file: ${e.message}`, "DEBUG");
        }
      }
    } catch (e) {
      logToCollab(`Global cleanup error in startWatcher: ${e.message}`, "WARN");
    }
  }

  /* const pythonCmd = getPythonCommand(workspaceRoot); */
  const _pythonCmd = "python";
  const parentPid = getVSCodeWindowPid() || process.pid;

  if (outputChannel)
    outputChannel.appendLine(
      `[collab] Spawning watcher (parent PID: ${parentPid})`,
    );

  try {
    const heartbeatFile = path.join(stateDir, ".vscode_heartbeat");
    fs.mkdirSync(stateDir, { recursive: true });
    fs.writeFileSync(heartbeatFile, `${Date.now()}\n`, { encoding: "utf8" });

    const args = [
      "watch",
      "--interval",
      "5",
      "--timeout",
      "0",
      "--parent-pid",
      String(parentPid),
      "--heartbeat-file",
      heartbeatFile,
      "--heartbeat-grace-seconds",
      "20",
      "--pid-file",
      path.join(stateDir, ".daemon.pid"),
    ];

    logToCollab(`Spawning watcher: ${collabRuntime.command} ${args.join(" ")}`);

    watcherProcess = spawn(
      collabRuntime.command,
      args,
      {
        cwd: workspaceRoot,
        stdio: ["ignore", "pipe", "pipe"],
        detached: false,
      },
    );

    watcherHeartbeatFile = heartbeatFile;
    if (watcherHeartbeatInterval) clearInterval(watcherHeartbeatInterval);
    watcherHeartbeatInterval = setInterval(() => {
      try {
        if (watcherHeartbeatFile) {
          fs.writeFileSync(watcherHeartbeatFile, `${Date.now()}\n`, {
            encoding: "utf8",
          });
          watcherHeartbeatTicks++;
          if (outputChannel && watcherHeartbeatTicks % 5 === 0)
            outputChannel.appendLine(
              `[collab] Heartbeat (${watcherHeartbeatTicks})`,
            );
        }
      } catch (e) {
        logToCollab(`Heartbeat tick failed: ${e.message}`, "DEBUG");
      }
    }, 2000);

    if (outputChannel)
      outputChannel.appendLine(
        `[collab] Watcher spawned (PID: ${watcherProcess.pid})`,
      );

    if (watcherProcess.stdout) {
      watcherProcess.stdout.on("data", (data) => {
        const msg = data.toString().trim();
        if (!msg) return;
        if (outputChannel) outputChannel.appendLine(msg);
        if (msg.includes("CONFLICT")) handleConflictFromWatcher(msg);
        if (msg.includes("Startup reconciliation complete"))
          handleStartupSummaryFromWatcher(msg);
        processStartupSummaryLine(msg);
      });
    }
    if (watcherProcess.stderr) {
      watcherProcess.stderr.on("data", (data) => {
        const msg = data.toString().trim();
        if (msg && outputChannel) outputChannel.appendLine(`[ERR] ${msg}`);
      });
    }

    watcherProcess.on("error", (_err) => {
      watcherProcess = null;
      scheduleWatcherRestart(`error: ${_err?.message || "unknown"}`);
    });

    watcherProcess.on("exit", (code, signal) => {
      watcherProcess = null;
      scheduleWatcherRestart(
        `exit code=${code ?? "null"} signal=${signal ?? "null"}`,
      );
    });
  } catch (e) {
    if (outputChannel)
      outputChannel.appendLine(
        `[collab] Failed to spawn watcher: ${e.message}`,
      );
    watcherProcess = null;
  }
}

// =========================================================================
// Commands
// =========================================================================
async function cmdShowAll() {
  if (!supabaseClient) {
    vscode.window.showErrorMessage("Collab Locks: Not connected to Supabase.");
    return;
  }

  const { data, error } = await supabaseClient
    .from("file_locks")
    .select("*")
    .order("acquired_at", { ascending: false });

  if (error) {
    vscode.window.showErrorMessage(`Collab Locks: ${error.message}`);
    return;
  }

  if (!data || data.length === 0) {
    vscode.window.showInformationMessage("No active file locks.");
    return;
  }

  const items = data.map((lock) => ({
    label: `$(lock) ${lock.file_path}`,
    description: `@${lock.developer_id}`,
    detail: `Branch: ${lock.branch_name || "N/A"} | Reason: ${lock.reason || "N/A"}`,
  }));

  vscode.window.showQuickPick(items, {
    placeHolder: `${data.length} active lock(s)`,
    canPickMany: false,
  });
}

async function cmdReleaseAll() {
  if (!supabaseClient) {
    vscode.window.showErrorMessage("Collab Locks: Not connected to Supabase.");
    return;
  }

  const config = loadConfig();
  if (!config) return;

  const { data, error } = await supabaseClient
    .from("file_locks")
    .delete()
    .eq("developer_id", config.user)
    .select();

  if (error) {
    vscode.window.showErrorMessage(`Release failed: ${error.message}`);
    return;
  }

  const count = data ? data.length : 0;
  vscode.window.showInformationMessage(`Released ${count} lock(s).`);
  updateStatusBar();
}

async function cmdDebugCheck() {
  const config = loadConfig();
  const workspaceRoot = getWorkspaceRoot();
  const activeFile = getRelativeActivePath();

  if (!config || !workspaceRoot || !activeFile) {
    vscode.window.showErrorMessage(`Debug Check Failed: Config=${!!config}, Root=${!!workspaceRoot}, File=${!!activeFile}`);
    return;
  }

  try {
    const { data, error } = await supabaseClient
      .from("file_locks")
      .select("*")
      .eq("file_path", activeFile);

    const msg = `Collab Debug:\nFile: ${activeFile}\nUser: ${config.user}\nLocks Found: ${data ? data.length : 0}\nError: ${error ? error.message : "none"}`;
    vscode.window.showInformationMessage(msg);
  } catch (err) {
    vscode.window.showErrorMessage(`Debug Check Error: ${err.message}`);
  }
}

async function cmdOpenDashboard() {
  const workspaceRoot = getWorkspaceRoot();
  if (!workspaceRoot) return;

  const collabRuntime = detectCollab(workspaceRoot);
  if (!collabRuntime.command) {
    vscode.window.showErrorMessage(
      `Cannot open dashboard: ${collabRuntime.error || "Collab runtime not found"}`,
    );
    return;
  }

  const terminal = vscode.window.createTerminal("Collab Dashboard");
  terminal.sendText(`${collabRuntime.command} dashboard`);
  terminal.show();
}

// =========================================================================
// Activation
// =========================================================================
function activate(context) {
  isDeactivating = false;
  watcherRestartAttempts = 0;
  if (watcherRestartTimer) {
    clearTimeout(watcherRestartTimer);
    watcherRestartTimer = null;
  }

  outputChannel = vscode.window.createOutputChannel("Collab Locks");
  context.subscriptions.push(outputChannel);

  statusBarItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    100,
  );
  statusBarItem.text = "$(unlock) Locks";
  statusBarItem.tooltip = "Collaborative File Locks — initializing...";
  statusBarItem.command = "collabLocks.showAll";
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  // Register commands unconditionally so status bar is always clickable.
  context.subscriptions.push(
    vscode.commands.registerCommand("collabLocks.showAll", cmdShowAll),
    vscode.commands.registerCommand("collabLocks.releaseAll", cmdReleaseAll),
    vscode.commands.registerCommand("collabLocks.debugCheck", cmdDebugCheck),
    vscode.commands.registerCommand(
      "collabLocks.openDashboard",
      cmdOpenDashboard,
    ),
  );

  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(() => {
      updateStatusBar();
      checkLockOnFileOpen();
    }),
  );

  const workspaceRoot = getWorkspaceRoot();
  if (!workspaceRoot) {
    statusBarItem.text = "$(warning) Locks: No Workspace";
    return;
  }

  const config = loadConfig();
  if (!config) {
    statusBarItem.text = "$(warning) Locks: No Config";
    statusBarItem.tooltip = "Missing Supabase credentials in .env";
    vscode.window
      .showInformationMessage(
        "Collab Locks: Supabase credentials not found in .env. " +
          "See editors/vscode/collab-locks/README.md.",
        "Open Setup Guide",
      )
      .then((selection) => {
        if (selection === "Open Setup Guide") {
          const readmePath = path.join(
            workspaceRoot,
            "editors",
            "vscode",
            "collab-locks",
            "README.md",
          );
          if (fs.existsSync(readmePath)) {
            vscode.workspace
              .openTextDocument(readmePath)
              .then((doc) => vscode.window.showTextDocument(doc));
          }
        }
      });
    return;
  }

  try {
    const { createClient } = require("@supabase/supabase-js");
    supabaseClient = createClient(config.url, config.key);
  } catch (e) {
    statusBarItem.text = "$(warning) Locks: SDK Error";
    statusBarItem.tooltip =
      "Failed to initialize Supabase client. Run npm install in editors/vscode/collab-locks/";
    if (outputChannel)
      outputChannel.appendLine(`[collab] SDK init failed: ${e.message}`);
    return;
  }

  // Check runtime availability before starting watcher
  const collabRuntime = detectCollab(workspaceRoot);
  if (!collabRuntime.command) {
    statusBarItem.text = "$(warning) Locks: Setup Required";
    statusBarItem.tooltip = collabRuntime.error || "Collab runtime not found";
    vscode.window
      .showWarningMessage(
        "Collab Runtime Not Found",
        collabRuntime.error ||
          "The collab package is not installed. See setup guide.",
        "View Setup Guide",
      )
      .then((selection) => {
        if (selection === "View Setup Guide") {
          vscode.env.openExternal(
            vscode.Uri.parse(
              "https://github.com/KirilMT/collab/blob/main/README.md",
            ),
          );
        }
      });
    if (outputChannel)
      outputChannel.appendLine(
        `[collab] Runtime check failed: ${collabRuntime.error}`,
      );
    logToCollab(`Runtime check failed: ${collabRuntime.error}`, "ERROR");
    return;
  }

  // Clear any stale stop request on activation so we start fresh
  try {
    const stateDir = getStateDir(workspaceRoot);
    const stopFile = path.join(stateDir, ".stop_request");
    if (fs.existsSync(stopFile)) {
      fs.unlinkSync(stopFile);
      if (outputChannel) outputChannel.appendLine(`[collab] Cleared stale stop request on activation.`);
    }
  } catch (e) {
    logToCollab(`Failed to clear stale stop request: ${e.message}`, "WARN");
  }

  startWatcher();
  startGlobalSummaryPoller(workspaceRoot);
  subscribeToChanges();

  updateStatusBar();
  checkLockOnFileOpen();
}

// =========================================================================
// Deactivation
// =========================================================================
function deactivate() {
  isDeactivating = true;
  if (watcherRestartTimer) {
    clearTimeout(watcherRestartTimer);
    watcherRestartTimer = null;
  }

  const workspaceRoot = getWorkspaceRoot();
  const stateDir = workspaceRoot ? getStateDir(workspaceRoot) : os.tmpdir();
  const pidFile = workspaceRoot ? path.join(stateDir, ".daemon.pid") : null;

  if (outputChannel)
    outputChannel.appendLine(`[collab] VS Code deactivating — cleaning up...`);

  let pid = watcherProcess ? watcherProcess.pid : null;
  let stopPayload = pid ? `PID:${pid}` : null;

  if (pidFile && fs.existsSync(pidFile)) {
    try {
      const pidData = fs.readFileSync(pidFile, "utf-8").trim();
      if (pidData.startsWith("{")) {
        const meta = JSON.parse(pidData);
        if (meta && Number.isInteger(meta.pid) && meta.pid > 0) {
          pid = meta.pid;
        }
        if (meta && typeof meta.token === "string" && meta.token.trim()) {
          stopPayload = `TOKEN:${meta.token.trim()}`;
        }
      } else {
        const parsed = parseInt(pidData, 10);
        if (parsed > 0) {
          pid = parsed;
          stopPayload = `PID:${parsed}`;
        }
      }
    } catch (err) {
      if (outputChannel)
        outputChannel.appendLine(
          `[collab] WARNING: failed to parse PID metadata: ${err.message}`,
        );
    }
  }

  if (pid) {
    const stopFile = path.join(stateDir, ".stop_request");
    const shutdownFile = path.join(stateDir, ".shutdown_complete");

    if (outputChannel)
      outputChannel.appendLine(
        `[collab] Found watcher PID for shutdown: ${pid}`,
      );

    const isAlive = (p) => {
      try {
        if (process.platform === "win32") {
          execSync(`tasklist /FI "PID eq ${p}"`, { stdio: "ignore" });
          return true;
        } else {
          process.kill(p, 0);
          return true;
        }
      } catch (e) {
        logToCollab(`isAlive check failed for ${p} on deactivation: ${e.message}`, "DEBUG");
        return false;
      }
    };

    if (outputChannel)
      outputChannel.appendLine(
        `[collab] Gracefully terminating watcher (PID: ${pid})...`,
      );

    try {
      const fd = fs.openSync(stopFile, "w");
      try {
        fs.writeSync(fd, stopPayload || `PID:${pid}`);
        try {
          fs.fsyncSync(fd);
        } catch (err) {
          logToCollab(`fsync failed on deactivation: ${err.message}`, "DEBUG");
        }
      } finally {
        try {
          fs.closeSync(fd);
        } catch (err) {
          logToCollab(`close failed on deactivation: ${err.message}`, "DEBUG");
        }
      }
    } catch (err) {
      if (outputChannel)
        outputChannel.appendLine(
          `[collab] Failed to write stop_request: ${err.message}`,
        );
      logToCollab(`Failed to write stop_request: ${err.message}`, "WARN");
    }

    if (outputChannel)
      outputChannel.appendLine(
        `[collab] Wrote stop request payload: ${stopPayload || `PID:${pid}`}`,
      );

    if (outputChannel) outputChannel.appendLine(`[collab] Waiting for graceful shutdown...`);

    let waited = 0;
    while (waited < 10000 && isAlive(pid)) {
      if (fs.existsSync(shutdownFile)) {
        if (outputChannel)
          outputChannel.appendLine(`[collab] Shutdown signal detected after ${waited}ms`);
        break;
      }
      try {
        if (process.platform === "win32") {
          execSync("ping 127.0.0.1 -n 1 > nul", { stdio: "ignore" });
          waited += 200;
        } else {
          execSync("sleep 0.2", { stdio: "ignore" });
          waited += 200;
        }
      } catch (e) {
        logToCollab(`Ping wait failed on deactivation: ${e.message}`, "DEBUG");
        waited += 200;
      }

      // Log progress every 2 seconds
      if (waited > 0 && waited % 2000 === 0) {
        if (outputChannel)
          outputChannel.appendLine(
            `[collab] Still waiting for graceful shutdown... (${waited}ms elapsed)`,
          );
      }
    }

    if (fs.existsSync(shutdownFile)) {
      try {
        const kept = fs.readFileSync(shutdownFile, "utf8").trim();
        if (outputChannel)
          outputChannel.appendLine(
            `[collab] Watcher shutdown detected: ${kept} locks kept`,
          );
        fs.unlinkSync(shutdownFile);
      } catch (err) {
        logToCollab(`Failed to handle shutdown file: ${err.message}`, "DEBUG");
      }
    }

    if (isAlive(pid)) {
      if (outputChannel)
        outputChannel.appendLine(
          `[collab] Watcher still alive after ${waited}ms, force killing (PID: ${pid})`,
        );
      try {
        if (process.platform === "win32") {
          execSync(`taskkill /F /T /PID ${pid}`, { stdio: "ignore" });
        } else {
          process.kill(-pid, "SIGKILL");
        }
      } catch (e) {
        logToCollab(`Force-kill failed: ${e.message}`, "DEBUG");
      }
    } else {
      if (outputChannel)
        outputChannel.appendLine(`[collab] Watcher shut down gracefully`);
    }

    try {
      if (fs.existsSync(stopFile)) fs.unlinkSync(stopFile);
      if (outputChannel) outputChannel.appendLine(`[collab] Cleaned up stop request file`);
    } catch (e) {
      logToCollab(`Failed to cleanup stop file: ${e.message}`, "DEBUG");
    }
    watcherProcess = null;
  } else if (outputChannel) {
    outputChannel.appendLine(
      `[collab] No watcher PID found during deactivation; skipping watcher stop`,
    );
  }

  if (watcherHeartbeatInterval) {
    try {
      clearInterval(watcherHeartbeatInterval);
    } catch (e) {
      logToCollab(`Failed to clear heartbeat interval: ${e.message}`, "DEBUG");
    }
    watcherHeartbeatInterval = null;
  }
  if (watcherHeartbeatFile) {
    try {
      if (fs.existsSync(watcherHeartbeatFile)) fs.unlinkSync(watcherHeartbeatFile);
      if (outputChannel) outputChannel.appendLine(`[collab] Cleaned up heartbeat file`);
    } catch (e) {
      logToCollab(`Failed to cleanup heartbeat file: ${e.message}`, "DEBUG");
    }
    watcherHeartbeatFile = null;
  }

  if (currentSubscription) {
    try {
      currentSubscription.unsubscribe();
      if (outputChannel) outputChannel.appendLine(`[collab] Unsubscribed from Realtime`);
    } catch (e) {
      logToCollab(`Failed to unsubscribe: ${e.message}`, "DEBUG");
    }
    currentSubscription = null;
  }

  // Smart shutdown: release only locks for files that are clean in git.
  // Keep locks alive for files still being edited (dirty in git status).
  if (supabaseClient && workspaceRoot) {
    const config = loadConfig();
    if (config && config.user) {
      let dirtyFiles = new Set();
      let gitFailed = false;
      try {
        const gitOut = execSync("git status --porcelain", {
          cwd: workspaceRoot,
          timeout: 3000,
          encoding: "utf8",
        }).trim();
        if (gitOut) {
          for (const line of gitOut.split("\n")) {
            if (line.length > 3) {
              let fp = line.substring(3).trim();
              if (fp.includes(" -> ")) fp = fp.split(" -> ").pop().trim();
              if (fp.startsWith('"') && fp.endsWith('"'))
                fp = fp.slice(1, -1);
              dirtyFiles.add(fp);
            }
          }
        }
      } catch (e) {
        gitFailed = true;
        if (outputChannel)
          outputChannel.appendLine(
            `[collab] WARNING: git status failed during shutdown: ${e.message}`,
          );
      }

      if (!gitFailed) {
        supabaseClient
          .from("file_locks")
          .select("file_path")
          .eq("developer_id", config.user)
          .then((res) => {
            const locks = res?.data || [];
            let nKept = 0;
            let nReleased = 0;
            const releasePromises = [];
            for (const lock of locks) {
              const fp = lock.file_path;
              if (fp && !dirtyFiles.has(fp)) {
                nReleased++;
                releasePromises.push(
                  supabaseClient
                    .from("file_locks")
                    .delete()
                    .eq("file_path", fp)
                    .eq("developer_id", config.user)
                    .then(() => {})
                    .catch(() => {}),
                );
              } else if (fp) {
                nKept++;
              }
            }
            if (outputChannel)
              outputChannel.appendLine(
                `[collab] Shutdown: kept ${nKept} lock(s), released ${nReleased} lock(s).`,
              );
            return Promise.all(releasePromises);
          })
          .catch(() => {});
      } else {
        if (outputChannel)
          outputChannel.appendLine(
            "[collab] All locks preserved (git status unavailable).",
          );
      }
    }
  }

  if (outputChannel)
    outputChannel.appendLine(`[collab] VS Code deactivation complete`);
}

module.exports = { activate, deactivate };
