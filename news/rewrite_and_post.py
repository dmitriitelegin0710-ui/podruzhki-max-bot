"""
Рерайт новостей шоу-бизнеса через YandexGPT и публикация в MAX (+ Telegram).

Читает news/filtered_articles.json (результат gdelt_search.py + filter_articles.py),
пропускает уже опубликованные (news/posted_news.json), для новых —
переписывает текст через YandexGPT (своими словами, без копирования
чужого текста) и публикует в MAX через max_common.py, а если настроен
Telegram (см. telegram_common.py в корне репозитория) — параллельно
публикует тот же пост туда.

Фото: сначала пробуем реальное фото статьи-источника (image_url, извлечённое
в gdelt_search.py из og:image) — оно соответствует новости, но это чужая
редакционная фотография без явных прав на переиспользование (сознательно
принятый риск). Если фото у статьи нет — раньше подставлялось случайное
стоковое фото с Pexels по одному из 4 общих "гламурных" слов (гламур
звезды/красная дорожка/...) — независимо от того, о чём вообще новость.
ИЗМЕНЕНО: теперь в этом случае сначала отдельным лёгким запросом к
YandexGPT (generate_photo_keywords) по уже переписанному тексту поста
подбираются 2-3 английских ключевых слова, которые точно описывают СМЫСЛ
именно этой новости — и только если по ним ничего не нашлось на Pexels,
используются старые общие "гламурные" слова как запасной вариант.

За один запуск публикует не больше POSTS_PER_RUN новостей — сейчас 1,
расписание в news_post.yml вызывает скрипт несколько раз в день, чтобы
новости не приходили пачкой, а были распределены по дню.

--- Устойчивость к "зависанию" на одной статье ---
Скрипт пробует НЕСКОЛЬКО кандидатов за один запуск (см.
MAX_ATTEMPTS_PER_RUN) и останавливается, как только наберёт нужное
количество успешных публикаций (POSTS_PER_RUN) — одна проблемная статья
не блокирует все остальные.

--- Защита от публикации отказов YandexGPT ---
Иногда YandexGPT вместо рерайта возвращает отказ вида "Я не могу это
обсуждать" (сработал встроенный контент-фильтр Яндекса на чувствительную
тему). rewrite_article проверяет:
  1) поле "status" у альтернативы в ответе API — если оно
     ALTERNATIVE_STATUS_CONTENT_FILTER, это точный признак срабатывания
     цензуры на стороне Яндекса;
  2) сам текст ответа — на характерные фразы-отказы и на подозрительно
     короткую длину (на случай, если поле status не пришло).
Если сработало любое из двух — поднимается GptRefusalError, статья
пропускается на этот раз (не публикуется и НЕ отмечается как
опубликованная), а скрипт переходит к следующему кандидату из очереди.

--- Публикация в Telegram ---
Если заданы переменные окружения TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID,
после успешной публикации в MAX тот же пост (тот же текст, то же фото)
уходит и в Telegram-канал через telegram_common.py (лежит в корне
репозитория — см. импорт с добавлением корня в sys.path ниже). Если
переменные не заданы — публикация в Telegram просто пропускается.
MAX остаётся источником истины для "опубликовано/не опубликовано": если
Telegram по какой-то причине не сработал, это только логируется, но
статья всё равно считается опубликованной (т.к. в MAX она уже вышла).

Требуемые GitHub Secrets:
  MAX_BOT_TOKEN, MAX_CHAT_ID, PEXELS_API_KEY   — как у rubric_post_to_max.py
  YANDEX_API_KEY, YANDEX_FOLDER_ID             — как у rubric_post_to_max.py
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID         — опционально
"""
import json
import os
import random
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

from max_common import (
    fetch_pexels_image,
    upload_media_and_get_token,
    send_message,
)

# НОВОЕ: telegram_common.py лежит в корне репозитория, а не в news/, чтобы
# его можно было использовать и из rubric_post_to_max.py (там он тоже
# лежит рядом, в корне). При запуске "python news/rewrite_and_post.py"
# Python по умолчанию ищет импорты только в папке news/ (директории самого
# скрипта), поэтому корень репозитория нужно добавить в sys.path явно.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import telegram_common

FILTERED_FILE = Path("news/filtered_articles.json")
STATE_FILE = Path("news/posted_news.json")

YANDEX_API_KEY = os.environ["YANDEX_API_KEY"]
YANDEX_FOLDER_ID = os.environ["YANDEX_FOLDER_ID"]
YANDEXGPT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
YANDEXGPT_MODEL_URI_TEMPLATE = "gpt://{folder_id}/yandexgpt-lite/rc"

POSTS_PER_RUN = 1
MAX_ATTEMPTS_PER_RUN = 5

EMOJI_POOL = ["🎬", "⭐", "📸", "🎤", "✨", "💫"]

PHOTO_KEYWORDS = [
    "гламур звезды",
    "красная дорожка",
    "селебрити стиль",
    "вечернее платье",
]

GPT_REFUSAL_STATUSES = {
    "ALTERNATIVE_STATUS_CONTENT_FILTER",
}

GPT_REFUSAL_TEXT_PATTERNS = [
    "я не могу это обсуждать",
    "я не могу обсуждать эту тему",
    "не могу предоставить информацию",
    "не могу помочь с этим запросом",
    "не могу выполнить этот запрос",
    "не могу сгенерировать",
    "не могу написать текст",
    "давайте поговорим о чём-то другом",
    "давайте поговорим о чем-то другом",
    "как языковая модель",
    "я являюсь языковой моделью",
    "у меня есть ограничения",
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


class GptRefusalError(Exception):
    """Поднимается, когда YandexGPT отказался переписывать текст
    (сработал встроенный контент-фильтр), а не вернул реальный рерайт."""
    pass


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


def looks_like_gpt_refusal(text: str) -> bool:
    normalized = text.lower()
    if len(normalized) < 30:
        return True
    return any(pattern in normalized for pattern in GPT_REFUSAL_TEXT_PATTERNS)


def rewrite_article(title: str, text: str) -> str:
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

    alternative = data["result"]["alternatives"][0]
    status = alternative.get("status", "")

    if status in GPT_REFUSAL_STATUSES:
        raise GptRefusalError(
            f"YandexGPT вернул статус '{status}' — сработал контент-фильтр"
        )

    raw_text = alternative["message"]["text"].strip()
    cleaned = clean_formatting(raw_text)

    if looks_like_gpt_refusal(cleaned):
        raise GptRefusalError(
            "Ответ YandexGPT похож на отказ по содержанию текста"
        )

    return cleaned


def source_name(url: str) -> str:
    domain = urlparse(url).netloc.replace("www.", "")
    return domain.split(".")[0].capitalize()


def generate_photo_keywords(post_text: str) -> list:
    if not post_text:
        return []

    plain_text = re.sub(r'[*_+#]', '', post_text).strip()[:700]
    if not plain_text:
        return []

    prompt = (
        "Ниже текст новостного поста о шоу-бизнесе для женского паблика в "
        "мессенджере. Придумай 2-3 ключевых слова НА АНГЛИЙСКОМ ЯЗЫКЕ для "
        "поиска стоковой фотографии на Pexels, которая максимально точно "
        "иллюстрировала бы главную тему и настроение именно этой новости "
        "(например, если пост про свадьбу — 'wedding celebration', если про "
        "выход в свет на премьере — 'red carpet premiere', и т.п., а не "
        "просто общие слова про шоу-бизнес). Ставь слова по порядку от "
        "самого точного к более общему.\n"
        "Ответь СТРОГО в формате: keyword phrase one, keyword phrase two, "
        "keyword phrase three — без кавычек, без нумерации, без пояснений, "
        "только сами фразы через запятую.\n\n"
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
        alternative = resp.json()["result"]["alternatives"][0]
        if alternative.get("status", "") in GPT_REFUSAL_STATUSES:
            print("Ключевые слова для фото: YandexGPT отказал, использую запасной вариант")
            return []
        raw = alternative["message"]["text"].strip()
        if looks_like_gpt_refusal(raw):
            print("Ключевые слова для фото: похоже на отказ, использую запасной вариант")
            return []
        keywords = [kw.strip(" .\"'") for kw in raw.split(",") if kw.strip(" .\"'")]
        if keywords:
            print(f"Ключевые слова для фото по тексту новости: {keywords}")
            return keywords
    except Exception as e:
        print(f"Не удалось сгенерировать ключевые слова для фото по тексту новости — {e}")
    return []


def get_post_image(article: dict, post_text: str = None):
    image_url = article.get("image_url")
    if image_url:
        return image_url
    dynamic_keywords = generate_photo_keywords(post_text) if post_text else []
    return fetch_pexels_image(dynamic_keywords + PHOTO_KEYWORDS)


def try_post_article(article: dict) -> bool:
    title = article.get("title", "")
    url = article["url"]
    print(f"Обрабатываю: {title} ({url})")

    try:
        rewritten = rewrite_article(title, article.get("text", ""))
    except GptRefusalError as e:
        print(f"YandexGPT отказался переписывать эту статью ({e}). Пропускаю, НЕ публикую отказ.")
        return False
    except Exception as e:
        print(f"Ошибка рерайта — {e}. Пропускаю эту статью на этот раз (не отмечаю как опубликованную).")
        return False

    emoji = random.choice(EMOJI_POOL)
    post_text = f"{emoji} {rewritten}\n\n_Источник: {source_name(url)}_"

    image_url = None
    attachments = []
    try:
        image_url = get_post_image(article, post_text=rewritten)
        if image_url:
            token = upload_media_and_get_token(image_url)
            if token:
                attachments.append({"type": "image", "payload": {"token": token}})
    except Exception as e:
        print(f"Ошибка при подготовке фото — {e}. Публикую без него.")

    response = send_message(post_text, attachments or None)
    print(f"MAX: статус публикации {response.status_code}, ответ: {response.text[:200]}")

    if response.status_code != 200:
        print("MAX: НЕ опубликовано, проверьте токены/права бота")
        return False

    if telegram_common.is_configured():
        try:
            tg_response = telegram_common.send_message(post_text, photo_url=image_url)
            if tg_response.status_code == 200:
                print("Telegram: опубликовано")
            else:
                print(
                    f"Telegram: НЕ опубликовано — статус {tg_response.status_code}, "
                    f"ответ: {tg_response.text[:200]}"
                )
        except Exception as e:
            print(f"Telegram: ошибка публикации — {e}")

    return True


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

    if posted_count == 0:
        print(
            f"Ни одна из {len(candidates)} проверенных статей не опубликовалась в этот раз. "
            "Проверьте логи выше на ошибки рерайта/публикации."
        )

    if changed:
        save_state(state)


if __name__ == "__main__":
    main()
