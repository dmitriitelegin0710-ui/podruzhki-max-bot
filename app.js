const TEST_URL = new URLSearchParams(location.search).get("test") || "test-001.md";

let quiz = null;
let current = 0;
let answers = [];

const app = document.getElementById("app");

function esc(s) {
  return String(s).replace(/[&<>"']/g, ch => ({
    "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;"
  }[ch]));
}

function parseMarkdown(md) {
  const lines = md.replace(/\r/g, "").split("\n");
  let title = "";
  let hook = "";
  let questions = [];
  let results = {};
  let section = "";
  let currentQ = null;
  let currentResult = null;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();

    if (line.startsWith("# ")) {
      title = line.slice(2).trim();
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
    throw new Error("Не удалось найти заголовок или вопросы в Markdown.");
  }

  for (const q of questions) {
    for (const letter of ["A","B","C","D"]) {
      if (!q.options[letter]) {
        throw new Error(`В вопросе ${q.number} отсутствует вариант ${letter}.`);
      }
    }
  }

  return { title, hook, questions, results };
}

function renderStart() {
  app.innerHTML = `
    <section class="card start">
      <div class="badge">ТЕСТ</div>
      <h1>${esc(quiz.title)}</h1>
      ${quiz.hook ? `<p class="hook">${esc(quiz.hook)}</p>` : ""}
      <div class="meta">${quiz.questions.length} вопросов · 4 варианта ответа</div>
      <button class="primary" onclick="startQuiz()">Начать тест</button>
    </section>
  `;
}

window.startQuiz = function() {
  current = 0;
  answers = [];
  renderQuestion();
};

function renderQuestion() {
  const q = quiz.questions[current];
  const letters = ["A","B","C","D"];

  app.innerHTML = `
    <section class="card">
      <div class="progress-row">
        <span>Вопрос ${current + 1} из ${quiz.questions.length}</span>
        <span>${Math.round((current / quiz.questions.length) * 100)}%</span>
      </div>
      <div class="progress"><div style="width:${(current / quiz.questions.length) * 100}%"></div></div>

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

  // При равенстве используется ответ на последнем вопросе — как указано в вашем MD.
  const resultKey = winners.includes(answers[answers.length - 1])
    ? answers[answers.length - 1]
    : winners[0];

  const result = quiz.results[resultKey];

  app.innerHTML = `
    <section class="card result">
      <div class="badge">ТВОЙ РЕЗУЛЬТАТ</div>
      <div class="score">${max} из ${quiz.questions.length}</div>
      <h1>${esc(result ? result.title : resultKey)}</h1>
      <p>${esc(result ? result.text : "Результат определён по вашим ответам.")}</p>
      <button class="primary" onclick="startQuiz()">Пройти ещё раз</button>
    </section>
  `;
}

async function loadQuiz() {
  try {
    const response = await fetch(`tests/${encodeURIComponent(TEST_URL)}`);
    if (!response.ok) throw new Error(`Файл не найден: tests/${TEST_URL}`);
    const md = await response.text();
    quiz = parseMarkdown(md);
    renderStart();
  } catch (error) {
    app.innerHTML = `
      <section class="card error">
        <h1>Не удалось загрузить тест</h1>
        <p>${esc(error.message)}</p>
        <p>Проверьте, что файл лежит в папке <b>tests/</b>.</p>
      </section>
    `;
  }
}

loadQuiz();
