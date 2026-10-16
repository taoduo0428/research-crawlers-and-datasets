"use strict";

const BRIDGE_ORIGIN = "http://127.0.0.1:8765";
const STORAGE_KEY = "liepinEdgeBridgeStateV1";
const TOKEN_STORAGE_KEY = "liepinBridgeRuntimeToken";
const WATCHDOG_ALARM = "liepin-edge-bridge-watchdog";
const EMPTY_QUEUE_DELAY_MS = 2_000;
const BRIDGE_ERROR_DELAY_MS = 15_000;
const RESULT_RETRY_LIMIT = 20;
const WORKER_IDS = Object.freeze([
  "browser_forward",
  "api_reverse",
  "camoufox_reverse_2"
]);
const ALLOWED_STATUSES = new Set([
  "success",
  "unavailable",
  "security_transit",
  "login_required",
  "empty",
  "error"
]);
const SLOW_STATUSES = new Set([
  "security_transit",
  "login_required",
  "error"
]);

const app = {
  booted: false,
  bridgeToken: "",
  authBlocked: false,
  saveTail: Promise.resolve(),
  workers: new Map()
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function randomSuccessDelay() {
  return 300 + Math.floor(Math.random() * 401);
}

function newWorker(id) {
  return {
    id,
    tabId: null,
    task: null,
    pendingResult: null,
    resultAttempts: 0,
    requesting: false,
    reporting: false,
    retired: false,
    halted: false,
    timer: null,
    navigationTimer: null
  };
}

function storedWorker(worker) {
  return {
    id: worker.id,
    tabId: Number.isInteger(worker.tabId) ? worker.tabId : null,
    task: worker.task || null,
    pendingResult: worker.pendingResult || null,
    resultAttempts: Number.isInteger(worker.resultAttempts)
      ? worker.resultAttempts
      : 0,
    retired: Boolean(worker.retired),
    halted: Boolean(worker.halted)
  };
}

function saveState() {
  const write = app.saveTail.then(async () => {
    const workers = {};
    for (const [id, worker] of app.workers) {
      workers[id] = storedWorker(worker);
    }
    await chrome.storage.session.set({
      [STORAGE_KEY]: {
        workers
      }
    });
  });
  app.saveTail = write.catch(() => undefined);
  return write;
}

async function loadState() {
  const stored = (await chrome.storage.session.get(STORAGE_KEY))[STORAGE_KEY];
  if (!stored || typeof stored !== "object") {
    return;
  }
  if (!stored.workers || typeof stored.workers !== "object") {
    return;
  }
  for (const [id, value] of Object.entries(stored.workers)) {
    if (!WORKER_IDS.includes(id) || !value) {
      continue;
    }
    const worker = newWorker(id);
    worker.tabId = Number.isInteger(value.tabId) ? value.tabId : null;
    worker.task = value.task || null;
    worker.pendingResult = value.pendingResult || null;
    worker.resultAttempts = Number.isInteger(value.resultAttempts)
      ? value.resultAttempts
      : 0;
    worker.retired = Boolean(value.retired);
    worker.halted = Boolean(value.halted);
    app.workers.set(id, worker);
  }
}

async function fetchWithTimeout(path, options = {}, timeoutMs = 5_000) {
  if (!app.bridgeToken) {
    throw new Error("bridge runtime token is not configured");
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${BRIDGE_ORIGIN}${path}`, {
      ...options,
      headers: {
        ...(options.headers || {}),
        "X-Bridge-Token": app.bridgeToken
      },
      cache: "no-store",
      signal: controller.signal
    });
    if (response.status === 401) {
      app.authBlocked = true;
      throw new Error("bridge authentication rejected");
    }
    if (!response.ok) {
      throw new Error(`bridge HTTP ${response.status}`);
    }
    return response;
  } finally {
    clearTimeout(timer);
  }
}

async function fetchJson(path) {
  const response = await fetchWithTimeout(path, {
    headers: { Accept: "application/json" }
  });
  return response.json();
}

async function loadBridgeToken() {
  try {
    const response = await fetch(
      chrome.runtime.getURL(".runtime/bridge-config.json"),
      { cache: "no-store" }
    );
    if (response.ok) {
      const runtime = await response.json();
      const token = typeof runtime.token === "string" ? runtime.token.trim() : "";
      if (runtime.bridge_origin === BRIDGE_ORIGIN && token) {
        app.bridgeToken = token;
        app.authBlocked = false;
        return;
      }
    }
  } catch (_error) {
    // Manual settings remain available when no generated runtime config exists.
  }
  const stored = await chrome.storage.local.get(TOKEN_STORAGE_KEY);
  const value = stored[TOKEN_STORAGE_KEY];
  app.bridgeToken = typeof value === "string" ? value.trim() : "";
  app.authBlocked = false;
}

function isAllowedTaskUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    return url.protocol === "https:" && url.hostname === "www.liepin.com";
  } catch (_error) {
    return false;
  }
}

function normalizeTask(raw) {
  if (!raw || typeof raw !== "object") {
    throw new Error("bridge task is not an object");
  }
  const recordId = String(raw.record_id ?? "").trim();
  const detailUrl = String(raw.detail_url ?? "").trim();
  const sourceIndex = Number(raw.source_index);
  if (!recordId || !Number.isInteger(sourceIndex) || !isAllowedTaskUrl(detailUrl)) {
    throw new Error("bridge task is missing record_id, source_index or a valid detail_url");
  }
  return {
    record_id: recordId,
    source_index: sourceIndex,
    detail_url: detailUrl,
    title: typeof raw.title === "string" ? raw.title.trim() : ""
  };
}

async function reconcileWorkers() {
  const wanted = new Set(WORKER_IDS);
  for (const id of WORKER_IDS) {
    let worker = app.workers.get(id);
    if (!worker) {
      worker = newWorker(id);
      app.workers.set(id, worker);
    }
    worker.retired = false;
  }

  for (const worker of app.workers.values()) {
    if (!wanted.has(worker.id)) {
      worker.retired = true;
      if (!worker.task && !worker.pendingResult) {
        await retireWorker(worker);
      }
    }
  }

  for (const id of wanted) {
    scheduleWorker(id, 0);
  }
}

async function retireWorker(worker) {
  clearWorkerTimers(worker);
  if (Number.isInteger(worker.tabId)) {
    try {
      await chrome.tabs.remove(worker.tabId);
    } catch (_error) {
      // The tab may already have been closed by the user.
    }
  }
  app.workers.delete(worker.id);
  await saveState();
}

function clearWorkerTimers(worker) {
  if (worker.timer) {
    clearTimeout(worker.timer);
    worker.timer = null;
  }
  if (worker.navigationTimer) {
    clearTimeout(worker.navigationTimer);
    worker.navigationTimer = null;
  }
}

function scheduleWorker(id, delayMs) {
  const worker = app.workers.get(id);
  if (!worker || worker.halted) {
    return;
  }
  if (worker.timer) {
    clearTimeout(worker.timer);
  }
  worker.timer = setTimeout(() => {
    worker.timer = null;
    void driveWorker(id);
  }, Math.max(0, delayMs));
}

async function getExistingTab(tabId) {
  if (!Number.isInteger(tabId)) {
    return null;
  }
  try {
    return await chrome.tabs.get(tabId);
  } catch (_error) {
    return null;
  }
}

async function navigateWorker(worker) {
  if (!worker.task) {
    return;
  }
  const existing = await getExistingTab(worker.tabId);
  if (existing) {
    await chrome.tabs.update(existing.id, {
      url: worker.task.detail_url,
      active: false
    });
  } else {
    const tab = await chrome.tabs.create({
      url: worker.task.detail_url,
      active: false
    });
    worker.tabId = tab.id;
    await saveState();
  }

  if (worker.navigationTimer) {
    clearTimeout(worker.navigationTimer);
  }
  worker.navigationTimer = setTimeout(() => {
    worker.navigationTimer = null;
    void queueResult(worker, {
      status: "error",
      jd: "",
      final_url: worker.task ? worker.task.detail_url : "",
      title: worker.task ? worker.task.title : ""
    });
  }, 20_000);
}

async function requestTask(worker) {
  const raw = await fetchJson(`/next?worker=${encodeURIComponent(worker.id)}`);
  if (!raw || typeof raw !== "object" || !("task" in raw)) {
    throw new Error("bridge /task returned invalid JSON");
  }
  if (raw.task === null) {
    scheduleWorker(worker.id, EMPTY_QUEUE_DELAY_MS);
    return;
  }
  worker.task = normalizeTask(raw.task);
  if (raw.task.worker && raw.task.worker !== worker.id) {
    throw new Error("bridge assigned a task to the wrong worker");
  }
  worker.resultAttempts = 0;
  await saveState();
  await navigateWorker(worker);
}

async function driveWorker(id) {
  const worker = app.workers.get(id);
  if (!worker || worker.requesting || worker.reporting || worker.halted) {
    return;
  }
  if (worker.pendingResult) {
    void deliverPendingResult(worker);
    return;
  }
  if (worker.task) {
    try {
      await navigateWorker(worker);
    } catch (error) {
      console.error(`[${worker.id}] navigation failed`, error);
      await queueResult(worker, {
        status: "error",
        jd: "",
        final_url: worker.task.detail_url,
        title: worker.task.title
      });
    }
    return;
  }
  if (worker.retired) {
    await retireWorker(worker);
    return;
  }

  worker.requesting = true;
  try {
    if (!app.bridgeToken || app.authBlocked) {
      scheduleWorker(worker.id, BRIDGE_ERROR_DELAY_MS);
      return;
    }
    await requestTask(worker);
  } catch (error) {
    console.error(`[${worker.id}] task request failed`, error);
    scheduleWorker(worker.id, BRIDGE_ERROR_DELAY_MS);
  } finally {
    worker.requesting = false;
  }
}

function sanitizedContentResult(worker, raw) {
  let status = String(raw.status || "error");
  if (!ALLOWED_STATUSES.has(status)) {
    status = "error";
  }
  let jd = typeof raw.jd === "string" ? raw.jd.trim() : "";
  if (status === "success" && !jd) {
    status = "empty";
  }
  if (status !== "success") {
    jd = "";
  }
  return {
    worker: worker.id,
    record_id: worker.task.record_id,
    source_index: worker.task.source_index,
    detail_url: worker.task.detail_url,
    status,
    jd,
    final_url: typeof raw.final_url === "string" && raw.final_url
      ? raw.final_url
      : worker.task.detail_url,
    title: typeof raw.title === "string" && raw.title.trim()
      ? raw.title.trim()
      : worker.task.title
  };
}

async function queueResult(worker, raw) {
  if (!worker || !worker.task) {
    return;
  }
  if (worker.pendingResult) {
    return;
  }
  if (worker.navigationTimer) {
    clearTimeout(worker.navigationTimer);
    worker.navigationTimer = null;
  }
  worker.pendingResult = sanitizedContentResult(worker, raw);
  worker.resultAttempts = 0;
  await saveState();
  scheduleWorker(worker.id, 0);
}

async function postResult(payload) {
  await fetchWithTimeout("/result", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
}

async function deliverPendingResult(worker) {
  if (!worker.pendingResult || worker.reporting || worker.halted) {
    return;
  }
  worker.reporting = true;
  try {
    while (worker.pendingResult && worker.resultAttempts < RESULT_RETRY_LIMIT) {
      worker.resultAttempts += 1;
      await saveState();
      try {
        await postResult(worker.pendingResult);
        const completedStatus = worker.pendingResult.status;
        worker.pendingResult = null;
        worker.task = null;
        worker.resultAttempts = 0;
        await saveState();
        if (worker.retired) {
          await retireWorker(worker);
        } else {
          const delay = SLOW_STATUSES.has(completedStatus)
            ? BRIDGE_ERROR_DELAY_MS
            : randomSuccessDelay();
          scheduleWorker(worker.id, delay);
        }
        return;
      } catch (error) {
        console.error(
          `[${worker.id}] result delivery attempt ${worker.resultAttempts} failed`,
          error
        );
        if (worker.resultAttempts < RESULT_RETRY_LIMIT) {
          const delay = Math.min(
            BRIDGE_ERROR_DELAY_MS,
            500 * (2 ** Math.min(worker.resultAttempts - 1, 5))
          );
          await sleep(delay);
        }
      }
    }
    worker.halted = true;
    await saveState();
    console.error(
      `[${worker.id}] halted after ${RESULT_RETRY_LIMIT} result delivery failures`
    );
  } finally {
    worker.reporting = false;
  }
}

function findWorkerByTab(tabId) {
  for (const worker of app.workers.values()) {
    if (worker.tabId === tabId) {
      return worker;
    }
  }
  return null;
}

function classifyRedirect(urlValue) {
  try {
    const hostname = new URL(urlValue).hostname.toLowerCase();
    if (hostname === "passport.liepin.com") {
      return "login_required";
    }
    if (hostname === "wow.liepin.com" || hostname === "safe.liepin.com") {
      return "security_transit";
    }
    if (hostname !== "www.liepin.com") {
      return "error";
    }
  } catch (_error) {
    return "error";
  }
  return null;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message && message.type === "liepin_bridge_token_updated") {
    void loadBridgeToken()
      .then(async () => {
        await reconcileWorkers();
        sendResponse({ accepted: Boolean(app.bridgeToken) });
      })
      .catch(() => sendResponse({ accepted: false }));
    return true;
  }
  if (!message || message.type !== "liepin_detail_result" || !sender.tab) {
    return false;
  }
  const worker = findWorkerByTab(sender.tab.id);
  if (!worker || !worker.task) {
    sendResponse({ accepted: false, reason: "tab has no assigned task" });
    return false;
  }
  void queueResult(worker, message)
    .then(() => sendResponse({ accepted: true }))
    .catch((error) => {
      console.error(`[${worker.id}] failed to queue content result`, error);
      sendResponse({ accepted: false, reason: "failed to persist result" });
    });
  return true;
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  const worker = findWorkerByTab(tabId);
  if (!worker || !worker.task || worker.pendingResult) {
    return;
  }
  const currentUrl = changeInfo.url || tab.url || "";
  if (currentUrl) {
    const redirectStatus = classifyRedirect(currentUrl);
    if (redirectStatus) {
      void queueResult(worker, {
        status: redirectStatus,
        jd: "",
        final_url: currentUrl,
        title: worker.task.title
      });
      return;
    }
  }
  if (changeInfo.status === "complete") {
    void chrome.tabs.sendMessage(tabId, { type: "liepin_extract_now" })
      .catch(() => {
        // The declarative content script also starts extraction by itself.
      });
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  const worker = findWorkerByTab(tabId);
  if (!worker) {
    return;
  }
  worker.tabId = null;
  if (worker.task && !worker.pendingResult) {
    void queueResult(worker, {
      status: "error",
      jd: "",
      final_url: worker.task.detail_url,
      title: worker.task.title
    });
  } else {
    void saveState();
  }
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== WATCHDOG_ALARM) {
    return;
  }
  for (const worker of app.workers.values()) {
    if (!worker.halted) {
      scheduleWorker(worker.id, 0);
    }
  }
});

chrome.runtime.onInstalled.addListener(() => {
  void boot();
});

chrome.runtime.onStartup.addListener(() => {
  void boot();
});

async function boot() {
  if (app.booted) {
    return;
  }
  app.booted = true;
  await loadState();
  await loadBridgeToken();
  for (const worker of app.workers.values()) {
    worker.requesting = false;
    worker.reporting = false;
    if (worker.pendingResult && worker.resultAttempts < RESULT_RETRY_LIMIT) {
      worker.halted = false;
    }
  }
  await chrome.alarms.create(WATCHDOG_ALARM, { periodInMinutes: 0.5 });
  await reconcileWorkers();
  for (const worker of app.workers.values()) {
    scheduleWorker(worker.id, 0);
  }
}

void boot();
