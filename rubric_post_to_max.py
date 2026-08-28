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


def upload_media_and_get_token(media_url: str):
    meta_resp = requests.post(
        f"{API_BASE}/uploads",
        params={"type": "image"},
        headers={"Authorization": BOT_TOKEN},
        timeout=30,
    )
    meta_resp.raise_for_status()
    meta = meta_resp.json()
    upload_url = meta["url"]
    token = meta.get("token")

    media_bytes = requests.get(media_url, timeout=60).content
    files = {"data": ("image.jpg", media_bytes)}
    upload_resp = requests.post(upload_url, files=files, timeout=120)
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

        scheduled_h, scheduled_m = (int(p) for p in rubric["time"].split(":"))
        scheduled_dt = now.replace(hour=scheduled_h, minute=scheduled_m, second=0, microsecond=0)

        if now < scheduled_dt:
            continue

        print(f"Готовлю пост для рубрики: {rubric['title']} ({rubric['key']})")

        try:
            text = f"{rubric['emoji']} " + generate_text(rubric, weekday_name, date_human, season)

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
            keywords = get_photo_keywords(rubric, weekday_index)
            image_url = fetch_pexels_image(keywords)
            if image_url:
                token = upload_media_and_get_token(image_url)
                if token:
                    attachments.append({"type": "image", "payload": {"token": token}})
        except Exception as e:
            print(f"Рубрика {rubric['key']}: ошибка при подготовке фото — {e}. Публикую без него.")

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
