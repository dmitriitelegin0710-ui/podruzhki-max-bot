"""
Скрипт публикации в MAX — по рубрикам вместо Google Таблицы.
Расписание и темы рубрик — в rubrics.json (лежит рядом с этим файлом).
Текст поста для каждой рубрики генерирует YandexGPT.
Картинка — через Pexels Photos API, видео — через Pexels Videos API
(один и тот же PEXELS_API_KEY на оба).
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
STATE_FILE = "posted_rubrics.json"
TIMEZONE = "Europe/Moscow"
API_BASE = "https://platform-api2.max.ru"
YANDEXGPT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
DEFAULT_BUTTON_TEXT = "Читать на сайте"

POST_STRUCTURE_INSTRUCTIONS = """
Ты — автор постов для женского паблика в мессенджере MAX. Пиши по-русски, живо и по-доброму,
без канцелярита.

Не используй хэштеги. Не придумывай цитаты реальных людей.
Не добавляй никаких ссылок в текст — ссылка (если нужна) добавляется отдельно кнопкой.
НЕ используй Markdown-разметку вообще: никаких звёздочек (**жирный**), решёток (### заголовок),
подчёркиваний или обратных кавычек. Пиши обычным простым текстом.
"""

# Длина и структура поста теперь разная по рубрикам — задаётся полем "length" в rubrics.json,
# чтобы посты не были все одного шаблонного размера.
LENGTH_INSTRUCTIONS = {
    "short": (
        "Формат — короткий пост на 60-100 слов. Заголовок (короткая цепляющая фраза) "
        "и 1-2 небольших абзаца по смыслу. Без лишнего многословия, только суть."
    ),
    "medium": (
        "Формат — пост на 150-200 слов. Структура: заголовок, затем 2 абзаца "
        "(вводит тему → раскрывает суть/совет), в конце — короткий вывод или вопрос к читательницам. "
        "Абзацы разделяй пустой строкой."
    ),
    "long": (
        "Формат — развёрнутый пост на 220-280 слов. Структура: заголовок, затем 3 абзаца "
        "(вводит тему → раскрывает суть, при необходимости с пунктами через «•» → практический пример), "
        "в конце — короткий вывод. Абзацы разделяй пустой строкой."
    ),
}


def load_rubrics():
    with open(RUBRICS_FILE, encoding="utf-8") as f:
        return json.load(f)["rubrics"]


def load_state() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_state(state: set) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(state), f, ensure_ascii=False)


def strip_markdown(text: str) -> str:
    """Подстраховка на случай, если YandexGPT всё же добавит Markdown-разметку,
    несмотря на прямой запрет в промпте — убираем символы, оставляя сам текст."""
    text = re.sub(r'#{1,6}\s*', '', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'_(.+?)_', r'\1', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    return text.strip()


def generate_text(rubric: dict, weekday_name: str) -> str:
    length_key = rubric.get("length", "medium")
    length_instruction = LENGTH_INSTRUCTIONS.get(length_key, LENGTH_INSTRUCTIONS["medium"])

    prompt = (
        f"{POST_STRUCTURE_INSTRUCTIONS}\n\n"
        f"{length_instruction}\n\n"
        f"Рубрика: {rubric['title']}\n"
        f"Сегодня: {weekday_name}\n"
        f"Тема поста: {rubric['topic_hint']}"
    )
    body = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite/latest",
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
    return strip_markdown(raw_text)


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
    """Поиск короткого бесплатного видео через официальный Pexels Videos API —
    тот же ключ, что и для фото, та же лицензия (свободное использование).
    Видео почти всегда без осмысленного звука — используется как фоновая картинка."""
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
            print(f"Pexels Videos: по запросу '{keyword}' ничего не нашлось")
            return None
        video = random.choice(videos)
        video_files = sorted(video.get("video_files", []), key=lambda f: f.get("width") or 0)
        candidates = [f for f in video_files if (f.get("width") or 0) <= 1280] or video_files
        if not candidates:
            return None
        return candidates[-1]["link"]
    except Exception as e:
        print(f"Pexels Videos: ошибка запроса ({keyword}) — {e}")
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
    filename = "image.jpg" if media_type == "image" else "video.mp4"
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
    payload = {"text": text}
    if attachments:
        payload["attachments"] = attachments
    return requests.post(url, headers=headers, json=payload, timeout=30)


def get_media_keywords(rubric: dict, weekday_index: int, media_type: str):
    if media_type == "video":
        by_weekday = rubric.get("video_keywords_by_weekday")
        if by_weekday:
            return by_weekday.get(str(weekday_index), [])
        return rubric.get("video_keywords", [])
    else:
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

    print(f"Текущее время по Москве: {now.strftime('%d.%m.%Y %H:%M')} ({weekday_name})")

    rubrics = load_rubrics()
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
            text = f"{rubric['emoji']} " + generate_text(rubric, weekday_name)
        except Exception as e:
            print(f"Рубрика {rubric['key']}: ошибка генерации текста — {e}. Пропускаю на этот раз.")
            continue

        attachments = []
        media_type = rubric.get("media_type", "image")
        keywords = get_media_keywords(rubric, weekday_index, media_type)

        if media_type == "video":
            media_url = fetch_pexels_video(keywords)
        else:
            media_url = fetch_pexels_image(keywords)

        if media_url:
            token = upload_media_and_get_token(media_url, media_type)
            if token:
                attachments.append({"type": media_type, "payload": {"token": token}})

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
