const DEFAULT_TEST = 1;
const TOTAL_TESTS = 24;

// Ссылка на канал MAX — используется кнопкой "Вернуться на канал"
// на стартовом экране теста, на экране вопроса и на экране результата.
const CHANNEL_URL = "https://max.ru/channel_podruzhki";

let tests = [];
let testIndex = Math.max(0, Math.min(TOTAL_TESTS - 1, Number(new URLSearchParams(location.search).get("test") || DEFAULT_TEST) - 1));
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

window.returnToChannel = function() {
  try {
    if (hasWebApp()) {
      // 1) Основной способ: мини-приложение открыто ПОВЕРХ экрана канала
      //    (пользователь пришёл по ссылке из поста в канале). Простое
      //    закрытие мини-аппа возвращает его ровно туда, откуда он был
      //    открыт, то есть на канал. Это надёжнее, чем пытаться открыть
      //    диплинк на канал изнутри уже открытого мини-аппа — такой
      //    "самовызов" на некоторых платформах просто блокируется на
      //    уровне ОС/WebView и внешне выглядит как "кнопка не работает".
      if (typeof window.WebApp.close === "function") {
        window.WebApp.close();
        return;
      }
      // 2) Резерв: открыть диплинк max.ru внутри самого MAX.
      if (typeof window.WebApp.openMaxLink === "function") {
        window.WebApp.openMaxLink(CHANNEL_URL);
        return;
      }
      // 3) Резерв: открыть ту же ссылку через штатный "внешний" метод.
      if (typeof window.WebApp.openLink === "function") {
        window.WebApp.openLink(CHANNEL_URL);
        return;
      }
    }
  } catch (e) {
    console.error("returnToChannel: ошибка при вызове MAX Bridge", e);
  }

  // 4) Финальный fallback — обычный переход по ссылке.
  //    Актуален только если страница открыта вне MAX (прямой браузер).
  window.location.href = CHANNEL_URL;
};

window.shareResult = function() {
  if (!quiz || !lastResult) return;

  const resultTitle = lastResult.title || "";
  const shareText = `Прошла тест «${quiz.title}» — мой результат: ${resultTitle}. Попробуй тоже! 🧠`;
  const shareLink = `${location.origin}${location.pathname}?test=${testIndex + 1}`;

  if (hasWebApp() && typeof window.WebApp.shareMaxContent === "function") {
    window.WebApp.shareMaxContent({ text: shareText, link: shareLink });
    return;
  }

  if (hasWebApp() && typeof window.WebApp.shareContent === "function") {
    window.WebApp.shareContent({ text: shareText, link: shareLink });
    return;
  }

  if (navigator.share) {
    navigator.share({ text: shareText, url: shareLink }).catch(() => {});
    return;
  }

  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(`${shareText} ${shareLink}`);
    alert("Ссылка на тест и результат скопированы — вставьте в чат, чтобы поделиться!");
  }
};

function renderStart() {
  quiz = tests[testIndex];

  app.innerHTML = `
    <section class="card start">
      <div class="test-counter">ТЕСТ ${testIndex + 1} ИЗ ${tests.length}</div>
      <h1>${esc(quiz.title)}</h1>
      ${quiz.hook ? `<p class="hook">${esc(quiz.hook)}</p>` : ""}
      <div class="meta">${quiz.questions.length} вопросов · 4 варианта ответа</div>

      <div class="start-actions">
        <button class="primary" onclick="startQuiz()">Пройти тест</button>
        <button class="secondary" onclick="nextTest()">Следующий</button>
        <button class="secondary" onclick="returnToChannel()">Вернуться на канал</button>
      </div>
    </section>
  `;
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

      <div class="quiz-actions">
        <button class="secondary" onclick="goToFirstTest()">Все тесты</button>
        <button class="secondary" onclick="nextTest()">Следующий</button>
        <button class="secondary" onclick="returnToChannel()">Вернуться в канал</button>
      </div>
    </section>
  `;
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
      <button class="secondary" onclick="shareResult()">Поделиться результатом</button>
      <button class="secondary" onclick="nextTest()">Следующий тест</button>
      <button class="secondary" onclick="returnToChannel()">Вернуться на канал</button>
    </section>
  `;
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
