"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  batchId: localStorage.getItem("facebookCaptureBatchId") || "",
  jobId: localStorage.getItem("facebookCaptureJobId") || "",
  selectedJobId: localStorage.getItem("facebookCaptureSelectedJobId") || "",
  mode: "",
  pollTimer: null,
  toastTimer: null,
};

const jobLabels = {
  queued: "排队中", running: "抓取中", completed: "已完成",
  cancelling: "停止中", completed_with_errors: "部分完成", failed: "失败", cancelled: "已停止",
};
const stageStateLabels = { waiting: "等待", active: "进行中", complete: "完成", warning: "注意", error: "失败" };
const artifactStateLabels = { waiting: "等待", writing: "写入中", ready: "已落盘", missing: "缺失", discarding: "不保留", not_saved: "未保存" };
const terminalBatchStates = new Set(["completed", "completed_with_errors"]);
const terminalJobStates = new Set(["completed", "failed", "cancelled"]);

const defaultStages = [
  ["environment", "模拟器与网络", "ADB 设备和 Android 网络就绪"],
  ["pcap", "原始 PCAP", "tcpdump 保存 TLS 解密前流量"],
  ["proxy", "代理与 MITM", "ADB reverse 与 mitmproxy 实时接收流量"],
  ["target", "目标帖子校验", "只允许匹配目标帖的后续 Reaction 抓取"],
  ["pagination", "Reaction 分页", "ALL connection 持续增长直到 terminal"],
  ["decrypt", "解密 flow 校验", "MITM archive 与安全索引完成校验"],
  ["analysis", "解析与去重", "GraphQL 节点标准化并输出 CSV"],
  ["complete", "证据链完成", "解析结果与所选证据均已处理"],
].map(([id, label, detail]) => ({ id, label, detail, state: "waiting" }));

function showToast(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove("show"), 1800);
}

function statusClass(status) {
  if (status === "running" || status === "cancelling") return "active";
  if (status === "completed") return "complete";
  if (status === "failed") return "error";
  if (status === "cancelled" || status === "completed_with_errors") return "warning";
  return "waiting";
}

function setJobBadge(status) {
  const badge = $("jobBadge");
  badge.className = `job-badge ${status || "idle"}`;
  badge.textContent = jobLabels[status] || "未运行";
  const active = status === "queued" || status === "running" || status === "cancelling";
  $("startButton").disabled = active;
  $("cancelButton").disabled = !active;
  $("postUrls").disabled = active;
  document.querySelectorAll('input[name="saveArtifact"]').forEach((input) => { input.disabled = active; });
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "—";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const amount = bytes / (1024 ** index);
  return `${amount.toFixed(index === 0 ? 0 : amount >= 10 ? 1 : 2)} ${units[index]}`;
}

function createPill(stateName, labelMap, labelKey = stateName) {
  const span = document.createElement("span");
  span.className = `state-pill ${stateName}`;
  span.textContent = labelMap[labelKey] || labelKey;
  return span;
}

function displayPostUrl(value) {
  try {
    const parsed = new URL(value);
    return `${parsed.hostname.replace(/^www\./, "")}${parsed.pathname}${parsed.search}`;
  } catch {
    return value;
  }
}

function renderStages(stages) {
  const list = $("stageList");
  list.replaceChildren();
  let completed = "等待任务";
  let active = "";
  stages.forEach((stage, index) => {
    const item = document.createElement("li");
    item.className = `stage-item ${stage.state}`;
    const number = document.createElement("span");
    number.className = "stage-number";
    number.textContent = String(index + 1).padStart(2, "0");
    const copy = document.createElement("div");
    copy.className = "stage-copy";
    const title = document.createElement("strong");
    title.textContent = stage.label;
    const detail = document.createElement("span");
    detail.textContent = stage.detail;
    copy.append(title, detail);
    item.append(number, copy, createPill(stage.state, stageStateLabels));
    list.append(item);
    if (stage.state === "complete") completed = stage.label;
    if (!active && ["active", "error", "warning"].includes(stage.state)) active = stage.label;
  });
  $("currentStage").textContent = active || completed;
}

function artifactLink(url, text, download = false) {
  const link = document.createElement("a");
  link.className = "text-link";
  link.href = download ? `${url}?download=1` : url;
  link.textContent = text;
  if (!download) { link.target = "_blank"; link.rel = "noopener"; }
  return link;
}

function renderArtifacts(artifacts) {
  const rows = $("artifactRows");
  rows.replaceChildren();
  if (!artifacts.length) {
    const row = document.createElement("tr");
    row.className = "empty-row";
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.textContent = "选择帖子后显示产物";
    row.append(cell);
    rows.append(row);
    return;
  }
  artifacts.forEach((artifact) => {
    const row = document.createElement("tr");
    const layer = document.createElement("td"); layer.textContent = artifact.layer;
    const name = document.createElement("td");
    const nameCode = document.createElement("code"); nameCode.textContent = artifact.name;
    const nameLabel = document.createElement("span"); nameLabel.className = "artifact-label";
    nameLabel.textContent = `${artifact.label || ""}${artifact.required ? " · 必留" : ""}`;
    name.append(nameCode, nameLabel);
    const status = document.createElement("td"); status.append(createPill(artifact.state, artifactStateLabels));
    const size = document.createElement("td"); size.textContent = formatBytes(artifact.size_bytes);
    const path = document.createElement("td");
    const pathCode = document.createElement("code"); pathCode.textContent = artifact.path; pathCode.title = artifact.path; path.append(pathCode);
    const action = document.createElement("td"); action.className = "artifact-actions";
    const jobId = state.selectedJobId || state.jobId;
    const url = String(artifact.url || "").replace("{job_id}", encodeURIComponent(jobId));
    if (!["waiting", "missing", "not_saved", "discarding"].includes(artifact.state) && artifact.size_bytes > 0) {
      if (artifact.viewable) action.append(artifactLink(url, "查看"));
      action.append(artifactLink(url, "下载", true));
    }
    const copy = document.createElement("button"); copy.type = "button"; copy.className = "text-button"; copy.textContent = "复制路径";
    copy.addEventListener("click", () => copyText(artifact.path)); action.append(copy);
    row.append(layer, name, status, size, path, action); rows.append(row);
  });
}

function renderProgress(progress) {
  $("storageRoot").textContent = progress.storage_root || "选择帖子后显示";
  $("copyRoot").disabled = !progress.storage_root;
  const metrics = progress.metrics || {};
  $("metricUsers").textContent = String(metrics.valid_reactor_users || metrics.reaction_users || 0);
  $("metricPages").textContent = String(metrics.reaction_pages || 0);
  $("metricAttempts").textContent = String(metrics.attempts || 0);
  $("metricGrowth").textContent = String(metrics.growth_events || 0);
  $("metricRateLimits").textContent = String(metrics.rate_limits || 0);
  $("metricTerminal").textContent = metrics.terminal ? "已到达" : "未到达";
  $("metricTerminal").style.color = metrics.terminal ? "var(--green)" : "var(--ink)";
  $("lastUpdated").textContent = metrics.updated_at ? `更新 ${new Date(metrics.updated_at).toLocaleTimeString()}` : (jobLabels[progress.job_status] || "等待网络记录");
  renderStages(progress.stages || defaultStages);
  renderArtifacts(progress.artifacts || []);
}

function renderBatchArtifacts(artifacts) {
  const container = $("batchArtifacts");
  container.replaceChildren();
  if (!artifacts.length) return;
  const caption = document.createElement("span"); caption.className = "artifact-caption"; caption.textContent = "批次汇总文件"; container.append(caption);
  artifacts.filter((artifact) => artifact.state === "ready").forEach((artifact) => {
    container.append(artifactLink(artifact.url, `${artifact.label} · ${formatBytes(artifact.size_bytes)}`, true));
  });
}

function renderBatch(batch) {
  setJobBadge(batch.status);
  if (batch.status === "completed" && (batch.posts || []).some(post => post.result_outcome?.code === "empty-lists")) {
    $("jobBadge").textContent = "已结束（含 Empty lists）";
  }
  const counts = batch.status_counts || {};
  $("batchTotal").textContent = String(batch.total_posts || 0);
  $("batchCompleted").textContent = String(counts.completed || 0);
  $("batchActive").textContent = String((counts.running || 0) + (counts.queued || 0) + (counts.cancelling || 0));
  $("batchFailed").textContent = String((counts.failed || 0) + (counts.cancelled || 0));
  $("batchUserSum").textContent = String(batch.per_post_valid_sum || 0);
  $("batchUniqueUsers").textContent = String(batch.cross_post_unique_users || 0);
  $("batchUpdated").textContent = batch.updated_at ? `更新 ${new Date(batch.updated_at).toLocaleTimeString()}` : "等待批次";
  const rows = $("batchRows"); rows.replaceChildren();
  (batch.posts || []).forEach((post) => {
    const row = document.createElement("tr"); if (post.job_id === state.selectedJobId) row.className = "selected";
    const index = document.createElement("td"); index.textContent = `#${String(post.index).padStart(2, "0")}`;
    const url = document.createElement("td");
    const select = document.createElement("button"); select.type = "button"; select.className = "post-select";
    select.textContent = displayPostUrl(post.post_url); select.title = post.post_url; select.addEventListener("click", () => selectJob(post.job_id)); url.append(select);
    const status = document.createElement("td");
    const emptyList = post.status === "completed" && post.result_outcome?.code === "empty-lists";
    status.append(createPill(emptyList ? "warning" : statusClass(post.status),
      emptyList ? { [post.status]: post.result_outcome.label } : jobLabels, post.status));
    const stage = document.createElement("td"); stage.textContent = post.current_stage;
    const users = document.createElement("td"); users.textContent = `有效 ${post.valid_users || 0}`;
    const pages = document.createElement("td"); pages.textContent = `${post.reaction_pages || 0} 页`;
    row.append(index, url, status, stage, users, pages); rows.append(row);
  });
  renderBatchArtifacts(batch.artifacts || []);
}

function renderLegacyBatch(job, progress) {
  const metrics = progress.metrics || {};
  renderBatch({
    status: job.status, updated_at: job.finished_at || job.started_at || job.created_at, total_posts: 1,
    status_counts: { [job.status]: 1 }, per_post_valid_sum: metrics.valid_reactor_users || metrics.reaction_users || 0,
    cross_post_unique_users: metrics.valid_reactor_users || metrics.reaction_users || 0,
    posts: [{ index: 1, job_id: job.job_id, post_url: (job.request || {}).post_url || "", status: job.status,
      current_stage: $("currentStage").textContent, result_outcome: progress.result_outcome,
      valid_users: metrics.valid_reactor_users || metrics.reaction_users || 0,
      reaction_pages: metrics.reaction_pages || 0 }], artifacts: [],
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
  return payload;
}

async function refreshHealth() {
  try {
    const health = await api("/health");
    $("serviceDot").className = "status-dot ok";
    $("serviceLabel").textContent = health.worker === "busy" ? "本地服务 · 忙碌" : "本地服务 · 就绪";
    const workers = health.workers || [];
    if (workers.length) {
      const stateLabels = { disabled: "未启用", idle: "空闲", busy: "抓取中", cooling: "冷却中", offline: "离线" };
      $("androidLabel").textContent = workers.map((worker) => {
        const android = worker.android || {};
        const online = android.state === "device" && android.boot_completed ? "在线" : (android.state || "未知");
        return `${worker.worker_id} · ${stateLabels[worker.state] || worker.state} · ${online}`;
      }).join(" ｜ ");
    } else {
      const android = health.android || {};
      $("androidLabel").textContent = android.state === "device" && android.boot_completed ? `模拟器 ${health.serial} · 在线` : `模拟器 ${health.serial} · ${android.state || "未知"}`;
    }
  } catch {
    $("serviceDot").className = "status-dot error"; $("serviceLabel").textContent = "本地服务不可用"; $("androidLabel").textContent = "模拟器状态未知";
  }
}

async function selectJob(jobId) {
  state.selectedJobId = jobId;
  localStorage.setItem("facebookCaptureSelectedJobId", jobId);
  if (state.mode === "batch") await refreshBatch(); else await refreshLegacyJob();
}

async function refreshSelectedJob() {
  if (!state.selectedJobId) return;
  renderProgress(await api(`/v1/jobs/${encodeURIComponent(state.selectedJobId)}/progress`));
}

async function refreshBatch() {
  if (!state.batchId) return;
  try {
    const batch = await api(`/v1/batches/${encodeURIComponent(state.batchId)}/progress`);
    const posts = batch.posts || [];
    if (!posts.some((post) => post.job_id === state.selectedJobId)) {
      const preferred = posts.find((post) => post.status === "running" || post.status === "cancelling") || posts.find((post) => post.status === "queued") || posts[0];
      state.selectedJobId = preferred ? preferred.job_id : "";
      if (state.selectedJobId) localStorage.setItem("facebookCaptureSelectedJobId", state.selectedJobId);
    }
    renderBatch(batch);
    await refreshSelectedJob();
    if (terminalBatchStates.has(batch.status)) stopPolling();
  } catch (error) {
    $("formError").textContent = error.message;
    if (error.message.includes("not found")) { localStorage.removeItem("facebookCaptureBatchId"); state.batchId = ""; stopPolling(); }
  }
}

async function refreshLegacyJob() {
  if (!state.jobId) return;
  try {
    const [job, progress] = await Promise.all([api(`/v1/jobs/${encodeURIComponent(state.jobId)}`), api(`/v1/jobs/${encodeURIComponent(state.jobId)}/progress`)]);
    state.selectedJobId = state.jobId; renderProgress(progress); renderLegacyBatch(job, progress);
    if (job.error) $("formError").textContent = job.error;
    if (terminalJobStates.has(job.status)) stopPolling();
  } catch (error) { $("formError").textContent = error.message; }
}

function stopPolling() { clearInterval(state.pollTimer); state.pollTimer = null; }
function startPolling() {
  stopPolling();
  const refresh = state.mode === "batch" ? refreshBatch : refreshLegacyJob;
  refresh(); state.pollTimer = setInterval(refresh, 1000);
}

async function restoreLatest() {
  if (state.batchId) { state.mode = "batch"; startPolling(); return; }
  try {
    const batch = await api("/v1/batches/latest"); state.batchId = batch.batch_id; state.mode = "batch";
    localStorage.setItem("facebookCaptureBatchId", state.batchId); startPolling(); return;
  } catch { /* Fall back to historical single-post tasks. */ }
  try {
    const job = state.jobId ? await api(`/v1/jobs/${encodeURIComponent(state.jobId)}`) : await api("/v1/jobs/latest");
    state.jobId = job.job_id; state.mode = "legacy"; localStorage.setItem("facebookCaptureJobId", state.jobId); startPolling();
  } catch { setJobBadge(""); }
}

async function copyText(value) {
  try { await navigator.clipboard.writeText(value); showToast("路径已复制"); }
  catch { showToast("浏览器未允许复制，请手动选择路径"); }
}

function validFacebookUrl(value) {
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol) && (parsed.hostname === "facebook.com" || parsed.hostname.endsWith(".facebook.com"));
  } catch { return false; }
}

$("captureForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const postUrls = Array.from(new Set($("postUrls").value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean)));
  $("formError").textContent = "";
  if (!postUrls.length || postUrls.some((value) => !validFacebookUrl(value))) {
    $("formError").textContent = "请检查链接：每行只能填写一个有效的 Facebook 帖子链接"; return;
  }
  if (postUrls.length > 100) { $("formError").textContent = "单个批次最多支持 100 个不同帖子链接"; return; }
  try {
    setJobBadge("queued");
    const saveArtifacts = Array.from(document.querySelectorAll('input[name="saveArtifact"]:checked')).map((input) => input.value);
    const batch = await api("/v1/batches", { method: "POST", body: JSON.stringify({ post_urls: postUrls, collect: ["reactions"], save_artifacts: saveArtifacts }) });
    state.batchId = batch.batch_id; state.mode = "batch"; state.selectedJobId = (batch.posts[0] || {}).job_id || "";
    localStorage.setItem("facebookCaptureBatchId", state.batchId);
    if (state.selectedJobId) localStorage.setItem("facebookCaptureSelectedJobId", state.selectedJobId);
    showToast(`${postUrls.length} 个帖子已加入队列`); startPolling();
  } catch (error) { setJobBadge(""); $("formError").textContent = error.message; }
});

$("cancelButton").addEventListener("click", async () => {
  try {
    if (state.mode === "batch" && state.batchId) {
      await api(`/v1/batches/${encodeURIComponent(state.batchId)}/cancel`, { method: "POST" }); showToast("批次停止请求已发送"); await refreshBatch();
    } else if (state.jobId) {
      await api(`/v1/jobs/${encodeURIComponent(state.jobId)}/cancel`, { method: "POST" }); showToast("停止请求已发送"); await refreshLegacyJob();
    }
  } catch (error) { $("formError").textContent = error.message; }
});

$("copyRoot").addEventListener("click", () => copyText($("storageRoot").textContent));
api('/v1/capabilities').then((capabilities) => {
  if (!capabilities.capture_pcap_supported) {
    const pcap = document.querySelector('input[name="saveArtifact"][value="pcap"]');
    pcap.checked = false;
    pcap.disabled = true;
    pcap.parentElement.title = '当前 Mac 执行器支持 CSV、MITM 与进度，尚不支持 PCAP';
  }
}).catch(() => { /* Older API versions expose the original Windows controls. */ });
$("checkSetup").addEventListener("click", async () => {
  const button = $("checkSetup");
  button.disabled = true;
  $("setupStatus").textContent = "正在检查依赖、SDK、端口和网络配置…";
  try {
    const report = await api("/v1/setup");
    const failed = report.checks.filter((item) => !item.ok);
    $("setupStatus").textContent = failed.length
      ? failed.map((item) => `${item.name}: ${item.detail}`).join("；")
      : "本机环境检查通过。还需确认模拟器登录、证书与实际帖子抓取；这不是采集完成证明。";
  } catch (error) { $("setupStatus").textContent = error.message; }
  finally { button.disabled = false; }
});
setJobBadge(""); renderStages(defaultStages); renderArtifacts([]); refreshHealth(); setInterval(refreshHealth, 5000); restoreLatest();
