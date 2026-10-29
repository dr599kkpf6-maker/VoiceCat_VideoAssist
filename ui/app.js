const ITEMS = [
  { id: "client", name: "哔哩哔哩客户端已通过调试端口启动", repair: "重启哔哩哔哩" },
  { id: "mic", name: "麦克风设备已识别", repair: "重新检测" },
  { id: "perm", name: "麦克风权限可用", repair: "打开权限设置" },
  { id: "model", name: "语音识别模型已下载", repair: "重新下载" },
];

// 界面占位，用来摆出检查中、通过、不通过和修复按钮。不检测设备、模型或客户端。
const PLACEHOLDER = {
  client: "fail",
  mic: "pass",
  perm: "fail",
  model: "pass",
};

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const startBtn = document.getElementById("start");
const retryBtn = document.getElementById("retry");
const checksEl = document.getElementById("checks");
const windowEl = document.querySelector(".window");
const glow = document.getElementById("glow");

let mode = "idle";
let busy = false;
let token = 0;
let shown = [];
const results = {};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, reduceMotion ? 0 : ms));
}

function icon(kind) {
  if (kind === "checking") return '<span class="spin"></span>';
  const path = kind === "pass"
    ? '<path d="M6 12.5 9.2 15.7 18 8"></path>'
    : '<path d="M8 8l8 8M16 8l-8 8"></path>';
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;
}

function paint() {
  const starting = busy && mode !== "started" && mode !== "failed";
  startBtn.textContent = mode === "started" ? "已启动" : mode === "failed" ? "启动失败" : "启动控制器";
  startBtn.classList.toggle("is-on", mode === "started");
  startBtn.classList.toggle("is-fail", mode === "failed");
  startBtn.classList.toggle("is-starting", starting);
  startBtn.setAttribute("aria-busy", starting ? "true" : "false");
  retryBtn.hidden = mode !== "failed";
  retryBtn.disabled = busy;

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

async function runSequence(ids, resolveStatus) {
  const run = ++token;
  busy = true;
  paint();
  for (const id of ids) {
    if (run !== token) return;
    if (!shown.includes(id)) shown.push(id);
    results[id] = "checking";
    paint();
    await sleep(420);
    if (run !== token) return;
    results[id] = resolveStatus(id);
    paint();
    await sleep(140);
  }
  if (run !== token) return;
  const allPass = ITEMS.every((item) => results[item.id] === "pass");
  mode = allPass ? "started" : "failed";
  busy = false;
  paint();
}

function beginAll() {
  shown = [];
  for (const item of ITEMS) delete results[item.id];
  mode = "idle";
  checksEl.replaceChildren();
  runSequence(ITEMS.map((item) => item.id), (id) => PLACEHOLDER[id]);
}

function repair(id) {
  if (busy || results[id] !== "fail") return;
  runSequence([id], () => "pass");
}

function disconnect() {
  token += 1;
  busy = false;
  mode = "idle";
  shown = [];
  for (const item of ITEMS) delete results[item.id];
  paint();
}

startBtn.addEventListener("click", () => {
  if (busy) return;
  if (mode === "idle") beginAll();
  else if (mode === "started") disconnect();
});

retryBtn.addEventListener("click", () => {
  if (busy || mode !== "failed") return;
  beginAll();
});

document.querySelectorAll(".tab.is-locked").forEach((tab) => {
  tab.addEventListener("click", (event) => event.preventDefault());
});

if (!reduceMotion) {
  let x = 180;
  let y = 120;
  let tx = x;
  let ty = y;
  windowEl.addEventListener("pointermove", (event) => {
    const rect = windowEl.getBoundingClientRect();
    tx = event.clientX - rect.left;
    ty = event.clientY - rect.top;
  });
  const follow = () => {
    x += (tx - x) * 0.12;
    y += (ty - y) * 0.12;
    glow.style.transform = `translate(${x - 130}px, ${y - 130}px)`;
    requestAnimationFrame(follow);
  };
  follow();
}
