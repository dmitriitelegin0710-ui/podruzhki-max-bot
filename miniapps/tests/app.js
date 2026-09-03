const DEFAULT_TEST = 1;
const TOTAL_TESTS = 24;

// Ссылка на канал MAX — используется кнопкой "Вернуться на канал"
// на стартовом экране теста, на экране вопроса и на экране результата.
const CHANNEL_URL = "https://max.ru/channel_podruzhki";

let tests = [];
// Номер теста может прийти двумя способами:
// 1) ?test=11 в URL — если сайт открыли напрямую как обычную ссылку
//    (например, при тестировании в браузере).
// 2) через start_param мини-приложения — если запуск произошёл по
//    кнопке "open_app", привязанной к боту. Формат payload — "test11"
//    (латиница/цифры/подчёркивание/дефис), поэтому вытаскиваем число
//    из строки на случай такого формата.
function resolveTestIndexFromStartParam() {
  try {
    if (typeof window.WebApp === "undefined" || !window.WebApp) return null;
    const startParam =
      (window.WebApp.initDataUnsafe && window.WebApp.initDataUnsafe.start_param) || null;
    if (!startParam) return null;
    const match = String(startParam).match(/(\d+)/);
    if (!match) return null;
    return Number(match[1]);
  } catch (e) {
    return null;
  }
}

const startParamTest = resolveTestIndexFromStartParam();
const requestedTest = startParamTest || Number(new URLSearchParams(location.search).get("test") || DEFAULT_TEST);
let testIndex = Math.max(0, Math.min(TOTAL_TESTS - 1, requestedTest - 1));
let quiz = null;
let current = 0;
let answers = [];
let lastResult = null; // сохраняем последний показанный результат — нужен для шеринга

const app = document.getElementById("app");

function esc(s) {
  return String(s).replace(/[&<>"']/g, ch => ({
    "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;"
  }[ch]));
}

function parseTestBlock(block) {
  const lines = block.replace(/\r/g, "").split("\n");
  let title = "";
  let hook = "";
  let questions = [];
  let results = {};
  let section = "";
  let currentQ = null;
  let currentResult = null;

  for (const raw of lines) {
    const line = raw.trim();

    if (line.match(/^#\s+\d+\.\s+/)) {
      title = line.replace(/^#\s+\d+\.\s+/, "").trim();
      continue;
    }

    if (line.startsWith("Хук:")) {
      hook = line.slice(5).trim();
      continue;
    }

    if (line === "## Вопросы") {
      section = "questions";
      continue;
    }

    if (line === "## Логика результата") {
      section = "logic";
      continue;
    }

    if (line === "## Результаты") {
      section = "results";
      continue;
    }

    if (line === "## CTA") {
      section = "cta";
      continue;
    }

    if (section === "questions") {
      const q = line.match(/^(\d+)\.\s+(.+)$/);
      if (q) {
        currentQ = { number: Number(q[1]), text: q[2], options: {} };
        questions.push(currentQ);
        continue;
      }

      const opt = line.match(/^-\s*([ABCD])\.\s+(.+)$/);
      if (opt && currentQ) {
        currentQ.options[opt[1]] = opt[2];
      }
    }

    if (section === "results") {
      const r = line.match(/^([ABCD])\s+—\s+(.+)$/);
      if (r) {
        currentResult = r[1];
        results[currentResult] = {
          title: r[2],
          text: ""
        };
        continue;
      }

      if (currentResult && line && !line.startsWith("#")) {
        results[currentResult].text += (results[currentResult].text ? " " : "") + line;
      }
    }
  }

  if (!title || !questions.length) {
    throw new Error("Не удалось найти заголовок или вопросы в тесте.");
  }

  for (const q of questions) {
    for (const letter of ["A","B","C","D"]) {
      if (!q.options[letter]) {
        throw new Error(`В тесте «${title}», вопросе ${q.number} отсутствует вариант ${letter}.`);
      }
    }
  }

  return { title, hook, questions, results };
}

function parseAllTests(md) {
  // Один MD-файл содержит 24 теста. Каждый начинается с "# N. Название".
  const blocks = md.replace(/\r/g, "").split(/(?=^#\s+\d+\.\s+)/m)
    .map(block => block.trim())
    .filter(Boolean);

  const parsed = blocks
    .map(parseTestBlock)
    .filter(test => test.questions.length);

  if (parsed.length !== TOTAL_TESTS) {
    throw new Error(`Ожидалось ${TOTAL_TESTS} теста, найдено ${parsed.length}.`);
  }

  for (const [i, test] of parsed.entries()) {
    if (test.questions.length !== 8) {
      throw new Error(`В тесте ${i + 1} должно быть 8 вопросов, найдено ${test.questions.length}.`);
    }
  }

  return parsed;
}

// --- Интеграция с MAX Bridge (window.WebApp) ---
// Функции ниже безопасно работают и без MAX Bridge (например, при
// открытии mini app напрямую в обычном браузере для проверки) — тогда
// используется обычный переход по ссылке / нативный шеринг браузера.
//
// ВАЖНО: window.WebApp и его методы по-настоящему работают только внутри
// реального клиента MAX (телефон/десктоп/офиц. веб-клиент). Если открыть
// ссылку теста просто в Chrome/Safari напрямую — WebApp будет либо
// отсутствовать, либо его методы не дадут эффекта, и сработает последний
// fallback (location.href). Это нормально и не является багом кода —
// проверять "Вернуться на канал" нужно из реального поста/бота в MAX.

function hasWebApp() {
  return typeof window.WebApp !== "undefined" && window.WebApp !== null;
}

// Сообщаем клиенту MAX, что экран готов (убирает "скелетон" загрузки).
// Безопасно вызывается один раз при старте, если WebApp доступен.
if (hasWebApp() && typeof window.WebApp.ready === "function") {
  try { window.WebApp.ready(); } catch (e) { /* игнорируем — не критично */ }
}

// handleReturnToChannel вызывается из onclick НАСТОЯЩЕЙ ссылки <a href="...">
// (см. renderStart/renderQuestion/showResult). Такой подход надёжнее, чем
// программная навигация через location.href: если бридж не сработал —
// сработает обычный клик по ссылке, а его операционная система (Android/iOS)
// умеет распознавать диплинки max.ru гораздо надёжнее, чем JS-редирект,
// который многие встроенные WebView блокируют из соображений безопасности.
//
// Возвращает true, если нужно позволить браузеру перейти по обычной ссылке
// (fallback), и false, если переход уже обработан через MAX Bridge.
window.handleReturnToChannel = function(event) {
  try {
    if (hasWebApp()) {
      // 1) Мини-приложение обычно открыто ПОВЕРХ экрана канала — простое
      //    закрытие возвращает пользователя ровно туда, откуда он пришёл.
      if (typeof window.WebApp.close === "function") {
        window.WebApp.close();
        if (event) event.preventDefault();
        return false;
      }
      // 2) Резерв: диплинк max.ru внутри самого MAX.
      if (typeof window.WebApp.openMaxLink === "function") {
        window.WebApp.openMaxLink(CHANNEL_URL);
        if (event) event.preventDefault();
        return false;
      }
    }
  } catch (e) {
    console.error("returnToChannel: ошибка при вызове MAX Bridge", e);
  }

  // 3) Бридж недоступен/не сработал — пусть браузер сам обработает клик
  //    по реальной ссылке <a href="https://max.ru/...">. Это сработает
  //    даже там, где программная навигация была заблокирована.
  return true;
};

window.shareResult = async function() {
  if (!quiz || !lastResult) return;

  const resultTitle = lastResult.title || "";
  const shareText = `Прошла тест «${quiz.title}» — мой результат: ${resultTitle}. Попробуй тоже! 🧠`;
  const shareLink = `${location.origin}${location.pathname}?test=${testIndex + 1}`;

  try {
    if (hasWebApp() && typeof window.WebApp.shareMaxContent === "function") {
      window.WebApp.shareMaxContent({ text: shareText, link: shareLink });
      return;
    }
  } catch (e) {
    console.error("shareMaxContent error", e);
  }

  try {
    if (hasWebApp() && typeof window.WebApp.shareContent === "function") {
      window.WebApp.shareContent({ text: shareText, link: shareLink });
      return;
    }
  } catch (e) {
    console.error("shareContent error", e);
  }

  if (navigator.share) {
    try {
      await navigator.share({ text: shareText, url: shareLink });
      return; // пользователь успешно поделился
    } catch (e) {
      // Пользователь отменил ИЛИ share не работает в этой среде.
      // Раньше код на этом молча останавливался — теперь идём дальше.
      console.warn("navigator.share недоступен, пробуем буфер обмена", e);
    }
  }

  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(`${shareText} ${shareLink}`);
      alert("Ссылка на тест и результат скопированы — вставьте в чат, чтобы поделиться!");
      return;
    }
  } catch (e) {
    console.error("clipboard error", e);
  }

  // Гарантированный последний fallback — показать текст в диалоге,
  // чтобы можно было скопировать вручную, даже если всё остальное недоступно.
  prompt("Скопируйте текст и ссылку, чтобы поделиться:", `${shareText} ${shareLink}`);
};

// --- Диагностика для отладки на реальном телефоне (без DevTools) ---
// Откройте страницу с ?debug=1 в конце адреса, например:
// https://xn--d1aeghrfjy.online/max/?test=11&debug=1
// Внизу экрана появится панель с информацией о том, что реально доступно
// в window.WebApp на этом устройстве — пришлите скриншот для диагностики.
function renderDebugPanel() {
  if (new URLSearchParams(location.search).get("debug") !== "1") return;

  const info = {
    hasWebApp: hasWebApp(),
    webAppKeys: hasWebApp() ? Object.keys(window.WebApp) : [],
    hasClose: hasWebApp() && typeof window.WebApp.close === "function",
    hasOpenMaxLink: hasWebApp() && typeof window.WebApp.openMaxLink === "function",
    hasOpenLink: hasWebApp() && typeof window.WebApp.openLink === "function",
    hasShareMaxContent: hasWebApp() && typeof window.WebApp.shareMaxContent === "function",
    hasShareContent: hasWebApp() && typeof window.WebApp.shareContent === "function",
    hasNavigatorShare: typeof navigator.share === "function",
    platform: hasWebApp() && window.WebApp.platform,
    version: hasWebApp() && window.WebApp.version,
    userAgent: navigator.userAgent
  };

  const pre = document.createElement("pre");
  pre.id = "debug-panel";
  pre.textContent = JSON.stringify(info, null, 2);
  document.body.appendChild(pre);
}

function renderStart() {
  quiz = tests[testIndex];

  app.innerHTML = `
    <section class="card start">
      <div class="test-counter">Тест ${testIndex + 1} из ${tests.length}</div>
      <h1>${esc(quiz.title)}</h1>
      ${quiz.hook ? `<p class="hook">${esc(quiz.hook)}</p>` : ""}
      <div class="meta">${quiz.questions.length} вопросов · 4 варианта ответа</div>

      <button class="primary" onclick="startQuiz()">Пройти тест</button>
    </section>

    <nav class="actions-panel">
      <button class="panel-btn" onclick="nextTest()">Следующий</button>
      <a class="panel-btn" href="${CHANNEL_URL}" onclick="return handleReturnToChannel(event)">Вернуться на канал</a>
    </nav>
  `;
  renderDebugPanel();
}

window.startQuiz = function() {
  current = 0;
  answers = [];
  renderQuestion();
};

window.nextTest = function() {
  testIndex = (testIndex + 1) % tests.length;
  current = 0;
  answers = [];
  const url = new URL(location.href);
  url.searchParams.set("test", String(testIndex + 1));
  history.replaceState(null, "", url);
  renderStart();
};

// Возврат к самому первому тесту из общего списка ("Все тесты").
window.goToFirstTest = function() {
  testIndex = 0;
  current = 0;
  answers = [];
  const url = new URL(location.href);
  url.searchParams.set("test", "1");
  history.replaceState(null, "", url);
  renderStart();
};

function renderQuestion() {
  const q = quiz.questions[current];
  const letters = ["A","B","C","D"];
  const percent = Math.round((current / quiz.questions.length) * 100);

  app.innerHTML = `
    <section class="card">
      <div class="progress-row">
        <span>Тест ${testIndex + 1} из ${tests.length}</span>
        <span>Вопрос ${current + 1} из ${quiz.questions.length}</span>
      </div>
      <div class="progress"><div style="width:${percent}%"></div></div>

      <h2>${esc(q.text)}</h2>

      <div class="options">
        ${letters.map(letter => `
          <button class="option" data-letter="${letter}" onclick="choose('${letter}')">
            <span class="radio"></span>
            <span>${esc(q.options[letter])}</span>
          </button>
        `).join("")}
      </div>

      <button id="next" class="primary disabled" disabled>Далее</button>
    </section>

    <nav class="actions-panel">
      <button class="panel-btn" onclick="goToFirstTest()">Все тесты</button>
      <button class="panel-btn" onclick="nextTest()">Следующий</button>
      <a class="panel-btn" href="${CHANNEL_URL}" onclick="return handleReturnToChannel(event)">Вернуться в канал</a>
    </nav>
  `;
  renderDebugPanel();
}

window.choose = function(letter) {
  answers[current] = letter;
  document.querySelectorAll(".option").forEach(btn => {
    btn.classList.toggle("selected", btn.dataset.letter === letter);
  });

  const next = document.getElementById("next");
  next.disabled = false;
  next.classList.remove("disabled");
  next.textContent = current === quiz.questions.length - 1 ? "Показать результат" : "Далее";
  next.onclick = () => {
    if (current === quiz.questions.length - 1) showResult();
    else {
      current++;
      renderQuestion();
    }
  };
};

function showResult() {
  const scores = { A:0, B:0, C:0, D:0 };
  answers.forEach(a => scores[a]++);

  const max = Math.max(...Object.values(scores));
  const winners = Object.keys(scores).filter(k => scores[k] === max);
  const resultKey = winners.includes(answers[answers.length - 1])
    ? answers[answers.length - 1]
    : winners[0];

  const result = quiz.results[resultKey];
  lastResult = result || { title: resultKey, text: "" };

  app.innerHTML = `
    <section class="card result">
      <div class="badge">ТЕСТ ${testIndex + 1} ИЗ ${tests.length}</div>
      <div class="score">${max} из ${quiz.questions.length}</div>
      <h1>${esc(lastResult.title)}</h1>
      <p>${esc(lastResult.text || "Результат определён по вашим ответам.")}</p>

      <button class="primary" onclick="startQuiz()">Пройти ещё раз</button>
    </section>

    <nav class="actions-panel">
      <button class="panel-btn" onclick="shareResult()">Поделиться результатом</button>
      <button class="panel-btn" onclick="nextTest()">Следующий</button>
      <a class="panel-btn" href="${CHANNEL_URL}" onclick="return handleReturnToChannel(event)">Вернуться на канал</a>
    </nav>
  `;
  renderDebugPanel();
}

async function loadQuiz() {
  try {
    const response = await fetch("tests/test-001.md");
    if (!response.ok) throw new Error("Файл tests/test-001.md не найден.");
    const md = await response.text();

    tests = parseAllTests(md);
    renderStart();
  } catch (error) {
    app.innerHTML = `
      <section class="card error">
        <h1>Не удалось загрузить тесты</h1>
        <p>${esc(error.message)}</p>
        <p>Проверьте, что файл лежит в папке <b>tests/</b>.</p>
      </section>
    `;
  }
}

loadQuiz();
