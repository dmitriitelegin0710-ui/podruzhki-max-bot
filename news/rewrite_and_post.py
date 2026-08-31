"""
Рерайт новостей шоу-бизнеса через YandexGPT и публикация в MAX.

Читает news/filtered_articles.json (результат gdelt_search.py + filter_articles.py),
пропускает уже опубликованные (news/posted_news.json), для новых —
переписывает текст через YandexGPT (своими словами, без копирования
чужого текста) и публикует в MAX через max_common.py.

Фото: сначала пробуем реальное фото статьи-источника (image_url, извлечённое
в gdelt_search.py из og:image) — оно соответствует новости, но это чужая
редакционная фотография без явных прав на переиспользование (сознательно
принятый риск). Если фото у статьи нет — берём тематическое стоковое фото
с Pexels как запасной вариант.

За один запуск публикует не больше POSTS_PER_RUN новостей — сейчас 1,
расписание в news_post.yml вызывает скрипт несколько раз в день, чтобы
новости не приходили пачкой, а были распределены по дню.

--- ИЗМЕНЕНО: устойчивость к "зависанию" на одной статье ---
Раньше скрипт брал СТРОГО первую статью из очереди (new_articles[:1]) и,
если у неё падал рерайт (например, YandexGPT отказывался переписывать
текст из-за встроенного контент-фильтра на чувствительную тему), просто
завершал запуск ничего не опубликовав — при этом статья не помечалась как
опубликованная и на следующий запуск снова оказывалась первой в очереди.
Если такая статья случайно возникала в начале очереди (из-за огрехов в
фильтре на этапе filter_articles.py), новости переставали публиковаться
вообще, при этом никакой ошибки в логах workflow не было — скрипт просто
тихо завершался с "Публиковать нечего".

Теперь скрипт пробует НЕСКОЛЬКО кандидатов за один запуск (см.
MAX_ATTEMPTS_PER_RUN) и останавливается, как только наберёт нужное
количество успешных публикаций (POSTS_PER_RUN) — одна проблемная статья
больше не блокирует все остальные.

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

POSTS_PER_RUN = 1
# НОВОЕ: сколько кандидатов из очереди готовы попробовать за один запуск,
# прежде чем сдаться. Раньше пробовалась только 1 (=POSTS_PER_RUN) статья.
MAX_ATTEMPTS_PER_RUN = 5

EMOJI_POOL = ["🎬", "⭐", "📸", "🎤", "✨", "💫"]

# Запасной пул для случаев, когда у статьи нет собственного фото.
PHOTO_KEYWORDS = [
    "гламур звезды",
    "красная дорожка",
    "селебрити стиль",
    "вечернее платье",
]

POST_INSTRUCTIONS = """
Ты — автор постов о шоу-бизнесе для женского паблика в мессенджере MAX. Пиши по-русски,
живо, тепло и визуально ярко — как реальные популярные женские паблики, а не сухим текстом.

КРИТИЧЕСКИ ВАЖНО:
- Перескажи факты из текста ниже СВОИМИ СЛОВАМИ. Не копируй фразы дословно из исходного текста.
- Не придумывай факты, цитаты или детали, которых нет в исходном тексте.
- Если в исходном тексте что-то непонятно или противоречиво — просто не включай эту деталь.

Не используй хэштеги. Не добавляй ссылки в текст — они не нужны.

РАЗМЕТКА (мессенджер MAX поддерживает её нативно):
  **жирный текст** — для заголовка поста и 1-2 ключевых фраз внутри
  _курсив_ — для лёгкого акцента, изредка
Используй 2-4 уместных по смыслу эмодзи по тексту (не только в начале).
Абзацы разделяй пустой строкой.

Формат — пост на 50-90 слов: короткий цепляющий заголовок (**жирным**, с эмодзи), затем
1-2 абзаца по сути новости.

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
    # Без точки и домена верхнего уровня — иначе MAX сам превращает
    # текст вида "site.ru" в кликабельную ссылку.
    domain = urlparse(url).netloc.replace("www.", "")
    return domain.split(".")[0].capitalize()


def get_post_image(article: dict):
    """Сначала пробуем реальное фото статьи, иначе — стоковое с Pexels."""
    image_url = article.get("image_url")
    if image_url:
        return image_url
    return fetch_pexels_image(PHOTO_KEYWORDS)


def try_post_article(article: dict) -> bool:
    """Пытается переписать и опубликовать одну статью.
    Возвращает True при успешной публикации, False при любой неудаче
    (ошибка рерайта или неуспешный ответ MAX API) — вызывающий код в этом
    случае переходит к следующему кандидату, а не сдаётся полностью."""
    title = article.get("title", "")
    url = article["url"]
    print(f"Обрабатываю: {title} ({url})")

    try:
        rewritten = rewrite_article(title, article.get("text", ""))
    except Exception as e:
        print(f"Ошибка рерайта — {e}. Пропускаю эту статью на этот раз (не отмечаю как опубликованную).")
        return False

    emoji = random.choice(EMOJI_POOL)
    post_text = f"{emoji} {rewritten}\n\n_Источник: {source_name(url)}_"

    attachments = []
    try:
        image_url = get_post_image(article)
        if image_url:
            token = upload_media_and_get_token(image_url)
            if token:
                attachments.append({"type": "image", "payload": {"token": token}})
    except Exception as e:
        print(f"Ошибка при подготовке фото — {e}. Публикую без него.")

    response = send_message(post_text, attachments or None)
    print(f"Статус публикации: {response.status_code}, ответ: {response.text[:200]}")

    if response.status_code == 200:
        return True

    print("НЕ опубликовано, проверьте токены/права бота")
    return False


def main():
    articles = load_filtered()
    state = load_state()

    new_articles = [a for a in articles if a.get("url") and a["url"] not in state]
    print(f"Всего отфильтрованных статей: {len(articles)}")
    print(f"Ещё не опубликовано: {len(new_articles)}")

    if not new_articles:
        print("Публиковать нечего на этот запуск")
        return

    candidates = new_articles[:MAX_ATTEMPTS_PER_RUN]
    changed = False
    posted_count = 0

    for article in candidates:
        if posted_count >= POSTS_PER_RUN:
            break

        success = try_post_article(article)
        if success:
            state.add(article["url"])
            changed = True
            posted_count += 1
        # при неуспехе просто переходим к следующему кандидату из candidates —
        # проблемная статья не блокирует остальные в этом же запуске

    if posted_count == 0:
        print(
            f"Ни одна из {len(candidates)} проверенных статей не опубликовалась в этот раз. "
            "Проверьте логи выше на ошибки рерайта/публикации."
        )

    if changed:
        save_state(state)


if __name__ == "__main__":
    main()
