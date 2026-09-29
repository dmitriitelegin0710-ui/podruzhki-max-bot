"""
Рерайт новостей шоу-бизнеса через YandexGPT и публикация в MAX + Telegram.

Читает news/filtered_articles.json (результат rss_search.py), пропускает
уже опубликованные (news/posted_news.json), для новых — переписывает
текст через YandexGPT (своими словами, без копирования чужого текста) и
публикует в MAX через max_common.py, а если настроен Telegram (см.
telegram_common.py в корне репозитория) — публикует туда тот же пост с
добавленными хэштегами (см. ниже) — без повторного обращения к YandexGPT.

--- Режим предпросмотра (DRY_RUN) ---
Если переменная окружения DRY_RUN установлена в "true"/"1"/"yes" — скрипт
делает всё как обычно (рерайт текста через YandexGPT, подбор фото), но
ОСТАНАВЛИВАЕТСЯ прямо перед загрузкой фото в MAX и отправкой сообщения:
готовый текст поста и адрес картинки печатаются в лог, ни в MAX, ни в
Telegram ничего не уходит, и статья НЕ помечается как опубликованная в
posted_news.json — так что при следующем обычном запуске (без DRY_RUN)
эта же статья снова будет доступна для настоящей публикации.
Удобно, чтобы посмотреть, что именно уйдёт в канал, не тратя реальный
пост на проверку.

Фото: сначала пробуем реальное фото статьи-источника (image_url, взятое
из RSS-ленты) — оно соответствует новости, но это чужая редакционная
фотография без явных прав на переиспользование (сознательно принятый
риск). Если фото у статьи нет — сначала отдельным лёгким запросом к
YandexGPT (generate_photo_keywords) по уже переписанному тексту поста
подбираются 2-3 английских ключевых слова, которые точно описывают СМЫСЛ
именно этой новости — и только если по ним ничего не нашлось на Pexels,
используются старые общие "гламурные" слова как запасной вариант.

--- Заголовок рубрики и ссылка на статью-источник ---
Перед текстом поста добавляется общий заголовок рубрики "В курсе событий"
с одним из эмодзи EMOJI_POOL (см. build_post_text), чтобы читательницы
узнавали рубрику с первого взгляда — так же, как у других рубрик канала.
Под постом, кроме текстовой подписи "_Источник: ..._", добавляется
КЛИКАБЕЛЬНАЯ кнопка "Читать полностью" (см. max_common.build_link_button_attachment),
ведущая на URL КОНКРЕТНОЙ статьи-источника (article["url"]), а не на
главную страницу сайта — потому что источники разных новостей могут быть
разными сайтами (см. rss_search.py), и ссылка на "непричастный" сайт
была бы некорректной. В Telegram кнопка не прокидывается (см.
telegram_common.send_message) — туда уходит только текст с фото.

За один запуск публикует не больше POSTS_PER_RUN новостей — сейчас 1,
расписание в news_post.yml вызывает скрипт несколько раз в день, чтобы
новости не приходили пачкой, а были распределены по дню.

--- Устойчивость к "зависанию" на одной статье ---
Скрипт пробует НЕСКОЛЬКО кандидатов за один запуск (MAX_ATTEMPTS_PER_RUN)
и останавливается, как только наберёт нужное количество успешных
публикаций (POSTS_PER_RUN) — одна проблемная статья не блокирует
остальные.

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
опубликованной), а скрипт переходит к следующему кандидату из очереди.

--- Публикация в Telegram БЕЗ повторного вызова YandexGPT ---
Если заданы TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID, после успешной
публикации в MAX тот же пост уходит и в Telegram — но не 1-в-1 идентичным:
telegram_common.generate_hashtags() подбирает 2-3 хэштега ПО ГОТОВОМУ
ТЕКСТУ поста через обычный поиск ключевых слов (без ИИ), и они
добавляются в конец поста для Telegram. Основной текст, тон и структура
не меняются — это дешёвая эвристика, а не полноценная адаптация под
площадку, зато не расходует токены YandexGPT второй раз. MAX остаётся
источником истины для "опубликовано/не опубликовано": если Telegram по
какой-то причине не сработал, это только логируется.

Требуемые GitHub Secrets:
  MAX_BOT_TOKEN, MAX_CHAT_ID, PEXELS_API_KEY
  YANDEX_API_KEY, YANDEX_FOLDER_ID
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — опционально
"""
import json
import os
import random
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from max_common import (
    fetch_pexels_image,
    upload_media_and_get_token,
    build_link_button_attachment,
    send_message,
)

# telegram_common.py лежит в корне репозитория, а не в news/, чтобы им
# могли пользоваться и news/rewrite_and_post.py, и rubric_post_to_max.py.
# При запуске "python news/rewrite_and_post.py" Python по умолчанию ищет
# импорты только в папке news/ (директории самого скрипта), поэтому корень
# репозитория нужно добавить в sys.path явно.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import telegram_common

FILTERED_FILE = Path("news/filtered_articles.json")
STATE_FILE = Path("news/posted_news.json")

YANDEX_API_KEY = os.environ["YANDEX_API_KEY"]
YANDEX_FOLDER_ID = os.environ["YANDEX_FOLDER_ID"]
YANDEXGPT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
YANDEXGPT_MODEL_URI_TEMPLATE = "gpt://{folder_id}/yandexgpt-lite/rc"

POSTS_PER_RUN = 1
# Сколько кандидатов из очереди готовы попробовать за один запуск.
MAX_ATTEMPTS_PER_RUN = 8

# --- Слоты публикации (по московскому времени) ---
# Workflow запускается несколько раз в окрестности каждого слота (см.
# news_post.yml), а сам скрипт решает, пора ли публиковать: слот "созрел",
# если его время уже наступило, но прошло не больше SLOT_MAX_LATE_MINUTES
# и новость на этот слот ещё не выходила. Так один пропущенный или
# задержанный GitHub'ом запуск по расписанию не приводит к потере новости —
# следующий запуск догонит (та же идея, что и в rubric_post_to_max.py).
# Москва не переходит на летнее время, поэтому фиксированный UTC+3.
MSK = timezone(timedelta(hours=3))
NEWS_SLOTS = ["09:05", "13:15", "17:25"]
SLOT_MAX_LATE_MINUTES = 120   # последний слот 17:25 → не позже 19:25, до вечернего ритуала 20:20
SLOTS_STATE_FILE = Path("news/posted_news_slots.json")
# Слоты работают ВСЕГДА — и при запуске по расписанию GitHub, и при запуске
# через API из cron-job.org (для GitHub это тоже workflow_dispatch, поэтому
# отличить его от нажатия кнопки нельзя). Чтобы опубликовать новость прямо
# сейчас, минуя слоты, при ручном запуске нужно включить галочку force_now.
FORCE_NOW = os.environ.get("FORCE_NOW", "").strip().lower() in ("1", "true", "yes")

# Итоги попытки опубликовать одну статью.
RESULT_POSTED = "posted"     # опубликовано
RESULT_REFUSED = "refused"   # YandexGPT отказался переписывать — повторять бессмысленно
RESULT_FAILED = "failed"     # временная ошибка (сеть, MAX и т.п.) — можно попробовать позже

# Режим предпросмотра — см. описание в шапке файла. Включается переменной
# окружения DRY_RUN=true в workflow (workflow_dispatch input dry_run).
DRY_RUN = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")

# Заголовок рубрики — печатается жирным перед текстом каждого новостного
# поста (см. build_post_text), чтобы читательницы узнавали рубрику сразу,
# так же как у остальных рубрик канала (сравните "Кино на вечер" и т.п.).
RUBRIC_TITLE = "В курсе событий"

EMOJI_POOL = ["🎬", "⭐", "📸", "🎤", "✨", "💫"]

# Текст кнопки-ссылки на статью-источник (см. build_link_button_attachment
# в max_common.py). Ведёт на URL КОНКРЕТНОЙ статьи, а не на сайт целиком —
# источники разных новостей могут быть разными сайтами.
SOURCE_BUTTON_TEXT = "Читать полностью"

# Запасной пул для случаев, когда у статьи нет собственного фото.
PHOTO_KEYWORDS = [
    "гламур звезды",
    "красная дорожка",
    "селебрити стиль",
    "вечернее платье",
]

# Статусы ответа YandexGPT, означающие срабатывание встроенного
# контент-фильтра Яндекса (отказ переписывать текст).
GPT_REFUSAL_STATUSES = {
    "ALTERNATIVE_STATUS_CONTENT_FILTER",
}

# Текстовые признаки отказа — страховка на случай, если поле "status" не
# пришло или отказ выражен иначе, чем через content filter.
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
Комментарии под постами ОТКЛЮЧЕНЫ. НЕ задавай вопросов читательницам в конце поста и не
проси делиться мнением в комментариях.

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


def load_slots_state() -> set:
    if SLOTS_STATE_FILE.exists():
        with SLOTS_STATE_FILE.open(encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_slots_state(slots: set) -> None:
    SLOTS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Храним только записи за последние дни, чтобы файл не рос бесконечно.
    recent = sorted(slots)[-30:]
    with SLOTS_STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(recent, f, ensure_ascii=False)


def get_due_slot(now: datetime, done_slots: set):
    """Возвращает ключ слота 'ГГГГ-ММ-ДД_ЧЧ:ММ', который сейчас пора
    отработать, либо None, если публиковать пока (или уже) не нужно."""
    for slot in NEWS_SLOTS:
        hour, minute = (int(p) for p in slot.split(":"))
        slot_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        key = f"{now.strftime('%Y-%m-%d')}_{slot}"
        if key in done_slots:
            continue
        if now < slot_dt:
            continue
        if now - slot_dt > timedelta(minutes=SLOT_MAX_LATE_MINUTES):
            continue
        return key
    return None


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
    """Эвристическая проверка текста ответа на признаки отказа —
    страховка на случай, если поле status в ответе API не помогло."""
    normalized = text.lower()
    if len(normalized) < 30:
        return True
    return any(pattern in normalized for pattern in GPT_REFUSAL_TEXT_PATTERNS)


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

    alternative = data["result"]["alternatives"][0]
    status = alternative.get("status", "")

    # Точный признак срабатывания контент-фильтра Яндекса — проверяем ДО
    # того, как вообще смотрим на текст ответа.
    if status in GPT_REFUSAL_STATUSES:
        raise GptRefusalError(
            f"YandexGPT вернул статус '{status}' — сработал контент-фильтр"
        )

    raw_text = alternative["message"]["text"].strip()
    cleaned = clean_formatting(raw_text)

    # Страховка по тексту, если статус не пришёл или пуст.
    if looks_like_gpt_refusal(cleaned):
        raise GptRefusalError(
            "Ответ YandexGPT похож на отказ по содержанию текста"
        )

    return cleaned


def source_name(url: str) -> str:
    # Без точки и домена верхнего уровня — иначе MAX сам превращает
    # текст вида "site.ru" в кликабельную ссылку.
    domain = urlparse(url).netloc.replace("www.", "")
    return domain.split(".")[0].capitalize()


def build_post_text(rewritten: str, url: str) -> str:
    """Собирает финальный текст поста: заголовок рубрики (с одним из
    EMOJI_POOL — разнообразие сохраняется, как и раньше) + переписанный
    текст + текстовая подпись источника. Кликабельная ссылка на статью
    добавляется ОТДЕЛЬНО как кнопка (см. try_post_article), а не в текст."""
    emoji = random.choice(EMOJI_POOL)
    return (
        f"{emoji} **{RUBRIC_TITLE}**\n\n"
        f"{rewritten}\n\n"
        f"_Источник: {source_name(url)}_"
    )


def generate_photo_keywords(post_text: str) -> list:
    """По уже переписанному тексту поста просит YandexGPT сформулировать
    2-3 ключевых слова на английском для поиска ФОТО НА PEXELS, точно
    отражающих смысл именно этой новости — используется только как
    запасной вариант, когда у статьи-источника нет собственного image_url.
    Возвращает список слов от самого точного к самому общему;
    fetch_pexels_image пробует их по очереди. При любой ошибке, отказе
    модели или пустом результате возвращает [] — тогда используются
    старые общие PHOTO_KEYWORDS."""
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
    """Сначала пробуем реальное фото статьи. Если его нет — сначала
    пробуем ключевые слова, сгенерированные по смыслу переписанного текста
    поста (post_text), а если это не сработало — старые общие PHOTO_KEYWORDS
    как запасной вариант."""
    image_url = article.get("image_url")
    if image_url:
        return image_url
    dynamic_keywords = generate_photo_keywords(post_text) if post_text else []
    return fetch_pexels_image(dynamic_keywords + PHOTO_KEYWORDS)


def try_post_article(article: dict) -> bool:
    """Пытается переписать и опубликовать одну статью в MAX, а если
    настроен Telegram — и туда же (с добавленными хэштегами, без
    повторного вызова YandexGPT).
    В режиме DRY_RUN текст и фото подбираются по-настоящему, но реальная
    отправка в MAX/Telegram пропускается — вместо неё готовый пост
    печатается в лог.
    Возвращает RESULT_POSTED при успешной публикации в MAX (или при
    успешном предпросмотре в режиме DRY_RUN); неуспех в Telegram только
    логируется и не меняет этот результат. RESULT_REFUSED — YandexGPT
    отказался переписывать текст (см. GptRefusalError): такую статью
    больше не пробуем. RESULT_FAILED — любая другая неудача до этапа
    публикации в MAX (можно попробовать позже)."""
    title = article.get("title", "")
    url = article["url"]
    print(f"Обрабатываю: {title} ({url})")

    try:
        rewritten = rewrite_article(title, article.get("text", ""))
    except GptRefusalError as e:
        print(f"YandexGPT отказался переписывать эту статью ({e}). Пропускаю, НЕ публикую отказ.")
        return RESULT_REFUSED
    except Exception as e:
        print(f"Ошибка рерайта — {e}. Пропускаю эту статью на этот раз (не отмечаю как опубликованную).")
        return RESULT_FAILED

    max_post_text = build_post_text(rewritten, url)

    image_url = None
    try:
        image_url = get_post_image(article, post_text=rewritten)
    except Exception as e:
        print(f"Ошибка при подборе фото — {e}. Пост будет без фото.")

    if DRY_RUN:
        print("\n===== DRY RUN — пост НЕ публикуется, это только предпросмотр =====")
        print(max_post_text)
        print(f"\nКнопка-ссылка на статью: {url}")
        print(f"Фото (в реальном запуске будет загружено в MAX): {image_url or '— нет фото'}")
        print("===== конец предпросмотра =====\n")
        return RESULT_POSTED

    attachments = []
    if image_url:
        try:
            token = upload_media_and_get_token(image_url)
            if token:
                attachments.append({"type": "image", "payload": {"token": token}})
        except Exception as e:
            print(f"Ошибка при загрузке фото в MAX — {e}. Публикую без него.")

    # Кликабельная кнопка ведёт на URL именно ЭТОЙ статьи (а не на сайт
    # целиком) — источники разных новостей могут быть разными сайтами.
    button = build_link_button_attachment(url, SOURCE_BUTTON_TEXT)
    if button:
        attachments.append(button)

    response = send_message(max_post_text, attachments or None)
    print(f"MAX: статус публикации {response.status_code}, ответ: {response.text[:200]}")

    if response.status_code != 200:
        print("MAX: НЕ опубликовано, проверьте токены/права бота")
        return RESULT_FAILED

    # Дублируем пост в Telegram, если настроены секреты. Хэштеги подбираются
    # по готовому тексту через обычный поиск ключевых слов — БЕЗ повторного
    # обращения к YandexGPT. Кнопка-ссылка на статью в Telegram не
    # прокидывается (telegram_common.send_message её не поддерживает) —
    # туда уходит только текст с фото, как и раньше.
    if telegram_common.is_configured():
        try:
            hashtags = telegram_common.generate_hashtags(
                rewritten,
                telegram_common.NEWS_HASHTAG_KEYWORDS,
                telegram_common.DEFAULT_NEWS_HASHTAGS,
            )
            telegram_post_text = telegram_common.adapt_text_for_telegram_local(max_post_text, hashtags)
            tg_response = telegram_common.send_message(telegram_post_text, photo_url=image_url)
            if tg_response.status_code == 200:
                print(f"Telegram: опубликовано (хэштеги: {hashtags})")
            else:
                print(
                    f"Telegram: НЕ опубликовано — статус {tg_response.status_code}, "
                    f"ответ: {tg_response.text[:200]}"
                )
        except Exception as e:
            print(f"Telegram: ошибка публикации — {e}")

    return RESULT_POSTED


def main():
    if DRY_RUN:
        print("=== РЕЖИМ ПРЕДПРОСМОТРА (DRY_RUN) — реальной публикации не будет ===\n")

    now = datetime.now(MSK)
    print(f"Текущее время по Москве: {now.strftime('%d.%m.%Y %H:%M')}")

    # Публикуем только когда "созрел" слот (см. NEWS_SLOTS). Исключение —
    # ручной запуск с галочкой force_now: тогда публикуем сразу.
    slot_key = None
    slots_done = set()
    if FORCE_NOW:
        print("force_now: слоты публикации игнорируются — публикую сразу")
    else:
        slots_done = load_slots_state()
        slot_key = get_due_slot(now, slots_done)
        if not slot_key:
            print(
                "Сейчас нет слота публикации новости (не время, слот уже отработан "
                "или окно прошло) — выхожу. Чтобы опубликовать вручную вне слота, "
                "включите галочку force_now."
            )
            return
        print(f"Слот публикации: {slot_key}")

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

        result = try_post_article(article)
        if result == RESULT_POSTED:
            posted_count += 1
            if not DRY_RUN:
                state.add(article["url"])
                changed = True
        elif result == RESULT_REFUSED and not DRY_RUN:
            # Отказ контент-фильтра YandexGPT по одной и той же статье не
            # проходит от повторных попыток. Раньше такая статья оставалась
            # в начале очереди и съедала попытки в каждом следующем запуске,
            # из-за чего новость могла не выходить целый день. Помечаем её
            # как обработанную — она больше не будет блокировать очередь.
            state.add(article["url"])
            changed = True
        # при других неудачах просто переходим к следующему кандидату —
        # проблемная статья не блокирует остальные в этом же запуске

    if posted_count == 0:
        print(
            f"Ни одна из {len(candidates)} проверенных статей не опубликовалась в этот раз. "
            "Проверьте логи выше на ошибки рерайта/публикации."
        )

    if changed:
        save_state(state)

    # Слот закрываем только после успешной публикации — если не вышло,
    # следующий запуск по расписанию (через 30 минут) попробует снова.
    if posted_count > 0 and slot_key and not DRY_RUN:
        slots_done.add(slot_key)
        save_slots_state(slots_done)
        print(f"Слот {slot_key} отмечен как выполненный")


if __name__ == "__main__":
    main()
