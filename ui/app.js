const CLIENT = {
  bilibili: {
    name: "哔哩哔哩客户端已通过调试端口启动",
    repair: "重启哔哩哔哩",
    confirm: "将关闭当前哔哩哔哩并重新打开",
  },
  mango: {
    name: "芒果TV客户端已通过调试端口启动",
    repair: "重启芒果TV",
    confirm: "将关闭当前芒果TV并重新打开",
  },
};

const SHARED = [
  { id: "mic", name: "麦克风设备已识别", repair: "重新检测" },
  { id: "perm", name: "麦克风权限可用", repair: "打开权限设置" },
  { id: "model", name: "语音识别模型已下载", repair: "重新下载" },
];

function itemsFor(platform) {
  return [{ id: "client", ...CLIENT[platform] }, ...SHARED];
}

function blankState(token = 0) {
  return { mode: "idle", busy: false, checksOpen: true, shown: [], results: {}, startedAt: 0, token };
}

const store = { bilibili: blankState(), mango: blankState() };
let view = "bilibili";

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const startBtn = document.getElementById("start");
const retryBtn = document.getElementById("retry");
const checksEl = document.getElementById("checks");
const confirmEl = document.getElementById("confirm");
const confirmOk = document.getElementById("confirm-ok");
const confirmCancel = document.getElementById("confirm-cancel");
const waveEl = document.getElementById("wave");
const captionEl = document.getElementById("caption");
const elapsedEl = document.getElementById("elapsed");
const inspectorEl = document.getElementById("inspector");
const foldBtn = document.getElementById("fold");
const waveLevels = Array(40).fill(0);
let wavePhase = 0;
let waveFrame = 0;
let clockTimer = 0;
let levelTimer = 0;
let permWatch = 0;

function state() {
  return store[view];
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, reduceMotion ? 0 : ms));
}

function icon(kind) {
  if (kind === "checking") return '<span class="spin"></span>';
  const file = kind === "pass" ? "icons/check.svg" : "icons/fail.svg";
  return `<img src="${file}" alt="">`;
}

function paint() {
  const current = state();
  const starting = current.busy && current.mode !== "started" && current.mode !== "failed";
  document.getElementById("start-action").textContent = "启动控制器";
  document.getElementById("start-state").textContent = current.mode === "started"
    ? "已启动"
    : current.mode === "failed"
      ? "启动失败"
      : starting
        ? "启动中"
        : "未启动";
  startBtn.classList.toggle("is-on", current.mode === "started");
  startBtn.classList.toggle("is-fail", current.mode === "failed");
  startBtn.classList.toggle("is-starting", starting);
  startBtn.setAttribute("aria-busy", starting ? "true" : "false");
  retryBtn.hidden = true;
  waveEl.hidden = current.mode === "idle";
  captionEl.hidden = current.mode !== "started";
  if (current.mode !== "started") captionEl.textContent = "";
  if (current.mode !== "idle" && !waveFrame) drawWave();
  if (current.mode === "idle" && waveFrame) {
    cancelAnimationFrame(waveFrame);
    waveFrame = 0;
  }
  elapsedEl.textContent = current.mode === "started"
    ? formatElapsed(Date.now() - current.startedAt)
    : "00:00:00";
  inspectorEl.classList.toggle("is-open", current.mode !== "idle");
  inspectorEl.classList.toggle("is-collapsed", !current.checksOpen);
  document.getElementById("inspector-clip").setAttribute("aria-hidden", current.checksOpen ? "false" : "true");
  foldBtn.classList.toggle("is-collapsed", !current.checksOpen);
  foldBtn.setAttribute("aria-expanded", current.checksOpen ? "true" : "false");
  foldBtn.setAttribute("aria-label", current.checksOpen ? "收起检测项" : "展开检测项");

  const items = itemsFor(view);
  const keep = new Set(current.shown);
  for (const child of [...checksEl.children]) {
    if (!keep.has(child.dataset.id)) child.remove();
  }

  for (const id of current.shown) {
    const item = items.find((entry) => entry.id === id);
    let row = checksEl.querySelector(`[data-id="${id}"]`);
    if (!row) {
      row = document.createElement("li");
      row.className = "row";
      row.dataset.id = id;
      row.innerHTML = '<span class="mark"></span><span class="name"></span><span class="slot"></span>';
      checksEl.appendChild(row);
    }
    row.querySelector(".name").textContent = item.name;
    const status = current.results[id];
    const mark = row.querySelector(".mark");
    mark.className = `mark is-${status}`;
    mark.innerHTML = icon(status);
    const slot = row.querySelector(".slot");
    if (status === "fail") {
      let button = slot.querySelector("button");
      if (!button) {
        button = document.createElement("button");
        button.type = "button";
        button.className = "repair";
        button.addEventListener("click", () => repair(id));
        slot.appendChild(button);
      }
      button.textContent = item.repair;
      button.disabled = current.busy;
    } else {
      slot.replaceChildren();
    }
  }
  syncLevel();
}

function showPlatform(platform) {
  view = platform;
  document.getElementById("guide-bilibili").hidden = platform !== "bilibili";
  document.getElementById("guide-mango").hidden = platform !== "mango";
  document.querySelector(".panel").setAttribute("aria-label", platform === "mango" ? "芒果TV控制" : "哔哩哔哩控制");
  document.querySelectorAll(".tab[data-platform]").forEach((tab) => {
    const on = tab.dataset.platform === platform;
    tab.classList.toggle("is-active", on);
    if (on) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  });
  paint();
}

function bridge() {
  if (window.pywebview && window.pywebview.api) return Promise.resolve(window.pywebview.api);
  return new Promise((resolve) => {
    window.addEventListener("pywebviewready", () => resolve(window.pywebview.api), { once: true });
  });
}

function askRestart() {
  document.getElementById("confirm-text").textContent = CLIENT[view].confirm;
  confirmEl.hidden = false;
  return new Promise((resolve) => {
    const finish = (agreed) => {
      confirmEl.hidden = true;
      confirmOk.removeEventListener("click", onOk);
      confirmCancel.removeEventListener("click", onCancel);
      resolve(agreed);
    };
    const onOk = () => finish(true);
    const onCancel = () => finish(false);
    confirmOk.addEventListener("click", onOk);
    confirmCancel.addEventListener("click", onCancel);
  });
}

function formatElapsed(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
}

function drawWave() {
  if (waveEl.hidden) return;
  const ctx = waveEl.getContext("2d");
  const width = waveEl.width;
  const height = waveEl.height;
  ctx.clearRect(0, 0, width, height);
  ctx.beginPath();
  ctx.lineWidth = 2;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.strokeStyle = "rgba(119, 97, 175, 0.9)";
  const mid = height / 2;
  waveLevels.forEach((level, index) => {
    const x = (index / (waveLevels.length - 1)) * (width - 4) + 2;
    const amp = level < 0.04 ? height * 0.16 : level * (height * 0.42);
    const y = mid + Math.sin(index * 0.62 + wavePhase) * amp;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  if (!reduceMotion) wavePhase += 0.18;
  waveFrame = requestAnimationFrame(drawWave);
}

function syncLevel() {
  clearInterval(levelTimer);
  if (state().mode !== "started") {
    waveLevels.fill(0);
    return;
  }
  levelTimer = setInterval(() => { pollLevel(); }, 100);
  cancelAnimationFrame(waveFrame);
  drawWave();
}

function startClock() {
  const current = state();
  if (!current.startedAt) current.startedAt = Date.now();
  clearInterval(clockTimer);
  clockTimer = setInterval(() => {
    if (state().mode !== "started") return;
    elapsedEl.textContent = formatElapsed(Date.now() - state().startedAt);
  }, 250);
  syncLevel();
}

let levelBusy = false;

async function pollLevel() {
  if (levelBusy || state().mode !== "started") return;
  levelBusy = true;
  const platform = view;
  try {
    const api = await bridge();
    const reading = await api.sound_level();
    if (view !== platform || state().mode !== "started") return;
    if (!reading || !reading.running) {
      disconnect(platform);
      return;
    }
    waveLevels.shift();
    waveLevels.push(Math.max(0, Math.min(1, Number(reading.level) || 0)));
    if (typeof reading.caption === "string") captionEl.textContent = reading.caption;
  } catch (error) {
    return;
  } finally {
    levelBusy = false;
  }
}

async function settle(platform) {
  const current = store[platform];
  const run = current.token;
  const allPass = itemsFor(platform).every((item) => current.results[item.id] === "pass");
  if (!allPass) {
    current.mode = "failed";
    current.busy = false;
    if (view === platform) paint();
    return;
  }
  if (current.mode === "started") {
    current.busy = false;
    if (view === platform) paint();
    return;
  }
  current.busy = true;
  if (view === platform) paint();
  try {
    const api = await bridge();
    const started = await api.start_listening(platform);
    if (run !== current.token) {
      try { await api.stop_listening(); } catch (error) { return; }
      return;
    }
    if (started && started.stopped && started.stopped !== platform) {
      const other = store[started.stopped];
      if (other) {
        other.token += 1;
        other.mode = "idle";
        other.busy = false;
        other.shown = [];
        other.results = {};
        other.startedAt = 0;
      }
    }
    if (!started || !started.ok) {
      current.mode = "failed";
      current.busy = false;
      if (view === platform) paint();
      return;
    }
  } catch (error) {
    if (run !== current.token) return;
    current.mode = "failed";
    current.busy = false;
    if (view === platform) paint();
    return;
  }
  current.mode = "started";
  current.busy = false;
  current.startedAt = Date.now();
  if (view === platform) {
    paint();
    startClock();
  }
}

async function runSequence(platform, ids, resolveStatus) {
  const current = store[platform];
  const run = ++current.token;
  current.busy = true;
  if (view === platform) paint();
  for (const id of ids) {
    if (run !== current.token) return;
    if (!current.shown.includes(id)) current.shown.push(id);
    current.results[id] = "checking";
    if (view === platform) paint();
    await sleep(180);
    if (run !== current.token) return;
    current.results[id] = await resolveStatus(id);
    if (view === platform) paint();
  }
  if (run !== current.token) return;
  await settle(platform);
}

async function inspect(platform, id) {
  try {
    const api = await bridge();
    const result = await api.check_item(id, platform);
    return result && result.ok ? "pass" : "fail";
  } catch (error) {
    return "fail";
  }
}

function beginAll() {
  const platform = view;
  const current = store[platform];
  current.checksOpen = true;
  permWatch += 1;
  current.shown = [];
  current.results = {};
  current.mode = "idle";
  if (view === platform) checksEl.replaceChildren();
  runSequence(platform, itemsFor(platform).map((item) => item.id), (id) => inspect(platform, id));
}

function watchPermission() {
  const watch = ++permWatch;
  const platform = view;
  const tick = async () => {
    if (watch !== permWatch || store[platform].mode === "idle" || store[platform].busy) return;
    const status = await inspect(platform, "perm");
    if (watch !== permWatch || store[platform].busy) return;
    store[platform].results.perm = status;
    await settle(platform);
  };
  window.addEventListener("focus", () => { tick(); }, { once: true });
  let tries = 0;
  const timer = setInterval(() => {
    tries += 1;
    if (tries > 15 || watch !== permWatch || store[platform].results.perm === "pass") {
      clearInterval(timer);
      return;
    }
    tick();
  }, 2000);
}

async function repair(id) {
  const platform = view;
  const current = store[platform];
  if (current.busy || current.results[id] !== "fail") return;
  if (id === "client") {
    try {
      const api = await bridge();
      const status = await api.client_status(platform);
      if (status && status.running) {
        const agreed = await askRestart();
        if (!agreed) return;
      }
    } catch (error) {
      return;
    }
  }
  await runSequence(platform, [id], async (itemId) => {
    try {
      const api = await bridge();
      const result = await api.repair_item(itemId, platform);
      if (itemId === "perm") watchPermission();
      return result && result.ok ? "pass" : "fail";
    } catch (error) {
      return "fail";
    }
  });
}

async function closeOther(platform) {
  const otherName = platform === "bilibili" ? "mango" : "bilibili";
  const other = store[otherName];
  const stopMic = other.mode === "started";
  if (other.mode === "idle" && !other.busy) return;
  other.token += 1;
  other.mode = "idle";
  other.busy = false;
  other.shown = [];
  other.results = {};
  other.startedAt = 0;
  other.checksOpen = true;
  if (!stopMic) return;
  try {
    const api = await bridge();
    await api.stop_listening();
  } catch (error) {
    return;
  }
}

async function disconnect(platform) {
  const current = store[platform];
  current.token += 1;
  permWatch += 1;
  const wasStarted = current.mode === "started";
  current.busy = false;
  current.mode = "idle";
  current.checksOpen = true;
  current.shown = [];
  current.results = {};
  current.startedAt = 0;
  if (view === platform) paint();
  if (!wasStarted) return;
  try {
    const api = await bridge();
    await api.stop_listening();
  } catch (error) {
    return;
  }
}

startBtn.addEventListener("click", async () => {
  if (state().busy) return;
  if (state().mode === "started") {
    disconnect(view);
    return;
  }
  await closeOther(view);
  beginAll();
});

async function controlWindow(name) {
  try {
    const api = await bridge();
    if (typeof api[name] === "function") await api[name]();
  } catch (error) {
    return;
  }
}

document.getElementById("win-min").addEventListener("click", () => controlWindow("minimize_window"));
document.getElementById("win-max").addEventListener("click", () => controlWindow("toggle_window"));
document.getElementById("win-close").addEventListener("click", () => controlWindow("close_window"));

retryBtn.addEventListener("click", () => {
  if (state().busy || state().mode !== "failed") return;
  beginAll();
});

foldBtn.addEventListener("click", () => {
  state().checksOpen = !state().checksOpen;
  paint();
});

document.querySelectorAll(".tab[data-platform]").forEach((tab) => {
  tab.addEventListener("click", () => {
    if (tab.dataset.platform === view) return;
    showPlatform(tab.dataset.platform);
  });
});

document.querySelectorAll(".tab.is-locked").forEach((tab) => {
  tab.addEventListener("click", (event) => event.preventDefault());
});
