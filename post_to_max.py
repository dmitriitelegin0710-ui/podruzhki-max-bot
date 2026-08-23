"""
Скрипт публикации постов из Google Таблицы в канал MAX.
Запускается по расписанию через GitHub Actions (см. .github/workflows/post.yml).
Ничего не требует, кроме:
- ссылки на CSV-экспорт таблицы (переменная SHEET_CSV_URL ниже),
- токена бота и chat_id канала — их нужно положить в GitHub Secrets
  (Settings -> Secrets and variables -> Actions), НЕ вписывать в этот файл.

Добавлена поддержка картинок:
  Тип медиа — нет / картинка
  Ключевое слово для фото — на английском, по нему Pexels ищет картинку
                              (используется, только если "Ссылка на медиа" пустая)
  Ссылка на медиа — прямая ссылка на картинку (необязательна, если есть ключевое слово)

Видео пока не поддерживается — это осознанно, добавим позже при необходимости.
"""
import csv
import json
import os
from datetime import datetime
import requests
import pytz

# ---- НАСТРОЙКИ ----
# Ссылка на CSV-экспорт вашей Google Таблицы.
# Как получить: Файл -> Опубликовать в интернете -> выбрать лист "Постинг" ->
# в выпадающем списке ФОРМАТА (не там, где выбираете лист) явно указать
# "Значения, разделённые запятыми (.csv)" вместо "Веб-страница" -> Опубликовать
# -> скопировать ссылку. Она должна заканчиваться на pub?output=csv,
# а НЕ на pubhtml (pubhtml — это веб-страница, скрипт её прочитать не сможет).
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vR-oJrZwXiwymusIpr6cRZ5GPNDiNWaFDeOvsFMuqe0herSVMpkEZcN2vYzBOibznyj3sG7IcINKYBq/pub?output=csv"

# Токен бота и ID канала берутся из переменных окружения (GitHub Secrets),
# в самом файле их быть не должно — иначе токен утечёт вместе с кодом.
BOT_TOKEN = os.environ["MAX_BOT_TOKEN"]
CHAT_ID = os.environ["MAX_CHAT_ID"]
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")  # необязательный: нет ключа — просто не будет автоподбора

STATE_FILE = "posted.json"  # тут храним номера уже опубликованных постов
TIMEZONE = "Europe/Moscow"
STATUS_READY = "Черновик"  # ВАЖНО: должно точь-в-точь совпадать со значением в столбце "Статус" вашей таблицы


def load_posted() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_posted(posted: set) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(posted, key=int), f, ensure_ascii=False)


def fetch_pexels_image(keyword: str):
    """Ищет картинку на Pexels по ключевому слову, возвращает прямую ссылку или None."""
    keyword = (keyword or "").strip()
    if not keyword:
        return None
    if not PEXELS_API_KEY:
        print("PEXELS_API_KEY не задан — автоподбор картинки пропущен")
        return None
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": keyword, "per_page": 1, "orientation": "portrait"},
            headers={"Authorization": PEXELS_API_KEY},  # у Pexels ключ без слова Bearer
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


def build_attachments(media_type: str, media_url: str, photo_keyword: str):
    """Собирает вложение для поста. Пока поддерживаются только картинки."""
    media_type = (media_type or "").strip().lower()
    media_url = (media_url or "").strip()

    if not media_type or media_type == "нет":
        return None

    if media_type == "картинка":
        url = media_url or fetch_pexels_image(photo_keyword)
        if not url:
            print("Картинка не найдена (ни ссылки, ни через Pexels) — публикую без вложения")
            return None
        return [{"type": "image", "payload": {"url": url}}]

    print(f"Тип медиа '{media_type}' пока не поддерживается (видео отключено) — публикую без вложения")
    return None


def send_message(text: str, attachments=None) -> requests.Response:
    url = f"https://platform-api2.max.ru/messages?chat_id={CHAT_ID}"
    # ВАЖНО: MAX не использует слово "Bearer" перед токеном, в отличие от многих
    # других API. Заголовок должен быть ровно таким:
    headers = {"Authorization": BOT_TOKEN}
    payload = {"text": text}
    if attachments:
        payload["attachments"] = attachments
    return requests.post(url, headers=headers, json=payload, timeout=30)


def parse_scheduled(date_str: str, time_str: str, tz):
    """Собирает Дату+Время из таблицы в один объект datetime с часовым поясом."""
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

    resp = requests.get(SHEET_CSV_URL, timeout=30)
    resp.encoding = "utf-8"
    reader = csv.DictReader(resp.text.splitlines())

    posted = load_posted()
    changed = False

    for row in reader:
        num = (row.get("№") or "").strip()
        if not num or num in posted:
            continue

        if (row.get("Статус") or "").strip() != STATUS_READY:
            continue

        scheduled = parse_scheduled(row.get("Дата"), row.get("Время"), tz)
        # Публикуем, если время уже наступило (а не только точно "сейчас") —
        # так пропуск одного запуска (например, из-за задержки GitHub Actions)
        # не потеряет пост навсегда, его отправит следующий же запуск.
        if not scheduled or scheduled > now:
            continue

        text = (row.get("Текст поста") or "").strip()
        if not text:
            print(f"Пост №{num}: пусто в колонке 'Текст поста', пропускаю")
            continue

        try:
            attachments = build_attachments(
                row.get("Тип медиа"),
                row.get("Ссылка на медиа"),
                row.get("Ключевое слово для фото"),
            )
        except Exception as e:
            print(f"Пост №{num}: ОШИБКА при подготовке картинки — {e}. Отправляю без вложения.")
            attachments = None

        response = send_message(text, attachments)
        print(f"Пост №{num}: статус {response.status_code}, ответ: {response.text[:200]}")

        if response.status_code == 200:
            posted.add(num)
            changed = True
        else:
            print(f"Пост №{num}: НЕ опубликован, проверьте токен/chat_id/права бота/ссылку на медиа")

    if changed:
        save_posted(posted)
    else:
        print("Подходящих постов на этот запуск не найдено")


if __name__ == "__main__":
    main()
