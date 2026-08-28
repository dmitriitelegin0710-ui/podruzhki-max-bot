"""
Рерайт новостей шоу-бизнеса через YandexGPT и публикация в MAX.

Читает news/filtered_articles.json (результат gdelt_search.py + filter_articles.py),
пропускает уже опубликованные (news/posted_news.json), для новых —
переписывает текст через YandexGPT (своими словами, без копирования
чужого текста) и публикует в MAX через max_common.py.

За один запуск публикует не больше POSTS_PER_RUN новостей, чтобы не
завалить канал разом, если пайплайн находит сразу много подходящих статей.
Держать эту переменную маленькой безопаснее — но да, если фильтр находит
15 новых статей, при POSTS_PER_RUN=3 отработать их все займёт 5 запусков.

Требуемые GitHub Secrets:
  MAX_BOT_TOKEN, MAX_CHAT_ID, PEXELS_API_KEY   — как у rubric_post_to_max.py
  YANDEX_API_KEY, YANDEX_FOLDER_ID             — как у rubric_post_to_max.py
"""
import json
import os
import random
import re
from pathlib import Path
from urllib.parse import urlparse

import requests

from max_common import (
    fetch_pexels_image,
    upload_media_and_get_token,
    send_message,
)

FILTERED_FILE = Path("news/filtered_articles.json")
STATE_FILE = Path("news/posted_news.json")

YANDEX_API_KEY = os.environ["YANDEX_API_KEY"]
YANDEX_FOLDER_ID = os.environ["YANDEX_FOLDER_ID"]
YANDEXGPT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
YANDEXGPT_MODEL_URI_TEMPLATE = "gpt://{folder_id}/yandexgpt-lite/rc"

POSTS_PER_RUN = 3

EMOJI_POOL = ["🎬", "⭐", "📸", "🎤", "✨"]

# Общий пул — фото по конкретному инфоповоду искать не пытаемся (GDELT не даёт
# картинку конкретного человека), берём общие "гламурные" фото-заглушки.
PHOTO_KEYWORDS = [
    "гламур звезды",
    "красная дорожка",
    "селебрити стиль",
    "вечернее платье",
]

POST_INSTRUCTIONS = """
Ты — автор постов о шоу-бизнесе для женского паблика в мессенджере MAX. Пиши по-русски,
живо и по-доброму, без канцелярита.

КРИТИЧЕСКИ ВАЖНО:
- Перескажи факты из текста ниже СВОИМИ СЛОВАМИ. Не копируй фразы дословно из исходного текста.
- Не придумывай факты, цитаты или детали, которых нет в исходном тексте.
- Если в исходном тексте что-то непонятно или противоречиво — просто не включай эту деталь.

Не используй хэштеги. Не добавляй ссылки в текст — они не нужны.

РАЗМЕТКА (мессенджер MAX поддерживает её нативно):
  **жирный текст** — для смыслового заголовка и ключевой фразы
  _курсив_ — для лёгкого акцента, изредка
Абзацы разделяй пустой строкой.

Формат — пост на 50-90 слов: короткий цепляющий заголовок (**жирным**), затем 1-2 абзаца
по сути новости.

Заголовок статьи-источника: {title}
Текст статьи-источника:
{text}
"""


def load_filtered() -> list:
    if not FILTERED_FILE.exists():
        return []
    with FILTERED_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def load_state() -> set:
    if STATE_FILE.exists():
        with STATE_FILE.open(encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_state(state: set) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(sorted(state), f, ensure_ascii=False)


def clean_formatting(text: str) -> str:
    text = re.sub(r'#{1,6}\s*', '', text)
    text = re.sub(r'`{1,3}(.+?)`{1,3}', r'\1', text)
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    text = re.sub(r'(?<!\*)\*(?!\*)\s*', '', text)
    return text.strip()


def rewrite_article(title: str, text: str) -> str:
    # Обрезаем исходный текст — модели не нужна вся статья целиком,
    # и это снижает риск, что она случайно скопирует длинный кусок дословно.
    trimmed_text = text[:2500]

    prompt = POST_INSTRUCTIONS.format(title=title, text=trimmed_text)
    body = {
        "modelUri": YANDEXGPT_MODEL_URI_TEMPLATE.format(folder_id=YANDEX_FOLDER_ID),
        "completionOptions": {"stream": False, "temperature": 0.5, "maxTokens": 500},
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


def source_name(url: str) -> str:
    domain = urlparse(url).netloc
    return domain.replace("www.", "")


def main():
    articles = load_filtered()
    state = load_state()

    new_articles = [a for a in articles if a.get("url") and a["url"] not in state]
    print(f"Всего отфильтрованных статей: {len(articles)}")
    print(f"Ещё не опубликовано: {len(new_articles)}")

    to_post = new_articles[:POSTS_PER_RUN]
    if not to_post:
        print("Публиковать нечего на этот запуск")
        return

    changed = False

    for article in to_post:
        title = article.get("title", "")
        url = article["url"]
        print(f"Обрабатываю: {title} ({url})")

        try:
            rewritten = rewrite_article(title, article.get("text", ""))
        except Exception as e:
            print(f"Ошибка рерайта — {e}. Пропускаю эту статью на этот раз (не отмечаю как опубликованную).")
            continue

        emoji = random.choice(EMOJI_POOL)
        post_text = f"{emoji} {rewritten}\n\n_Источник: {source_name(url)}_"

        attachments = []
        try:
            image_url = fetch_pexels_image(PHOTO_KEYWORDS)
            if image_url:
                token = upload_media_and_get_token(image_url)
                if token:
                    attachments.append({"type": "image", "payload": {"token": token}})
        except Exception as e:
            print(f"Ошибка при подготовке фото — {e}. Публикую без него.")

        response = send_message(post_text, attachments or None)
        print(f"Статус публикации: {response.status_code}, ответ: {response.text[:200]}")

        if response.status_code == 200:
            state.add(url)
            changed = True
        else:
            print("НЕ опубликовано, проверьте токены/права бота")

    if changed:
        save_state(state)


if __name__ == "__main__":
    main()
