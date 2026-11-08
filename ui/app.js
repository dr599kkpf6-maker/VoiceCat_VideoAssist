const ITEMS = [
  { id: "client", name: "哔哩哔哩客户端已通过调试端口启动", repair: "重启哔哩哔哩" },
  { id: "mic", name: "麦克风设备已识别", repair: "重新检测" },
  { id: "perm", name: "麦克风权限可用", repair: "打开权限设置" },
  { id: "model", name: "语音识别模型已下载", repair: "重新下载" },
];

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
const foldIcon = document.getElementById("fold-icon");
const waveLevels = Array(40).fill(0);
let wavePhase = 0;
let waveFrame = 0;
let clockTimer = 0;
let levelTimer = 0;
let startedAt = 0;

let mode = "idle";
let busy = false;
let checksOpen = true;
let token = 0;
let permWatch = 0;
let shown = [];
const results = {};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, reduceMotion ? 0 : ms));
}

function icon(kind) {
  if (kind === "checking") return '<span class="spin"></span>';
  const file = kind === "pass" ? "icons/check.svg" : "icons/fail.svg";
  return `<img src="${file}" alt="">`;
}

function paint() {
  const starting = busy && mode !== "started" && mode !== "failed";
  document.getElementById("start-action").textContent = "启动控制器";
  document.getElementById("start-state").textContent = mode === "started"
    ? "已启动"
    : mode === "failed"
      ? "启动失败"
      : starting
        ? "启动中"
        : "未启动";
  startBtn.classList.toggle("is-on", mode === "started");
  startBtn.classList.toggle("is-fail", mode === "failed");
  startBtn.classList.toggle("is-starting", starting);
  startBtn.setAttribute("aria-busy", starting ? "true" : "false");
  retryBtn.hidden = true;
  waveEl.hidden = mode === "idle";
  captionEl.hidden = mode !== "started";
  if (mode !== "started") captionEl.textContent = "";
  if (mode !== "idle" && !waveFrame) drawWave();
  if (mode === "idle" && waveFrame) {
    cancelAnimationFrame(waveFrame);
    waveFrame = 0;
  }
  inspectorEl.classList.toggle("is-open", mode !== "idle");
  inspectorEl.classList.toggle("is-collapsed", !checksOpen);
  document.getElementById("inspector-clip").setAttribute("aria-hidden", checksOpen ? "false" : "true");
  foldBtn.classList.toggle("is-collapsed", !checksOpen);
  foldBtn.setAttribute("aria-expanded", checksOpen ? "true" : "false");
  foldBtn.setAttribute("aria-label", checksOpen ? "收起检测项" : "展开检测项");

  const keep = new Set(shown);
  for (const child of [...checksEl.children]) {
    if (!keep.has(child.dataset.id)) child.remove();
  }

  for (const id of shown) {
    const item = ITEMS.find((entry) => entry.id === id);
    let row = checksEl.querySelector(`[data-id="${id}"]`);
    if (!row) {
      row = document.createElement("li");
      row.className = "row";
      row.dataset.id = id;
      row.innerHTML = '<span class="mark"></span><span class="name"></span><span class="slot"></span>';
      checksEl.appendChild(row);
    }
    row.querySelector(".name").textContent = item.name;
    const status = results[id];
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
      button.disabled = busy;
    } else {
      slot.replaceChildren();
    }
  }
}

function bridge() {
  if (window.pywebview && window.pywebview.api) return Promise.resolve(window.pywebview.api);
  return new Promise((resolve) => {
    window.addEventListener("pywebviewready", () => resolve(window.pywebview.api), { once: true });
  });
}

function askRestart() {
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

function startClock() {
  startedAt = Date.now();
  elapsedEl.textContent = "00:00:00";
  clearInterval(clockTimer);
  clearInterval(levelTimer);
  clockTimer = setInterval(() => {
    if (mode !== "started") return;
    elapsedEl.textContent = formatElapsed(Date.now() - startedAt);
  }, 250);
  levelTimer = setInterval(() => { pollLevel(); }, 100);
  cancelAnimationFrame(waveFrame);
  drawWave();
}

function stopClock() {
  clearInterval(clockTimer);
  clearInterval(levelTimer);
  cancelAnimationFrame(waveFrame);
  waveFrame = 0;
  startedAt = 0;
  elapsedEl.textContent = "00:00:00";
  waveLevels.fill(0);
}

let levelBusy = false;

async function pollLevel() {
  if (levelBusy || mode !== "started") return;
  levelBusy = true;
  try {
    const api = await bridge();
    const state = await api.sound_level();
    if (mode !== "started") return;
    if (!state || !state.running) {
      disconnect();
      return;
    }
    waveLevels.shift();
    waveLevels.push(Math.max(0, Math.min(1, Number(state.level) || 0)));
    if (typeof state.caption === "string") captionEl.textContent = state.caption;
  } catch (error) {
    return;
  } finally {
    levelBusy = false;
  }
}

async function settle() {
  const run = token;
  const allPass = ITEMS.every((item) => results[item.id] === "pass");
  if (!allPass) {
    mode = "failed";
    busy = false;
    paint();
    return;
  }
  if (mode === "started") {
    busy = false;
    paint();
    return;
  }
  busy = true;
  paint();
  try {
    const api = await bridge();
    const started = await api.start_listening();
    if (run !== token) {
      try { await api.stop_listening(); } catch (error) { return; }
      return;
    }
    if (!started || !started.ok) {
      mode = "failed";
      busy = false;
      paint();
      return;
    }
  } catch (error) {
    if (run !== token) return;
    mode = "failed";
    busy = false;
    paint();
    return;
  }
  mode = "started";
  busy = false;
  paint();
  startClock();
}

async function runSequence(ids, resolveStatus) {
  const run = ++token;
  busy = true;
  paint();
  for (const id of ids) {
    if (run !== token) return;
    if (!shown.includes(id)) shown.push(id);
    results[id] = "checking";
    paint();
    await sleep(180);
    if (run !== token) return;
    results[id] = await resolveStatus(id);
    paint();
  }
  if (run !== token) return;
  await settle();
}

async function inspect(id) {
  try {
    const api = await bridge();
    const result = await api.check_item(id);
    return result && result.ok ? "pass" : "fail";
  } catch (error) {
    return "fail";
  }
}

function beginAll() {
  checksOpen = true;
  permWatch += 1;
  shown = [];
  for (const item of ITEMS) delete results[item.id];
  mode = "idle";
  checksEl.replaceChildren();
  runSequence(ITEMS.map((item) => item.id), inspect);
}

function watchPermission() {
  const watch = ++permWatch;
  const tick = async () => {
    if (watch !== permWatch || mode === "idle" || busy) return;
    const status = await inspect("perm");
    if (watch !== permWatch || busy) return;
    results.perm = status;
    await settle();
  };
  window.addEventListener("focus", () => { tick(); }, { once: true });
  let tries = 0;
  const timer = setInterval(() => {
    tries += 1;
    if (tries > 15 || watch !== permWatch || results.perm === "pass") {
      clearInterval(timer);
      return;
    }
    tick();
  }, 2000);
}

async function repair(id) {
  if (busy || results[id] !== "fail") return;
  if (id === "client") {
    try {
      const api = await bridge();
      const status = await api.client_status();
      if (status && status.running) {
        const agreed = await askRestart();
        if (!agreed) return;
      }
    } catch (error) {
      return;
    }
  }
  await runSequence([id], async (itemId) => {
    try {
      const api = await bridge();
      const result = await api.repair_item(itemId);
      if (itemId === "perm") watchPermission();
      return result && result.ok ? "pass" : "fail";
    } catch (error) {
      return "fail";
    }
  });
}

async function disconnect() {
  token += 1;
  permWatch += 1;
  checksOpen = true;
  const wasStarted = mode === "started";
  busy = false;
  mode = "idle";
  shown = [];
  for (const item of ITEMS) delete results[item.id];
  stopClock();
  paint();
  if (!wasStarted) return;
  try {
    const api = await bridge();
    await api.stop_listening();
  } catch (error) {
    return;
  }
}

startBtn.addEventListener("click", () => {
  if (busy) return;
  if (mode === "idle" || mode === "failed") beginAll();
  else if (mode === "started") disconnect();
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
  if (busy || mode !== "failed") return;
  beginAll();
});

foldBtn.addEventListener("click", () => {
  checksOpen = !checksOpen;
  paint();
});

document.querySelectorAll(".tab.is-locked").forEach((tab) => {
  tab.addEventListener("click", (event) => event.preventDefault());
});
