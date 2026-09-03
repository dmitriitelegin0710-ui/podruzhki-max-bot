// ============================================================================
// НАСТРОЙКА — заполните после того, как разместите backend/server.py
// ============================================================================
const BACKEND_BASE_URL = "https://api.xn--d1aeghrfjy.online"; // <-- без слэша на конце

const CHANNEL_URL = "https://max.ru/channel_podruzhki";

const FILTERS = [
  { id: "none",    label: "Обычный",    emoji: "🧴" },
  { id: "kids",    label: "Для детей",  emoji: "🧸" },
  { id: "allergy", label: "Аллергикам", emoji: "⚠️" },
  { id: "healthy", label: "Для ПП",     emoji: "🥗" },
];

const LOW_BALANCE_THRESHOLD = 1; // при таком количестве и меньше — подсвечиваем счётчик и предлагаем докупить

// ============================================================================
// СОСТОЯНИЕ
// ============================================================================
let activeFilter = "none";
let selectedFile = null;
let balanceInfo = null;   // { free_left, paid_balance, total_available, packages }
let lastResult = null;
let screen = "loading";   // loading | home | analyzing | result | paywall | error

const app = document.getElementById("app");

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ============================================================================
// MAX WebApp bridge + идентификация (initData ИЛИ device_id — оба варианта)
// ============================================================================

function hasWebApp() {
  return typeof window.WebApp !== "undefined" && !!window.WebApp;
}

function getInitData() {
  try {
    if (hasWebApp() && window.WebApp.initData) return window.WebApp.initData;
  } catch (e) { /* игнорируем */ }
  return null;
}

function getDeviceId() {
  let id = localStorage.getItem("scan_device_id");
  if (!id) {
    id = "dev_" + Math.random().toString(36).slice(2) + Date.now().toString(36);
    localStorage.setItem("scan_device_id", id);
  }
  return id;
}

// Добавляет заголовок initData (если апп открыт внутри MAX) — сервер сам
// решит, доверять ли ему, и при отсутствии initData использует device_id,
// который мы всегда кладём отдельным полем формы / query-параметром.
function authHeaders() {
  const initData = getInitData();
  return initData ? { "X-Init-Data": initData } : {};
}

function deviceIdParam() {
  return getInitData() ? "" : `device_id=${encodeURIComponent(getDeviceId())}`;
}

// ============================================================================
// API
// ============================================================================

async function apiGet(path) {
  const qs = deviceIdParam();
  const url = `${BACKEND_BASE_URL}${path}${qs ? (path.includes("?") ? "&" : "?") + qs : ""}`;
  const res = await fetch(url, { headers: authHeaders() });
  return { ok: res.ok, status: res.status, data: await res.json().catch(() => ({})) };
}

async function apiPostJson(path, body) {
  const res = await fetch(`${BACKEND_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ ...body, device_id: getInitData() ? undefined : getDeviceId() }),
  });
  return { ok: res.ok, status: res.status, data: await res.json().catch(() => ({})) };
}

async function apiPostForm(path, formData) {
  if (!getInitData()) formData.append("device_id", getDeviceId());
  const res = await fetch(`${BACKEND_BASE_URL}${path}`, {
    method: "POST",
    headers: authHeaders(),
    body: formData,
  });
  return { ok: res.ok, status: res.status, data: await res.json().catch(() => ({})) };
}

async function loadBalance() {
  const { ok, data } = await apiGet("/api/balance");
  if (ok) balanceInfo = data;
  return ok;
}

// ============================================================================
// ЭКРАНЫ
// ============================================================================

function render() {
  if (screen === "loading") return renderLoading();
  if (screen === "error") return renderError();
  if (screen === "home") return renderHome();
  if (screen === "analyzing") return renderAnalyzing();
  if (screen === "result") return renderResult();
  if (screen === "paywall") return renderPaywall();
}

function renderLoading() {
  app.innerHTML = `<div class="loading"><div class="spinner"></div>Загрузка…</div>`;
}

function renderError() {
  app.innerHTML = `
    <div class="error">
      Не удалось подключиться к серверу.<br>Попробуйте ещё раз чуть позже.
      <div><button class="retry-btn" id="retry-btn">Повторить</button></div>
    </div>
  `;
  document.getElementById("retry-btn").addEventListener("click", async () => {
    screen = "loading";
    render();
    const ok = await loadBalance();
    if (!ok) { screen = "error"; render(); return; }
    screen = balanceInfo.total_available > 0 ? "home" : "paywall";
    render();
  });
}

function balanceChipHtml() {
  const total = balanceInfo ? balanceInfo.total_available : 0;
  const low = total <= LOW_BALANCE_THRESHOLD;
  return `
    <div>
      <div class="balance-chip ${low ? "low" : ""}">🔍 Осталось сканов: ${total}</div>
      ${low ? `<div class="topup-link" id="topup-link">Докупить сканы →</div>` : ""}
    </div>
  `;
}

function bindTopupLink() {
  const el = document.getElementById("topup-link");
  if (el) el.addEventListener("click", () => { screen = "paywall"; render(); });
}

function renderHome() {
  const filtersHtml = FILTERS.map(
    (f) => `<div class="filter-pill ${f.id === activeFilter ? "active" : ""}" data-filter="${f.id}">
              <span class="emoji">${f.emoji}</span>${esc(f.label)}
            </div>`
  ).join("");

  const previewHtml = selectedFile
    ? `<div class="preview-wrap"><img id="preview-img" alt="Фото этикетки"></div>`
    : `<label class="camera-box" for="camera-input">
         <div class="icon">📷</div>
         <div class="label">Сфотографировать этикетку</div>
         <div class="sub">или выбрать фото из галереи</div>
       </label>`;

  app.innerHTML = `
    <div class="card">
      <div class="balance-row">
        <h1>Сканер состава</h1>
        ${balanceChipHtml()}
      </div>
      <p class="hint">Наведите камеру на список состава на упаковке — покажем, стоит ли брать этот продукт.</p>

      <div class="filters">${filtersHtml}</div>

      ${previewHtml}
      <input type="file" id="camera-input" accept="image/*" capture="environment">

      ${selectedFile ? `<button class="secondary" id="retake-btn">Выбрать другое фото</button>` : ""}
      <button class="primary" id="scan-btn" ${selectedFile ? "" : "disabled"}>Проверить состав</button>
    </div>

    <div class="actions-panel">
      <a class="panel-btn" href="${CHANNEL_URL}" id="back-to-channel">⟵ Вернуться на канал</a>
    </div>
  `;

  if (selectedFile) {
    const img = document.getElementById("preview-img");
    img.src = URL.createObjectURL(selectedFile);
  }

  bindTopupLink();

  document.querySelectorAll(".filter-pill").forEach((el) => {
    el.addEventListener("click", () => {
      activeFilter = el.dataset.filter;
      render();
    });
  });

  const input = document.getElementById("camera-input");
  input.addEventListener("change", () => {
    if (input.files && input.files[0]) {
      selectedFile = input.files[0];
      render();
    }
  });

  const retakeBtn = document.getElementById("retake-btn");
  if (retakeBtn) retakeBtn.addEventListener("click", () => { selectedFile = null; render(); });

  document.getElementById("scan-btn").addEventListener("click", runScan);

  document.getElementById("back-to-channel").addEventListener("click", (e) => {
    if (hasWebApp() && typeof window.WebApp.openMaxLink === "function") {
      e.preventDefault();
      window.WebApp.openMaxLink(CHANNEL_URL);
    }
  });
}

function renderAnalyzing() {
  app.innerHTML = `<div class="loading"><div class="spinner"></div>Анализируем состав…</div>`;
}

function verdictClass(v) {
  return { good: "verdict-good", neutral: "verdict-neutral", caution: "verdict-caution",
           bad: "verdict-bad", unknown: "verdict-unknown" }[v] || "verdict-unknown";
}

function renderResult() {
  const r = lastResult;
  const warningsHtml = r.warnings && r.warnings.length
    ? `<p><strong>Обратите внимание:</strong></p><ul class="result-list">${r.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`
    : "";
  const recsHtml = r.recommendations && r.recommendations.length
    ? `<ul class="result-list">${r.recommendations.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`
    : "";

  app.innerHTML = `
    <div class="card">
      <div class="balance-row">
        <div class="verdict-badge ${verdictClass(r.verdict)}">${esc(r.verdict_text)}</div>
        ${balanceChipHtml()}
      </div>
      ${warningsHtml}
      ${recsHtml}
      <button class="primary" id="scan-again-btn">Сканировать ещё</button>
    </div>
    <div class="actions-panel">
      <a class="panel-btn" href="${CHANNEL_URL}" id="back-to-channel">⟵ Вернуться на канал</a>
    </div>
  `;

  bindTopupLink();

  document.getElementById("scan-again-btn").addEventListener("click", () => {
    selectedFile = null;
    screen = "home";
    render();
  });
  document.getElementById("back-to-channel").addEventListener("click", (e) => {
    if (hasWebApp() && typeof window.WebApp.openMaxLink === "function") {
      e.preventDefault();
      window.WebApp.openMaxLink(CHANNEL_URL);
    }
  });
}

function renderPaywall() {
  const packages = (balanceInfo && balanceInfo.packages) || {};
  const cardsHtml = Object.entries(packages).map(([id, p]) => `
    <div class="package-card" data-package="${id}">
      <div class="package-title">${esc(p.title)}</div>
      <div class="package-price">${p.price_rub} ₽</div>
    </div>
  `).join("");

  app.innerHTML = `
    <div class="card">
      <h1>Бесплатные сканы закончились</h1>
      <p class="hint">Выберите пакет — оплата картой, откроется в новом окне. После оплаты сканы начислятся автоматически.</p>
      <div class="packages">${cardsHtml}</div>
      <button class="secondary" id="paywall-back-btn">Назад</button>
    </div>
  `;

  document.querySelectorAll(".package-card").forEach((el) => {
    el.addEventListener("click", () => buyPackage(el.dataset.package));
  });
  document.getElementById("paywall-back-btn").addEventListener("click", () => { screen = "home"; render(); });
}

// ============================================================================
// ДЕЙСТВИЯ
// ============================================================================

async function runScan() {
  if (!selectedFile) return;
  screen = "analyzing";
  render();

  const formData = new FormData();
  formData.append("image", selectedFile);
  formData.append("filter", activeFilter);

  const { ok, status, data } = await apiPostForm("/api/analyze", formData);

  if (ok) {
    lastResult = data;
    selectedFile = null;
    await loadBalance();
    screen = "result";
    render();
    return;
  }

  if (status === 402) {
    if (data.packages) balanceInfo = { ...balanceInfo, packages: data.packages, total_available: 0 };
    screen = "paywall";
    render();
    return;
  }

  screen = "error";
  render();
}

async function buyPackage(packageId) {
  const { ok, data } = await apiPostJson("/api/pay/create", { package_id: packageId });
  if (!ok || !data.confirmation_url) {
    screen = "error";
    render();
    return;
  }
  if (hasWebApp() && typeof window.WebApp.openLink === "function") {
    window.WebApp.openLink(data.confirmation_url);
  } else {
    window.location.href = data.confirmation_url;
  }
}

// ============================================================================
// СТАРТ
// ============================================================================

if (hasWebApp() && typeof window.WebApp.ready === "function") {
  try { window.WebApp.ready(); } catch (e) { /* не критично */ }
}

(async function init() {
  const ok = await loadBalance();
  if (!ok) {
    screen = "error";
    render();
    return;
  }
  screen = balanceInfo.total_available > 0 ? "home" : "paywall";
  render();
})();
