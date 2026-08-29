const DEFAULT_TEST = 1;
const TOTAL_TESTS = 24;
const SITE_BUTTON_TEXT = "Все тесты на сайте";

let tests = [];
let testIndex = Math.max(0, Math.min(TOTAL_TESTS - 1, Number(new URLSearchParams(location.search).get("test") || DEFAULT_TEST) - 1));
let quiz = null;
let current = 0;
let answers = [];

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
        <button class="secondary" onclick="nextTest()">Следующий тест</button>
        <button class="secondary site-placeholder" disabled title="Кнопка-заглушка">
          ${SITE_BUTTON_TEXT}
        </button>
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
  const url = new URL(location.href);
  url.searchParams.set("test", String(testIndex + 1));
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

  app.innerHTML = `
    <section class="card result">
      <div class="badge">ТЕСТ ${testIndex + 1} ИЗ ${tests.length}</div>
      <div class="score">${max} из ${quiz.questions.length}</div>
      <h1>${esc(result ? result.title : resultKey)}</h1>
      <p>${esc(result ? result.text : "Результат определён по вашим ответам.")}</p>

      <button class="primary" onclick="startQuiz()">Пройти ещё раз</button>
      <button class="secondary" onclick="nextTest()">Следующий тест</button>
      <button class="secondary site-placeholder" disabled title="Кнопка-заглушка">
        ${SITE_BUTTON_TEXT}
      </button>
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
