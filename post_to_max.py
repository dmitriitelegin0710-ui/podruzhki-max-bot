"""
Скрипт публикации постов из Google Таблицы в канал MAX.
Запускается по расписанию через GitHub Actions (см. .github/workflows/post.yml).

Столбцы, которые понимает скрипт:
  №, Дата, Время, Текст поста, Статус — обязательные
  Тип медиа            — нет / картинка / видео
  Ключевое слово для фото — на английском, для автоподбора через Pexels
                            (используется, только если "Ссылка на медиа" пустая)
  Ссылка на медиа       — прямая ссылка на картинку или видео (необязательна для картинки)
  Ссылка на сайт        — если заполнена, под постом появится кнопка со ссылкой
  Текст кнопки          — необязательно; если пусто, кнопка называется "Читать на сайте"
"""
import csv
import io
import json
import os
from datetime import datetime
import requests
import pytz

# ---- НАСТРОЙКИ ----
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vR-oJrZwXiwymusIpr6cRZ5GPNDiNWaFDeOvsFMuqe0herSVMpkEZcN2vYzBOibznyj3sG7IcINKYBq/pub?gid=1384020627&single=true&output=csv"

BOT_TOKEN = os.environ["MAX_BOT_TOKEN"]
CHAT_ID = os.environ["MAX_CHAT_ID"]
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")

STATE_FILE = "posted.json"
TIMEZONE = "Europe/Moscow"
STATUS_READY = "Черновик"
API_BASE = "https://platform-api2.max.ru"
DEFAULT_BUTTON_TEXT = "Читать на сайте"


def load_posted() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_posted(posted: set) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(posted, key=int), f, ensure_ascii=False)


def fetch_pexels_image(keyword: str):
    keyword = (keyword or "").strip()
    if not keyword or not PEXELS_API_KEY:
        if keyword and not PEXELS_API_KEY:
            print("PEXELS_API_KEY не задан — автоподбор картинки пропущен")
        return None
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": keyword, "per_page": 1, "orientation": "portrait"},
            headers={"Authorization": PEXELS_API_KEY},
            timeout=20,
        )
        resp.raise_for_status()
        photos = resp.json().get("photos") or []
        if not photos:
            print(f"Pexels: по запросу '{keyword}' ничего не нашлось")
            return None
        return photos[0]["src"]["large"]
    except Exception as e:
        print(f"Pexels: ошибка запроса ({keyword}) — {e}")
        return None


def upload_media_and_get_token(media_url: str, media_type: str):
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

    media_bytes = requests.get(media_url, timeout=60).content
    filename = "image.jpg" if media_type == "image" else "video.mp4"
    files = {"data": (filename, media_bytes)}
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


def build_media_attachment(media_type: str, media_url: str, photo_keyword: str):
    media_type = (media_type or "").strip().lower()
    media_url = (media_url or "").strip()

    if not media_type or media_type == "нет":
        return None

    if media_type == "картинка":
        url = media_url or fetch_pexels_image(photo_keyword)
        if not url:
            print("Картинка не найдена (ни ссылки, ни через Pexels) — без вложения")
            return None
        token = upload_media_and_get_token(url, "image")
        if not token:
            print("Не удалось получить токен изображения — без вложения")
            return None
        return {"type": "image", "payload": {"token": token}}

    if media_type == "видео":
        if not media_url:
            print("Для видео нужна прямая ссылка в 'Ссылка на медиа' — без вложения")
            return None
        token = upload_media_and_get_token(media_url, "video")
        if not token:
            print("Не удалось получить токен видео — без вложения")
            return None
        return {"type": "video", "payload": {"token": token}}

    print(f"Неизвестный тип медиа '{media_type}' — без вложения")
    return None


def build_link_button_attachment(site_url: str, button_text: str):
    site_url = (site_url or "").strip()
    if not site_url:
        return None
    text = (button_text or "").strip() or DEFAULT_BUTTON_TEXT
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


def parse_scheduled(date_str: str, time_str: str, tz):
    date_str = (date_str or "").strip()
    time_str = (time_str or "").strip()
    if not date_str:
        return None
    try:
        day, month, year = (int(p) for p in date_str.split("."))
    except ValueError:
        return None
    hours, minutes = 0, 0
    if ":" in time_str:
        try:
            hours, minutes = (int(p) for p in time_str.split(":")[:2])
        except ValueError:
            pass
    naive = datetime(year, month, day, hours, minutes)
    return tz.localize(naive)


def main() -> None:
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    print(f"Текущее время по Москве на момент запуска: {now.strftime('%d.%m.%Y %H:%M')}")

    resp = requests.get(SHEET_CSV_URL, timeout=30)
    resp.encoding = "utf-8"
    # ВАЖНО: используем io.StringIO(resp.text), а НЕ resp.text.splitlines() —
    # splitlines() ломает многострочные посты (разрезает абзацы по границам ячеек CSV).
    reader = csv.DictReader(io.StringIO(resp.text))

    posted = load_posted()
    print(f"Уже отмечено как опубликованные (из posted.json): {sorted(posted, key=int) if posted else 'пусто'}")
    changed = False
    checked = 0
    skipped_future = 0

    for row in reader:
        checked += 1
        num = (row.get("№") or "").strip()
        if not num or num in posted:
            continue

        if (row.get("Статус") or "").strip() != STATUS_READY:
            continue

        scheduled = parse_scheduled(row.get("Дата"), row.get("Время"), tz)
        if not scheduled or scheduled > now:
            skipped_future += 1
            continue

        text = (row.get("Текст поста") or "").strip()
        if not text:
            print(f"Пост №{num}: пусто в колонке 'Текст поста', пропускаю")
            continue

        attachments = []
        try:
            media = build_media_attachment(
                row.get("Тип медиа"), row.get("Ссылка на медиа"), row.get("Ключевое слово для фото")
            )
            if media:
                attachments.append(media)
        except Exception as e:
            print(f"Пост №{num}: ОШИБКА при подготовке медиа — {e}. Публикую без него.")

        button = build_link_button_attachment(row.get("Ссылка на сайт"), row.get("Текст кнопки"))
        if button:
            attachments.append(button)

        response = send_message(text, attachments or None)
        print(f"Пост №{num}: статус {response.status_code}, ответ: {response.text[:200]}")

        if response.status_code == 200:
            posted.add(num)
            changed = True
        else:
            print(f"Пост №{num}: НЕ опубликован, проверьте токен/chat_id/права бота/ссылки")

    print(f"Проверено строк: {checked}, из них ещё не наступило время: {skipped_future}")

    if changed:
        save_posted(posted)
    else:
        print("Подходящих постов на этот запуск не найдено")


if __name__ == "__main__":
    main()
