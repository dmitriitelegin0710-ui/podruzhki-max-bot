"""
Скрипт публикации в MAX — по рубрикам вместо Google Таблицы.
Расписание и темы рубрик — в rubrics.json (лежит рядом с этим файлом).
Текст поста для каждой рубрики генерирует YandexGPT.
Картинка — через Pexels Photos API.
Отправка в MAX — тот же API, что и раньше.

Запускается по расписанию через GitHub Actions (см. rubric_post.yml),
раз в 30 минут проверяет, не пора ли публиковать очередную рубрику.

Требуемые GitHub Secrets:
  MAX_BOT_TOKEN, MAX_CHAT_ID, PEXELS_API_KEY   — как и раньше
  YANDEX_API_KEY, YANDEX_FOLDER_ID             — для YandexGPT
"""
import json
import os
import random
import re
from datetime import datetime

import requests
import pytz

# ---- НАСТРОЙКИ ----
BOT_TOKEN = os.environ["MAX_BOT_TOKEN"]
CHAT_ID = os.environ["MAX_CHAT_ID"]
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")
YANDEX_API_KEY = os.environ["YANDEX_API_KEY"]
YANDEX_FOLDER_ID = os.environ["YANDEX_FOLDER_ID"]

RUBRICS_FILE = "rubrics.json"
HOLIDAYS_FILE = "max_women_holidays.json"
STATE_FILE = "posted_rubrics.json"
TAROT_FILE = "tarot_deck_78.json"
NUMEROLOGY_FILE = "numerology.json"
PALMISTRY_FILE = "palmistry.json"
OMENS_FILE = "omens.json"
WOMEN_STORIES_FILE = "women_success_stories.json"
TIMEZONE = "Europe/Moscow"
API_BASE = "https://platform-api2.max.ru"
YANDEXGPT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
# Версия модели закреплена явно (не "latest"), чтобы Яндекс не мог молча
# подменить её на другую ревизию/уровень мощности.
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

# Длина и структура поста разная по рубрикам — задаётся полем "length" в rubrics.json,
# чтобы посты не были все одного шаблонного размера.
LENGTH_INSTRUCTIONS = {
    "short": (
        "Формат — короткий пост на 40-60 слов. Заголовок (короткая цепляющая фраза) "
        "и 1-2 небольших абзаца по смыслу. Без лишнего многословия, только суть."
    ),
    "medium": (
        "Формат — пост на 60-80 слов. Структура: заголовок, затем 2 абзаца "
        "(вводит тему → раскрывает суть/совет), в конце — короткий вывод или вопрос к читательницам. "
        "Абзацы разделяй пустой строкой."
    ),
    "long": (
        "Формат — развёрнутый пост на 80-120 слов. Структура: заголовок, затем 3 абзаца "
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
    """Общий загрузчик для новых справочников рубрики «Эзотерика»
    (tarot_deck_78.json, numerology.json, palmistry.json, omens.json)."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _weekday_number(name: str) -> int:
    return {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }[name]


def _month_number(name: str) -> int:
    return {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }[name]


def _matches_floating_rule(rule: str, target_date) -> bool:
    # Поддерживаются только правила, реально записанные в max_women_holidays.json.
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

    ordinal_number = {
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
    }[ordinal]
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

    # Сначала используем priority из самого JSON.
    priority_order = holidays.get(
        "priority_order",
        ["very_high", "high", "medium", "low"],
    )
    priority_rank = {
        value: index for index, value in enumerate(priority_order)
    }

    # При одинаковом priority сохраняем порядок записей в JSON.
    return min(
        enumerate(candidates),
        key=lambda pair: (priority_rank.get(pair[1].get("priority"), len(priority_rank)), pair[0]),
    )[1]


def format_holiday_paragraph(holiday: dict) -> str:
    return f"\n\n🌷 **А ещё сегодня — {holiday['name']}!**\n{holiday['text']}"


def calculate_numerology_number(target_date) -> int:
    """Число дня по методу Пифагора — считается из сегодняшней календарной даты,
    а НЕ из даты рождения читательницы (в MAX нет формы ввода)."""
    digits = [int(ch) for ch in target_date.strftime("%d%m%Y")]
    total = sum(digits)
    while total > 9 and total not in (11, 22, 33):
        total = sum(int(ch) for ch in str(total))
    return total


def get_ezoterika_topic_hint(weekday_index: int, target_date) -> str:
    """Рубрика «Эзотерика и Таро» ротирует источник по дням недели:
    пн/пт — карта Таро, вт/сб — число дня (нумерология), ср — линия ладони
    (хиромантия), чт/вс — народная примета. Реальный факт из JSON подставляется
    в промпт, чтобы YandexGPT раскрывал его, а не выдумывал что-то от себя."""
    rng = random.Random(f"{target_date.isoformat()}-ezoterika")

    if weekday_index in (0, 4):  # понедельник, пятница — Таро
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

    if weekday_index in (1, 5):  # вторник, суббота — нумерология
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

    if weekday_index == 2:  # среда — хиромантия
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

    # четверг, воскресенье — народные приметы
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
    """Пост для рубрики «История сильной женщины» собирается напрямую из
    women_success_stories.json — БЕЗ обращения к YandexGPT. Только реальные факты,
    оформленные под фирменный стиль канала (эмодзи, жирный текст MAX-разметки, структура).
    История дня выбирается по номеру календарного дня — полный проход по списку без
    повторов, прежде чем начать заново."""
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
    """MAX умеет показывать **жирный**, _курсив_ и ++подчёркивание++ — это НЕ вырезаем.
    Убираем только то, что MAX не поддерживает или что модель иногда добавляет по ошибке:
    markdown-заголовки (### ...), код в обратных кавычках, зачёркивание, и одиночные
    "лишние" звёздочки-маркеры списков (модели иногда тянет писать "* пункт" вместо "• пункт")."""
    text = re.sub(r'#{1,6}\s*', '', text)
    text = re.sub(r'`{1,3}(.+?)`{1,3}', r'\1', text)
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    # одиночная "*", не входящая в пару "**", — это случайный маркер списка, а не разметка
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


def fetch_pexels_image(keywords: list):
    """Случайное фото из нескольких результатов (не всегда первое) —
    чтобы уменьшить повторы картинок день ото дня."""
    if not keywords or not PEXELS_API_KEY:
        return None
    keyword = random.choice(keywords)
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
            print(f"Pexels: по запросу '{keyword}' ничего не нашлось")
            return None
        return random.choice(photos)["src"]["large"]
    except Exception as e:
        print(f"Pexels: ошибка запроса ({keyword}) — {e}")
        return None


def fetch_pexels_video(keywords: list):
    """Короткое вертикальное видео с Pexels Videos (тот же PEXELS_API_KEY, что и для фото,
    отдельного секрета не требуется). Возвращает прямую ссылку на mp4-файл."""
    if not keywords or not PEXELS_API_KEY:
        return None
    keyword = random.choice(keywords)
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
            print(f"Pexels video: по запросу '{keyword}' ничего не нашлось")
            return None
        video = random.choice(videos)
        mp4_files = [vf for vf in (video.get("video_files") or []) if vf.get("file_type") == "video/mp4"]
        if not mp4_files:
            return None
        # берём файл с шириной ближе к 720px — не самое тяжёлое и не самое мыльное качество
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
        # Для video/audio ответ загрузки — служебный XML без токена (он уже был в meta выше),
        # поэтому json() здесь ожидаемо может не сработать для видео — это нормально.
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
    # format="markdown" — включает реальное форматирование MAX (**bold**, ++underline++, _italic_)
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
    """Если для рубрики не заданы отдельные ключевые слова под видео
    (video_keywords / video_keywords_by_weekday), используются те же слова, что для фото."""
    by_weekday = rubric.get("video_keywords_by_weekday")
    if by_weekday:
        return by_weekday.get(str(weekday_index), [])
    if rubric.get("video_keywords"):
        return rubric["video_keywords"]
    return get_photo_keywords(rubric, weekday_index)


def fetch_and_upload_media(rubric: dict, weekday_index: int):
    """Готовит вложение (фото или видео) для поста. Поведение управляется
    необязательным полем rubric["media"]:
      не задано / "photo" — как раньше, только фото с Pexels Photos;
      "video"  — только короткое видео с Pexels Videos;
      "random" — вероятность 40% на видео, иначе фото; если предпочтённый тип
        не нашёлся (пустой результат поиска), подстраховываемся вторым типом,
        чтобы пост не остался совсем без картинки.
    Возвращает готовый attachment-словарь либо None."""

    def try_photo():
        url = fetch_pexels_image(get_photo_keywords(rubric, weekday_index))
        if not url:
            return None
        token = upload_media_and_get_token(url, media_type="image")
        return {"type": "image", "payload": {"token": token}} if token else None

    def try_video():
        url = fetch_pexels_video(get_video_keywords(rubric, weekday_index))
        if not url:
            return None
        token = upload_media_and_get_token(url, media_type="video")
        return {"type": "video", "payload": {"token": token}} if token else None

    media_mode = rubric.get("media", "photo")

    if media_mode == "video":
        return try_video() or try_photo()
    if media_mode == "random":
        if random.random() < 0.4:
            return try_video() or try_photo()
        return try_photo() or try_video()
    return try_photo()


def main():
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    today_str = now.strftime("%Y-%m-%d")
    weekday_index = now.weekday()  # 0 = понедельник
    weekday_names = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    weekday_name = weekday_names[weekday_index]
    date_human = f"{now.day} {MONTHS_RU[now.month - 1]} {now.year} года"
    season = get_season(now.month)

    print(f"Текущее время по Москве: {now.strftime('%d.%m.%Y %H:%M')} ({weekday_name}, {season})")

    rubrics = load_rubrics()
    holidays = load_holidays()
    state = load_state()
    changed = False

    for rubric in rubrics:
        record_key = f"{today_str}_{rubric['key']}"
        if record_key in state:
            continue

        # Рубрики с полем "days" публикуются только в указанные дни недели
        # (0 = понедельник ... 6 = воскресенье). Рубрики без этого поля —
        # ежедневные "якоря", их поведение не меняется.
        allowed_days = rubric.get("days")
        if allowed_days is not None and weekday_index not in allowed_days:
            continue

        scheduled_h, scheduled_m = (int(p) for p in rubric["time"].split(":"))
        scheduled_dt = now.replace(hour=scheduled_h, minute=scheduled_m, second=0, microsecond=0)

        if now < scheduled_dt:
            continue

        print(f"Готовлю пост для рубрики: {rubric['title']} ({rubric['key']})")

        try:
            if rubric["key"] == "istoriya_zhenshiny":
                text = build_istoriya_zhenshiny_post(now.date())
            else:
                active_rubric = rubric
                if rubric["key"] == "ezoterika":
                    try:
                        ezoterika_topic = get_ezoterika_topic_hint(weekday_index, now.date())
                        active_rubric = {**rubric, "topic_hint": ezoterika_topic}
                    except Exception as e:
                        print(
                            f"Рубрика ezoterika: не удалось подготовить факт из JSON ({e}), "
                            "публикую с topic_hint по умолчанию"
                        )
                text = f"{rubric['emoji']} " + generate_text(active_rubric, weekday_name, date_human, season)

            if rubric["key"] == "utro_privet":
                holiday = get_holiday_for_date(now.date(), holidays)
                if holiday:
                    text += format_holiday_paragraph(holiday)
                    print(f"Праздник на сегодня: {holiday['name']}")
        except Exception as e:
            print(f"Рубрика {rubric['key']}: ошибка генерации текста — {e}. Пропускаю на этот раз.")
            continue

        attachments = []
        try:
            media_attachment = fetch_and_upload_media(rubric, weekday_index)
            if media_attachment:
                attachments.append(media_attachment)
        except Exception as e:
            print(f"Рубрика {rubric['key']}: ошибка при подготовке медиа — {e}. Публикую без него.")

        button = build_link_button_attachment(rubric.get("site_link"))
        if button:
            attachments.append(button)

        response = send_message(text, attachments or None)
        print(f"Рубрика {rubric['key']}: статус {response.status_code}, ответ: {response.text[:200]}")

        if response.status_code == 200:
            state.add(record_key)
            changed = True
        else:
            print(f"Рубрика {rubric['key']}: НЕ опубликовано, проверьте токены/права бота")

    if changed:
        save_state(state)
    else:
        print("Подходящих рубрик на этот запуск не найдено")


if __name__ == "__main__":
    main()
