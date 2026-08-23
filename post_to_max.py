"""
Скрипт публикации постов из Google Таблицы в канал MAX.
Запускается по расписанию через GitHub Actions (см. .github/workflows/post.yml).

Ничего не требует, кроме:
- ссылки на CSV-экспорт таблицы (переменная SHEET_CSV_URL ниже),
- токена бота и chat_id канала — их нужно положить в GitHub Secrets
  (Settings -> Secrets and variables -> Actions), НЕ вписывать в этот файл.
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
# формат "Значения, разделённые запятыми (.csv)" -> Опубликовать -> скопировать ссылку.
SHEET_CSV_URL = "СЮДА_ВСТАВИТЬ_ССЫЛКУ_НА_CSV"

# Токен бота и ID канала берутся из переменных окружения (GitHub Secrets),
# в самом файле их быть не должно — иначе токен утечёт вместе с кодом.
BOT_TOKEN = os.environ["MAX_BOT_TOKEN"]
CHAT_ID = os.environ["MAX_CHAT_ID"]

STATE_FILE = "posted.json"  # тут храним номера уже опубликованных постов
TIMEZONE = "Europe/Moscow"


def load_posted() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_posted(posted: set) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(posted, key=int), f, ensure_ascii=False)


def send_message(text: str) -> requests.Response:
    url = f"https://platform-api2.max.ru/messages?chat_id={CHAT_ID}"
    headers = {"Authorization": f"Bearer {BOT_TOKEN}"}
    payload = {"text": text}
    return requests.post(url, headers=headers, json=payload, timeout=30)


def main() -> None:
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    today_str = now.strftime("%d.%m.%Y")
    current_hour = now.hour

    resp = requests.get(SHEET_CSV_URL, timeout=30)
    resp.encoding = "utf-8"
    reader = csv.DictReader(resp.text.splitlines())

    posted = load_posted()
    changed = False

    for row in reader:
        num = (row.get("№") or "").strip()
        if not num or num in posted:
            continue
        if (row.get("Статус") or "").strip() != "Готов":
            continue
        if (row.get("Дата") or "").strip() != today_str:
            continue

        time_field = (row.get("Время") or "").strip()
        try:
            post_hour = int(time_field.split(":")[0])
        except (ValueError, IndexError):
            continue
        if post_hour != current_hour:
            continue

        text = (row.get("Текст поста") or "").strip()
        if not text:
            print(f"Пост №{num}: пусто в колонке 'Текст поста', пропускаю")
            continue

        response = send_message(text)
        print(f"Пост №{num}: статус {response.status_code}, ответ: {response.text[:200]}")

        if response.status_code == 200:
            posted.add(num)
            changed = True
        else:
            print(f"Пост №{num}: НЕ опубликован, проверьте токен/chat_id/права бота")

    if changed:
        save_posted(posted)
    else:
        print("Подходящих постов на этот час не найдено")


if __name__ == "__main__":
    main()
