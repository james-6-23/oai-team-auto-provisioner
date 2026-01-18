function $(id) {
  return document.getElementById(id);
}

function setHidden(el, hidden) {
  el.classList.toggle("hidden", hidden);
}

const THEME_KEY = "oai-theme";

function getThemeMode() {
  try {
    const v = localStorage.getItem(THEME_KEY);
    if (v === "light" || v === "dark") return v;
  } catch (_e) {
    // ignore
  }
  return "system";
}

function persistThemeMode(mode) {
  try {
    if (mode === "light" || mode === "dark") {
      localStorage.setItem(THEME_KEY, mode);
    } else {
      localStorage.removeItem(THEME_KEY);
    }
  } catch (_e) {
    // ignore
  }
}

function applyThemeMode(mode) {
  const root = document.documentElement;
  if (mode === "light") {
    root.setAttribute("data-theme", "light");
  } else if (mode === "dark") {
    root.setAttribute("data-theme", "dark");
  } else {
    root.removeAttribute("data-theme");
  }
}

function themeLabel(mode) {
  if (mode === "light") return "亮色";
  if (mode === "dark") return "深色";
  return "跟随系统";
}

function initThemeUI() {
  const btn = $("btn-theme");
  if (!btn) return;

  const mode = getThemeMode();
  applyThemeMode(mode);
  btn.textContent = `主题：${themeLabel(mode)}`;

  btn.addEventListener("click", () => {
    const current = getThemeMode();
    const next =
      current === "system" ? "dark" : current === "dark" ? "light" : "system";
    persistThemeMode(next);
    applyThemeMode(next);
    btn.textContent = `主题：${themeLabel(next)}`;
    toast(`主题已切换为：${themeLabel(next)}`, 1200);
  });
}

function toast(msg, ms = 2600) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  window.clearTimeout(toast._t);
  toast._t = window.setTimeout(() => el.classList.add("hidden"), ms);
}

function setPill(running) {
  const pill = $("status-pill");
  if (running) {
    pill.textContent = "运行中";
    pill.classList.remove("pill-idle");
    pill.classList.add("pill-running");
  } else {
    pill.textContent = "空闲";
    pill.classList.add("pill-idle");
    pill.classList.remove("pill-running");
  }
}

function setRunningUI(running) {
  const startBtn = $("btn-start");
  const stopBtn = $("btn-stop");

  startBtn.disabled = Boolean(running);
  stopBtn.disabled = !Boolean(running);

  document
    .querySelectorAll('input[name="mode"]')
    .forEach((el) => (el.disabled = Boolean(running)));

  // 仅锁定“运行参数”，配置编辑允许继续查看/修改（下次任务生效）
  $("team-index").disabled = Boolean(running);
  $("reg-count").disabled = Boolean(running);
  document
    .querySelectorAll('input[name="reg-source"]')
    .forEach((el) => (el.disabled = Boolean(running)));
}

function appendLog(text) {
  if (!text) return;
  const logEl = $("log");
  logEl.textContent += text;

  // 控制最大长度，避免长期运行导致内存膨胀
  const maxChars = 300_000;
  if (logEl.textContent.length > maxChars) {
    logEl.textContent = logEl.textContent.slice(-maxChars);
  }

  if ($("auto-scroll").checked) {
    logEl.scrollTop = logEl.scrollHeight;
  }
}

function switchTab(tab) {
  const tabs = ["logs", "config", "status", "data"];
  tabs.forEach((t) => {
    $(`tab-${t}`).classList.toggle("tab-active", tab === t);
    $(`view-${t}`).classList.toggle("view-active", tab === t);
  });
}

function getMode() {
  const el = document.querySelector('input[name="mode"]:checked');
  return el ? el.value : "all";
}

function getRegSource() {
  const el = document.querySelector('input[name="reg-source"]:checked');
  return el ? el.value : "domain";
}

function updateModeExtras() {
  const mode = getMode();
  setHidden($("mode-single"), mode !== "single");
  setHidden($("mode-register"), mode !== "register");
}

async function safeCall(fn, ...args) {
  try {
    const res = await fn(...args);
    if (!res || res.ok !== true) {
      const err = res && res.error ? res.error : "未知错误";
      throw new Error(err);
    }
    return res;
  } catch (e) {
    toast(String(e));
    throw e;
  }
}

async function loadFiles() {
  const res = await safeCall(window.pywebview.api.get_config);
  $("config-text").value = res.config_text || "";
  $("team-text").value = res.team_text || "";
  toast(res.exists ? "已加载已保存的配置" : "已加载示例模板（尚未保存）");
}

async function saveFiles() {
  const validated = await safeCall(
    window.pywebview.api.validate_and_format,
    $("config-text").value,
    $("team-text").value
  );
  $("config-text").value = validated.config_text || "";
  $("team-text").value = validated.team_text || "";

  await safeCall(window.pywebview.api.save_config, $("config-text").value, $("team-text").value);
  toast("校验通过，并已保存到程序内部配置");
}

async function createFromExample() {
  const res = await safeCall(window.pywebview.api.create_from_example, false);
  const summary = (res.results || [])
    .map((x) => `${x.name}: ${x.status}`)
    .join("\n");
  toast(`完成：\n${summary || "无变更"}`);
  await loadFiles();
}

async function startTask() {
  const mode = getMode();
  const params = {};
  if (mode === "single") {
    params.team_index = Number($("team-index").value || 0);
  }
  if (mode === "register") {
    params.count = Number($("reg-count").value || 1);
    params.email_source = getRegSource();
  }

  await safeCall(window.pywebview.api.start_task, mode, params);
  setPill(true);
  setRunningUI(true);
  switchTab("logs");
  toast("任务已启动");
}

async function stopTask() {
  await safeCall(window.pywebview.api.stop_task);
  toast("已发送停止请求（会在下一边界生效）");
}

async function pollLoop() {
  if (pollLoop._busy) return;
  pollLoop._busy = true;
  try {
    const res = await window.pywebview.api.poll_logs(400);
    if (!res || res.ok !== true) {
      const err = res && res.error ? res.error : "日志拉取失败";
      throw new Error(err);
    }
    appendLog(res.text || "");
    const running = Boolean(res.running);
    setPill(running);
    setRunningUI(running);
    pollLoop._fail = 0;
  } catch (_e) {
    pollLoop._fail = (pollLoop._fail || 0) + 1;
    // 避免每 500ms 都弹窗：首次/每 10 次提示一次
    if (pollLoop._fail === 1 || pollLoop._fail % 10 === 0) {
      toast(`日志轮询异常：${String(_e)}`);
    }
  } finally {
    pollLoop._busy = false;
  }
}

async function copyText(text) {
  const value = text || "";
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(value);
    return;
  }

  // file:// 场景下 Clipboard API 常不可用，这里做降级复制
  const ta = document.createElement("textarea");
  ta.value = value;
  ta.setAttribute("readonly", "true");
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  ta.style.top = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(ta);
  if (!ok) {
    throw new Error("复制失败（浏览器限制）");
  }
}

function findInLog(backwards) {
  const term = ($("log-search").value || "").trim();
  if (!term) {
    toast("请输入搜索关键字");
    return;
  }

  // 使用浏览器内置查找（WebView2/Chromium）
  const ok = window.find(term, false, Boolean(backwards), true, false, false, false);
  if (!ok) {
    toast("未找到匹配内容");
  }
}

function buildStatusText(data) {
  if (!data || data.ok !== true) return "";
  if (data.exists !== true) {
    return `暂无追踪记录\nstorage: ${data.tracker_path || ""}\n`;
  }

  const t = data.totals || {};
  let out =
    `tracker: ${data.tracker_path}\n` +
    `last_updated: ${data.last_updated || "N/A"}\n` +
    `总计: ${t.accounts || 0}, 完成: ${t.completed || 0}, 未完成: ${t.incomplete || 0}\n\n`;

  (data.teams || []).forEach((team) => {
    out += `[TEAM] ${team.team}\n`;
    out += `total=${team.total}, completed=${team.completed}, incomplete=${team.incomplete}\n`;
    const sc = team.status_count || {};
    out += `status_count=${JSON.stringify(sc)}\n`;
    const inc = team.incomplete_accounts || [];
    if (inc.length) {
      out += `incomplete(${inc.length}):\n`;
      inc.slice(0, 30).forEach((a) => {
        out += `- ${a.email} (${a.status})\n`;
      });
      if (inc.length > 30) out += `... 还有 ${inc.length - 30} 条\n`;
    }
    out += "\n";
  });

  return out.trim() + "\n";
}

function escapeHtml(s) {
  return String(s || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderStatus(data) {
  const sumEl = $("status-summary");
  const teamsEl = $("status-teams");
  teamsEl.innerHTML = "";

  if (!data || data.ok !== true) {
    sumEl.textContent = (data && data.error) || "状态获取失败";
    return;
  }

  if (data.exists !== true) {
    sumEl.innerHTML =
      `<div class="hint">暂无追踪记录</div>` +
      `<div class="hint">storage：<code>${escapeHtml(data.tracker_path || "")}</code></div>`;
    return;
  }

  const t = data.totals || {};
  sumEl.innerHTML =
    `<div class="stat-grid">` +
    `<div class="stat"><div class="stat-k">总计账号</div><div class="stat-v">${escapeHtml(
      t.accounts
    )}</div></div>` +
    `<div class="stat"><div class="stat-k">已完成</div><div class="stat-v ok">${escapeHtml(
      t.completed
    )}</div></div>` +
    `<div class="stat"><div class="stat-k">未完成</div><div class="stat-v warn">${escapeHtml(
      t.incomplete
    )}</div></div>` +
    `</div>` +
    `<div class="hint status-hint">tracker：<code>${escapeHtml(
      data.tracker_path
    )}</code> · last_updated：<code>${escapeHtml(data.last_updated || "N/A")}</code></div>`;

  const cards = (data.teams || []).map((team) => {
    const sc = team.status_count || {};
    const scLines = Object.keys(sc)
      .sort()
      .map((k) => `${k}: ${sc[k]}`)
      .join(" · ");
    const inc = team.incomplete_accounts || [];
    const incLines = inc
      .slice(0, 12)
      .map((a) => `<li><code>${escapeHtml(a.email)}</code> <span class="muted">(${escapeHtml(a.status)})</span></li>`)
      .join("");
    const more = inc.length > 12 ? `<div class="hint">... 还有 ${inc.length - 12} 条未展示</div>` : "";

    return (
      `<div class="team-card">` +
      `<div class="team-head">` +
      `<div class="team-name">${escapeHtml(team.team)}</div>` +
      `<div class="team-meta"><span class="muted">total</span> ${escapeHtml(
        team.total
      )} · <span class="muted">done</span> <span class="ok">${escapeHtml(
        team.completed
      )}</span> · <span class="muted">todo</span> <span class="warn">${escapeHtml(
        team.incomplete
      )}</span></div>` +
      `</div>` +
      `<div class="hint">${escapeHtml(scLines || "")}</div>` +
      (inc.length
        ? `<ul class="inc-list">${incLines}</ul>${more}`
        : `<div class="hint">无未完成账号</div>`) +
      `</div>`
    );
  });

  teamsEl.innerHTML = cards.join("");
}

async function refreshStatus() {
  const data = await safeCall(window.pywebview.api.get_status_summary);
  refreshStatus._last = data;
  renderStatus(data);
  toast("状态已刷新", 1200);
}

function buildAccountsText(rows) {
  const list = rows || [];
  if (!list.length) return "暂无账号记录\n";

  let out = "created_at\tteam\tstatus\temail\tpassword\tcrs_id\n";
  list.forEach((r) => {
    out += `${r.created_at || ""}\t${r.team || ""}\t${r.status || ""}\t${r.email || ""}\t${r.password || ""}\t${
      r.crs_id || ""
    }\n`;
  });
  return out;
}

function buildCredentialsText(rows) {
  const list = rows || [];
  if (!list.length) return "暂无凭据记录\n";

  let out = "created_at\tsource\temail\tpassword\n";
  list.forEach((r) => {
    out += `${r.created_at || ""}\t${r.source || ""}\t${r.email || ""}\t${r.password || ""}\n`;
  });
  return out;
}

function renderData(data) {
  const sumEl = $("data-summary");
  const accEl = $("data-accounts");
  const credEl = $("data-credentials");

  if (!data || data.ok !== true) {
    sumEl.textContent = (data && data.error) || "数据获取失败";
    accEl.textContent = "";
    credEl.textContent = "";
    return;
  }

  const c = data.counts || {};
  sumEl.innerHTML =
    `<div class="stat-grid">` +
    `<div class="stat"><div class="stat-k">账号记录</div><div class="stat-v">${escapeHtml(
      c.accounts || 0
    )}</div></div>` +
    `<div class="stat"><div class="stat-k">凭据记录</div><div class="stat-v">${escapeHtml(
      c.credentials || 0
    )}</div></div>` +
    `<div class="stat"><div class="stat-k">追踪更新时间</div><div class="stat-v">${escapeHtml(
      data.last_updated || "N/A"
    )}</div></div>` +
    `</div>` +
    `<div class="hint status-hint">storage：<code>${escapeHtml(data.db_path || "")}</code> · 导出位置：<code>工作目录/exports</code></div>`;

  accEl.textContent = buildAccountsText(data.accounts || []);
  credEl.textContent = buildCredentialsText(data.credentials || []);
}

async function refreshData() {
  const data = await safeCall(window.pywebview.api.get_output_overview, 50, 50);
  refreshData._last = data;
  renderData(data);
  toast("数据已刷新", 1200);
}

function wireUi(paths) {
  document
    .querySelectorAll('input[name="mode"]')
    .forEach((el) => el.addEventListener("change", updateModeExtras));
  document
    .querySelectorAll('input[name="reg-source"]')
    .forEach((el) => el.addEventListener("change", updateModeExtras));
  updateModeExtras();
  setRunningUI(false);
  initThemeUI();

  $("tab-logs").addEventListener("click", () => switchTab("logs"));
  $("tab-config").addEventListener("click", () => switchTab("config"));
  $("tab-status").addEventListener("click", () => switchTab("status"));
  $("tab-data").addEventListener("click", async () => {
    switchTab("data");
    await refreshData();
  });

  $("btn-load").addEventListener("click", loadFiles);
  $("btn-save").addEventListener("click", saveFiles);
  $("btn-from-example").addEventListener("click", createFromExample);

  $("btn-start").addEventListener("click", startTask);
  $("btn-stop").addEventListener("click", stopTask);

  $("btn-clear-log").addEventListener("click", async () => {
    await safeCall(window.pywebview.api.clear_logs);
    $("log").textContent = "";
    toast("已清空日志");
  });

  $("btn-copy-log").addEventListener("click", async () => {
    try {
      await copyText($("log").textContent || "");
      toast("已复制到剪贴板");
    } catch (e) {
      toast(`复制失败: ${e}`);
    }
  });

  $("btn-export-log").addEventListener("click", async () => {
    const res = await safeCall(window.pywebview.api.export_log, $("log").textContent || "");
    toast(`已导出：${res.filename}`);
    await safeCall(window.pywebview.api.open_path, res.filename);
  });

  $("log-search").addEventListener("keydown", (e) => {
    if (e.key === "Enter") findInLog(false);
  });
  $("btn-search-next").addEventListener("click", () => findInLog(false));
  $("btn-search-prev").addEventListener("click", () => findInLog(true));

  $("btn-open-workdir").addEventListener("click", () =>
    safeCall(window.pywebview.api.open_path, ".")
  );
  $("btn-open-data").addEventListener("click", async () => {
    switchTab("data");
    await refreshData();
  });

  const hint = $("paths-hint");
  hint.textContent =
    `工作目录：${paths.work_dir}\n` +
    `配置存储：${paths.config_storage}\n` +
    `输出存储：${paths.output_storage}\n` +
    `db：${paths.db_path}`;

  $("btn-refresh-status").addEventListener("click", refreshStatus);
  $("btn-copy-status").addEventListener("click", async () => {
    const txt = buildStatusText(refreshStatus._last);
    await copyText(txt);
    toast("已复制摘要");
  });

  $("btn-refresh-data").addEventListener("click", refreshData);
  $("btn-export-accounts").addEventListener("click", async () => {
    const res = await safeCall(window.pywebview.api.export_accounts_csv);
    toast(`已导出：${res.filename}`);
    await safeCall(window.pywebview.api.open_path, res.filename);
  });
  $("btn-export-credentials").addEventListener("click", async () => {
    const res = await safeCall(window.pywebview.api.export_created_credentials_csv);
    toast(`已导出：${res.filename}`);
    await safeCall(window.pywebview.api.open_path, res.filename);
  });
  $("btn-export-tracker").addEventListener("click", async () => {
    const res = await safeCall(window.pywebview.api.export_team_tracker_json);
    toast(`已导出：${res.filename}`);
    await safeCall(window.pywebview.api.open_path, res.filename);
  });
}

window.addEventListener("pywebviewready", async () => {
  try {
    await safeCall(window.pywebview.api.ping);
    const paths = await safeCall(window.pywebview.api.get_paths);
    wireUi(paths);
    await loadFiles();
    await refreshStatus();
    await refreshData();

    setInterval(pollLoop, 500);
  } catch (_e) {
    // safeCall 已 toast
  }
});
