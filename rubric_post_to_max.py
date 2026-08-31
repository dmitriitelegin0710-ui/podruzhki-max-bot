"""
Скрипт публикации в MAX — по рубрикам вместо Google Таблицы.
Расписание и темы рубрик — в rubrics.json (лежит рядом с этим файлом).
Текст поста для каждой рубрики генерирует YandexGPT.
Картинка — через Pexels Photos API.
Отправка в MAX — тот же API, что и раньше.

Рубрика "Тест дня" (test_dnya) — исключение: текст берётся не от YandexGPT,
а напрямую из miniapp/tests/test-001.md (тот же файл, что парсит app.js в
mini app), чтобы название и хук теста в канале и в mini app совпадали
всегда, без ручного дублирования данных.

Кнопка "Пройти тест" теперь открывает сайт как настоящее Mini App
(type=open_app), а не как обычную ссылку (type=link). Раньше клик по
кнопке открывал сайт во внешнем браузере (Opera и т.п.) — там window.WebApp
не привязан к реальной сессии MAX, и close()/openMaxLink()/shareMaxContent()
не работают. Через open_app сайт запускается внутри MAX по-настоящему,
и мост MAX Bridge (window.WebApp) активен.

Запускается по расписанию через GitHub Actions (см. rubric_post.yml),
раз в 30 минут проверяет, не пора ли публиковать очередную рубрику.

--- ПОДБОР ФОТО ---
После того как текст поста уже сгенерирован, отдельным лёгким запросом к
YandexGPT (generate_photo_keywords) он переводится в 2-3 английских
ключевых слова, максимально точно описывающих СМЫСЛ именно сегодняшнего
текста. Эти динамические слова пробуются для поиска на Pexels в первую
очередь, а статичные слова из rubrics.json остаются запасным вариантом.

--- НОВОЕ: публикация в Telegram ---
Если заданы переменные окружения TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID (см.
telegram_common.py, лежит рядом, в корне репозитория), после успешной
публикации КАЖДОЙ рубрики в MAX тот же самый пост (тот же текст, то же
фото/видео) параллельно уходит и в Telegram-канал — полный дубль
содержимого MAX-канала. Если секреты не заданы — публикация в Telegram
просто пропускается, ничего не ломая. Как и в новостях, MAX остаётся
источником истины для "опубликовано/не опубликовано": неуспех в Telegram
только логируется.
Функция fetch_and_upload_media теперь возвращает ещё и исходный URL медиа
(до загрузки в MAX, в виде токена) — раньше этот URL терялся сразу после
загрузки, а для Telegram он нужен, чтобы скачать то же самое изображение/
видео ещё раз.

Требуемые GitHub Secrets:
  MAX_BOT_TOKEN, MAX_CHAT_ID, PEXELS_API_KEY   — как и раньше
  YANDEX_API_KEY, YANDEX_FOLDER_ID             — для YandexGPT
  MAX_BOT_USERNAME                             — юзернейм бота без "@"
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID         — новое, опционально
"""
import json
import os
import random
import re
from datetime import datetime

import requests
import pytz

import telegram_common

# ---- НАСТРОЙКИ ----
BOT_TOKEN = os.environ["MAX_BOT_TOKEN"]
CHAT_ID = os.environ["MAX_CHAT_ID"]
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")
YANDEX_API_KEY = os.environ["YANDEX_API_KEY"]
YANDEX_FOLDER_ID = os.environ["YANDEX_FOLDER_ID"]
BOT_USERNAME = os.environ["MAX_BOT_USERNAME"]

RUBRICS_FILE = "rubrics.json"
HOLIDAYS_FILE = "max_women_holidays.json"
STATE_FILE = "posted_rubrics.json"
TAROT_FILE = "tarot_deck_78.json"
NUMEROLOGY_FILE = "numerology.json"
PALMISTRY_FILE = "palmistry.json"
OMENS_FILE = "omens.json"
WOMEN_STORIES_FILE = "women_success_stories.json"

TESTS_MD_FILE = "miniapp/tests/test-001.md"
TOTAL_TESTS = 24
MINIAPP_BASE_URL = "https://xn--d1aeghrfjy.online/max/"
SITE_TESTS_PLACEHOLDER_LINE = "📚 Все тесты — совсем скоро здесь появится ссылка на сайт"

TIMEZONE = "Europe/Moscow"
API_BASE = "https://platform-api2.max.ru"
YANDEXGPT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
YANDEXGPT_MODEL_URI_TEMPLATE = "gpt://{folder_id}/yandexgpt-lite/rc"
DEFAULT_BUTTON_TEXT = "Читать на сайте"

MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

POST_STRUCTURE_INSTRUCTIONS = """
Ты — автор постов для женского паблика в мессенджере MAX. Пиши по-русски, живо, тепло и
визуально ярко — как реальные популярные женские паблики, а не сухим текстом сплошными абзацами.

Не используй хэштеги. Не придумывай цитаты реальных людей.
Не добавляй никаких ссылок в текст — ссылка (если нужна) добавляется отдельно кнопкой.

РАЗМЕТКА (мессенджер MAX поддерживает её нативно, форматирование реально появится в посте):
  **жирный текст** — используй активно: для заголовка поста, для каждого смыслового
    подзаголовка внутри и для 2-4 ключевых фраз по тексту. Не бойся выделять больше, чем
    кажется достаточным — жирный текст держит внимание читателя.
  _курсив_ — для лёгкого смыслового акцента, несколько раз по тексту
  ++подчёркивание++ — для одной самой важной мысли или вывода в посте, максимум 1-2 раза

ЭМОДЗИ — используй свободно и по смыслу на протяжении всего поста (не только в самом начале):
  в заголовке/подзаголовках, рядом с ключевыми словами по теме, в начале пунктов списка.
  Ориентируйся на 4-8 эмодзи на пост в зависимости от длины — столько, сколько органично
  смотрится в реальных постах популярных женских пабликов.

Если по смыслу подходит список — оформляй его как перечень с эмодзи или "•" в начале
каждого пункта, а не сплошным текстом в одном абзаце.

НЕ используй заголовки через "#", НЕ используй обратные кавычки для кода, НЕ используй "*"
как маркер списка.
Абзацы разделяй пустой строкой.
"""

LENGTH_INSTRUCTIONS = {
    "short": (
        "Формат — короткий пост на 20-40 слов. Заголовок (короткая цепляющая фраза) "
        "и 1-2 небольших абзаца по смыслу. Без лишнего многословия, только суть."
    ),
    "medium": (
        "Формат — пост на 40-60 слов. Структура: заголовок, затем 2 абзаца "
        "(вводит тему → раскрывает суть/совет), в конце — короткий вывод или вопрос к читательницам. "
        "Абзацы разделяй пустой строкой."
    ),
    "long": (
        "Формат — развёрнутый пост на 60-100 слов. Структура: заголовок, затем 3 абзаца "
        "(вводит тему → раскрывает суть, при необходимости с пунктами через «•» → практический пример), "
        "в конце — короткий вывод. Абзацы разделяй пустой строкой."
    ),
}


def load_rubrics():
    with open(RUBRICS_FILE, encoding="utf-8") as f:
        return json.load(f)["rubrics"]


def load_holidays() -> dict:
    with open(HOLIDAYS_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_json_file(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _weekday_number(name: str) -> int:
    return {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }[name]


def _month_number(name: str) -> int:
    return {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    }[name]


def _matches_floating_rule(rule: str, target_date) -> bool:
    if rule == "256th_day_of_year":
        return target_date.timetuple().tm_yday == 256

    match = re.fullmatch(
        r"(first|second|third|fourth|last)_(monday|tuesday|wednesday|thursday|friday|saturday|sunday)_of_"
        r"(january|february|march|april|may|june|july|august|september|october|november|december)",
        rule,
    )
    if not match:
        return False

    ordinal, weekday_name, month_name = match.groups()
    month = _month_number(month_name)
    weekday = _weekday_number(weekday_name)

    if target_date.month != month or target_date.weekday() != weekday:
        return False

    if ordinal == "last":
        next_week = target_date + __import__("datetime").timedelta(days=7)
        return next_week.month != month

    ordinal_number = {"first": 1, "second": 2, "third": 3, "fourth": 4}[ordinal]
    return ((target_date.day - 1) // 7 + 1) == ordinal_number


def get_holiday_for_date(target_date, holidays: dict):
    candidates = []

    date_key = target_date.strftime("%m-%d")
    candidates.extend(holidays.get("dates", {}).get(date_key, []))

    for item in holidays.get("floating_dates", []):
        if _matches_floating_rule(item.get("rule", ""), target_date):
            candidates.append(item)

    if not candidates:
        return None

    priority_order = holidays.get("priority_order", ["very_high", "high", "medium", "low"])
    priority_rank = {value: index for index, value in enumerate(priority_order)}

    return min(
        enumerate(candidates),
        key=lambda pair: (priority_rank.get(pair[1].get("priority"), len(priority_rank)), pair[0]),
    )[1]


def format_holiday_paragraph(holiday: dict) -> str:
    return f"\n\n🌷 **А ещё сегодня — {holiday['name']}!**\n{holiday['text']}"


def calculate_numerology_number(target_date) -> int:
    digits = [int(ch) for ch in target_date.strftime("%d%m%Y")]
    total = sum(digits)
    while total > 9 and total not in (11, 22, 33):
        total = sum(int(ch) for ch in str(total))
    return total


def get_ezoterika_topic_hint(weekday_index: int, target_date) -> str:
    rng = random.Random(f"{target_date.isoformat()}-ezoterika")

    if weekday_index in (0, 4):
        deck = load_json_file(TAROT_FILE)
        all_cards = list(deck["major_arcana"])
        for suit_cards in deck["minor_arcana"].values():
            all_cards.extend(suit_cards)
        card = rng.choice(all_cards)
        position = rng.choice(["upright", "reversed"])
        position_ru = "прямое положение" if position == "upright" else "перевёрнутое положение"
        sphere = card[position]
        return (
            "Разбери карту дня Таро для читательниц женского паблика. Строго используй "
            "только перечисленные ниже факты, не меняй название карты и положение, "
            "не выдумывай другие значения:\n"
            f"Карта: {card['name']} ({position_ru}).\n"
            f"Общее значение: {sphere['general']}.\n"
            f"В любви: {sphere['love']}.\n"
            f"В карьере: {sphere['career']}.\n"
            f"В деньгах: {sphere['money']}."
        )

    if weekday_index in (1, 5):
        numerology = load_json_file(NUMEROLOGY_FILE)
        number = calculate_numerology_number(target_date)
        entry = numerology["numbers"][str(number)]
        return (
            "Разбери «число дня» в нумерологии — это универсальное число сегодняшнего "
            "дня (рассчитано из календарной даты), а НЕ число по дате рождения конкретного "
            "человека, поэтому не проси и не упоминай дату рождения читательниц. Строго "
            "используй только факты ниже, не выдумывай других значений:\n"
            f"Число дня: {number} — «{entry['title']}».\n"
            f"{entry['text']}\n"
            f"В любви: {entry['love']}\n"
            f"В карьере: {entry['career']}"
        )

    if weekday_index == 2:
        palmistry = load_json_file(PALMISTRY_FILE)
        line_keys = list(palmistry["lines"].keys())
        line_key = line_keys[target_date.isocalendar()[1] % len(line_keys)]
        line = palmistry["lines"][line_key]
        return (
            "Расскажи читательницам про эту линию ладони в хиромантии простым языком, "
            "объясни, как её найти на своей руке. Строго используй только факты ниже, "
            "не выдумывай другие линии и толкования:\n"
            f"Линия: {line['name']}.\n"
            f"Расположение: {line['location']}.\n"
            f"Значение: {line['meaning_general']}"
        )

    omens = load_json_file(OMENS_FILE)
    category = rng.choice(list(omens["categories"].values()))
    item = rng.choice(category["items"])
    return (
        "Расскажи читательницам про эту народную примету в лёгком, развлекательном тоне. "
        "Строго используй только факт ниже, не выдумывай других примет:\n"
        f"Примета: «{item['sign']}» — {item['meaning']}"
    )


STORY_CLOSING_LINES = [
    "Её история — ещё одно напоминание: сила не в отсутствии трудностей, а в том, как их проходишь.",
    "Иногда самый обычный день становится точкой отсчёта для чего-то большого.",
    "Такие истории хочется перечитывать в моменты, когда опускаются руки.",
    "Трудности никуда не исчезают — просто однажды перестают быть главным препятствием.",
    "Вот что бывает, когда не сдаются даже тогда, когда шансов почти не видно.",
]


def build_istoriya_zhenshiny_post(target_date) -> str:
    stories = load_json_file(WOMEN_STORIES_FILE)["stories"]
    story = stories[target_date.toordinal() % len(stories)]
    closing = STORY_CLOSING_LINES[target_date.toordinal() % len(STORY_CLOSING_LINES)]

    return (
        f"{story.get('emoji', '🌟')} **{story['name']}**\n\n"
        f"{story['hook']}\n\n"
        f"**Трудность:** {story['challenge']}\n\n"
        f"**Что она сделала:** {story['achievement']}\n\n"
        f"_Сфера: {story['sphere']} · {story['era']}_\n\n"
        f"++{closing}++"
    )


def load_tests_from_md(path: str = TESTS_MD_FILE) -> list:
    with open(path, encoding="utf-8") as f:
        content = f.read()

    blocks = re.split(r"(?=^#\s+\d+\.\s+)", content, flags=re.MULTILINE)

    tests = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        title_match = re.match(r"^#\s+\d+\.\s+(.+)$", block, re.MULTILINE)
        if not title_match:
            continue

        hook_match = re.search(r"^Хук:\s*(.+)$", block, re.MULTILINE)
        tests.append({
            "title": title_match.group(1).strip(),
            "hook": hook_match.group(1).strip() if hook_match else "",
        })

    if len(tests) != TOTAL_TESTS:
        raise ValueError(
            f"Ожидалось {TOTAL_TESTS} теста(ов) в {path}, найдено {len(tests)}. "
            "Публикацию пропускаю, чтобы не отправить пустой/неверный пост."
        )
    return tests


def build_test_dnya_post(target_date):
    tests = load_tests_from_md()
    index = target_date.toordinal() % len(tests)
    test = tests[index]
    test_number = index + 1

    text = f"🧠 **Тест дня: {test['title']}**"
    if test["hook"]:
        text += f"\n\n{test['hook']}"
    return text, test_number


def build_test_dnya_attachments(test_number: int):
    return [{
        "type": "inline_keyboard",
        "payload": {"buttons": [[
            {
                "type": "open_app",
                "text": "Пройти этот и другие тесты 🧠",
                "web_app": BOT_USERNAME,
                "payload": f"test{test_number}",
            },
        ]]},
    }]


def load_state() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_state(state: set) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(state), f, ensure_ascii=False)


def get_season(month: int) -> str:
    if month in (12, 1, 2):
        return "зима"
    if month in (3, 4, 5):
        return "весна"
    if month in (6, 7, 8):
        return "лето"
    return "осень"


def clean_formatting(text: str) -> str:
    text = re.sub(r'#{1,6}\s*', '', text)
    text = re.sub(r'`{1,3}(.+?)`{1,3}', r'\1', text)
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    text = re.sub(r'(?<!\*)\*(?!\*)\s*', '', text)
    return text.strip()


def generate_text(rubric: dict, weekday_name: str, date_human: str, season: str) -> str:
    length_key = rubric.get("length", "medium")
    length_instruction = LENGTH_INSTRUCTIONS.get(length_key, LENGTH_INSTRUCTIONS["medium"])

    prompt = (
        f"{POST_STRUCTURE_INSTRUCTIONS}\n\n"
        f"Точная сегодняшняя дата: {date_human} ({weekday_name}). Время года сейчас: {season}.\n"
        f"Ориентируйся ТОЛЬКО на эту дату и это время года при любых упоминаниях дат, сезона, "
        f"праздников, планов на будущее, гардероба и т.п. Никогда не называй никакую другую "
        f"дату, день недели или время года — используй строго те, что указаны выше.\n\n"
        f"{length_instruction}\n\n"
        f"Рубрика: {rubric['title']}\n"
        f"Тема поста: {rubric['topic_hint']}"
    )
    body = {
        "modelUri": YANDEXGPT_MODEL_URI_TEMPLATE.format(folder_id=YANDEX_FOLDER_ID),
        "completionOptions": {"stream": False, "temperature": 0.7, "maxTokens": 800},
        "messages": [{"role": "user", "text": prompt}],
    }
    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(YANDEXGPT_URL, headers=headers, json=body, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    raw_text = data["result"]["alternatives"][0]["message"]["text"].strip()
    return clean_formatting(raw_text)


def generate_photo_keywords(post_text: str) -> list:
    if not post_text:
        return []

    plain_text = re.sub(r'[*_+#]', '', post_text).strip()[:700]
    if not plain_text:
        return []

    prompt = (
        "Ниже текст поста для женского паблика в мессенджере. Придумай 2-3 ключевых "
        "слова НА АНГЛИЙСКОМ ЯЗЫКЕ для поиска стоковой фотографии на Pexels, которая "
        "максимально точно иллюстрировала бы главную тему, действие и настроение именно "
        "этого текста (а не тему рубрики вообще). Ставь слова по порядку от самого "
        "точного и конкретного к более общему — если конкретное сочетание не найдётся "
        "на стоке, сработает более общее.\n"
        "Ответь СТРОГО в формате: keyword phrase one, keyword phrase two, keyword phrase three "
        "— без кавычек, без нумерации, без пояснений, только сами фразы через запятую.\n\n"
        f"Текст поста:\n{plain_text}"
    )
    body = {
        "modelUri": YANDEXGPT_MODEL_URI_TEMPLATE.format(folder_id=YANDEX_FOLDER_ID),
        "completionOptions": {"stream": False, "temperature": 0.3, "maxTokens": 60},
        "messages": [{"role": "user", "text": prompt}],
    }
    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(YANDEXGPT_URL, headers=headers, json=body, timeout=20)
        resp.raise_for_status()
        raw = resp.json()["result"]["alternatives"][0]["message"]["text"].strip()
        keywords = [kw.strip(" .\"'") for kw in raw.split(",") if kw.strip(" .\"'")]
        if keywords:
            print(f"Ключевые слова для фото по тексту поста: {keywords}")
            return keywords
    except Exception as e:
        print(f"Не удалось сгенерировать ключевые слова для фото по тексту поста — {e}")
    return []


def fetch_pexels_image(keywords: list):
    if not keywords or not PEXELS_API_KEY:
        return None
    for keyword in keywords:
        try:
            resp = requests.get(
                "https://api.pexels.com/v1/search",
                params={"query": keyword, "per_page": 10, "orientation": "portrait"},
                headers={"Authorization": PEXELS_API_KEY},
                timeout=20,
            )
            resp.raise_for_status()
            photos = resp.json().get("photos") or []
            if not photos:
                print(f"Pexels: по запросу '{keyword}' ничего не нашлось, пробую следующее слово")
                continue
            top_matches = photos[:3]
            return random.choice(top_matches)["src"]["large"]
        except Exception as e:
            print(f"Pexels: ошибка запроса ({keyword}) — {e}")
    return None


def fetch_pexels_video(keywords: list):
    if not keywords or not PEXELS_API_KEY:
        return None
    for keyword in keywords:
        try:
            resp = requests.get(
                "https://api.pexels.com/videos/search",
                params={"query": keyword, "per_page": 10, "orientation": "portrait"},
                headers={"Authorization": PEXELS_API_KEY},
                timeout=20,
            )
            resp.raise_for_status()
            videos = resp.json().get("videos") or []
            if not videos:
                print(f"Pexels video: по запросу '{keyword}' ничего не нашлось, пробую следующее слово")
                continue
            top_matches = videos[:3]
            video = random.choice(top_matches)
            mp4_files = [vf for vf in (video.get("video_files") or []) if vf.get("file_type") == "video/mp4"]
            if not mp4_files:
                continue
            chosen = min(mp4_files, key=lambda vf: abs((vf.get("width") or 0) - 720))
            return chosen.get("link")
        except Exception as e:
            print(f"Pexels video: ошибка запроса ({keyword}) — {e}")
    return None


def upload_media_and_get_token(media_url: str, media_type: str = "image"):
    meta_resp = requests.post(
        f"{API_BASE}/uploads",
        params={"type": media_type},
        headers={"Authorization": BOT_TOKEN},
        timeout=30,
    )
    meta_resp.raise_for_status()
    meta = meta_resp.json()
    upload_url = meta["url"]
    token = meta.get("token")

    media_bytes = requests.get(media_url, timeout=120).content
    filename = "video.mp4" if media_type == "video" else "image.jpg"
    files = {"data": (filename, media_bytes)}
    upload_resp = requests.post(upload_url, files=files, timeout=180)
    upload_resp.raise_for_status()

    if not token:
        try:
            body = upload_resp.json()
            token = body.get("token")
            if not token and "photos" in body:
                first_photo = next(iter(body["photos"].values()))
                token = first_photo.get("token")
        except Exception:
            pass
    return token


def build_link_button_attachment(site_url, button_text=None):
    if not site_url:
        return None
    text = button_text or DEFAULT_BUTTON_TEXT
    return {
        "type": "inline_keyboard",
        "payload": {"buttons": [[{"type": "link", "text": text, "url": site_url}]]},
    }


def send_message(text: str, attachments=None) -> requests.Response:
    url = f"{API_BASE}/messages?chat_id={CHAT_ID}"
    headers = {"Authorization": BOT_TOKEN}
    payload = {"text": text, "format": "markdown"}
    if attachments:
        payload["attachments"] = attachments
    return requests.post(url, headers=headers, json=payload, timeout=30)


def get_photo_keywords(rubric: dict, weekday_index: int):
    by_weekday = rubric.get("photo_keywords_by_weekday")
    if by_weekday:
        return by_weekday.get(str(weekday_index), [])
    return rubric.get("photo_keywords", [])


def get_video_keywords(rubric: dict, weekday_index: int):
    by_weekday = rubric.get("video_keywords_by_weekday")
    if by_weekday:
        return by_weekday.get(str(weekday_index), [])
    if rubric.get("video_keywords"):
        return rubric["video_keywords"]
    return get_photo_keywords(rubric, weekday_index)


def fetch_and_upload_media(rubric: dict, weekday_index: int, post_text: str = None):
    """Готовит вложение (фото или видео) для поста в MAX.
    ИЗМЕНЕНО: теперь возвращает кортеж (attachment, media_url, media_type)
    вместо одного attachment — media_url и media_type (image/video) нужны,
    чтобы то же самое медиа можно было отдельно отправить в Telegram через
    telegram_common.py, не теряя URL сразу после загрузки в MAX."""

    static_photo_keywords = get_photo_keywords(rubric, weekday_index)
    static_video_keywords = get_video_keywords(rubric, weekday_index)

    dynamic_keywords = generate_photo_keywords(post_text) if post_text and PEXELS_API_KEY else []

    photo_keywords = dynamic_keywords + static_photo_keywords
    video_keywords = dynamic_keywords + static_video_keywords

    def try_photo():
        url = fetch_pexels_image(photo_keywords)
        if not url:
            return None, None, None
        token = upload_media_and_get_token(url, media_type="image")
        attachment = {"type": "image", "payload": {"token": token}} if token else None
        return attachment, url, "image"

    def try_video():
        url = fetch_pexels_video(video_keywords)
        if not url:
            return None, None, None
        token = upload_media_and_get_token(url, media_type="video")
        attachment = {"type": "video", "payload": {"token": token}} if token else None
        return attachment, url, "video"

    media_mode = rubric.get("media", "photo")

    if media_mode == "video":
        result = try_video()
        return result if result[0] else try_photo()
    if media_mode == "random":
        if random.random() < 0.4:
            result = try_video()
            return result if result[0] else try_photo()
        result = try_photo()
        return result if result[0] else try_video()
