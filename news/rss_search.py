"""
Получение новостей шоу-бизнеса напрямую из тех же RSS-источников,
что использует сайт podruzhki.online (см. import_rss.py на сайте) —
вместо поиска через GDELT.

Заменяет связку gdelt_search.py + filter_articles.py: результат этого
скрипта пишется сразу в news/filtered_articles.json — в том же формате,
который уже читает rewrite_and_post.py, поэтому сам rewrite_and_post.py
менять не нужно.

ВАЖНО: список лент и стоп-слова СОЗНАТЕЛЬНО продублированы из
import_rss.py сайта, а не импортированы оттуда — бот и сайт живут в
разных репозиториях. При изменении лент/ключевых слов на сайте не
забудьте перенести изменения и сюда вручную (или вынести оба списка в
общий JSON-файл, если захотите синхронизировать автоматически).

Требуемая зависимость: feedparser (добавьте в requirements.txt бота).
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import feedparser

OUTPUT_FILE = Path("news/filtered_articles.json")

# Те же ленты, что использует сайт (см. import_rss.py). Держите этот
# список в синхроне вручную, если на сайте появятся новые источники.
# Те же ленты, что использует сайт (см. import_rss.py). Держите этот
# список в синхроне вручную, если на сайте появятся новые источники.
# Положительный фильтр по темам (ниже, AUDIENCE_TOPIC_*) теперь общий
# для всех лент — отдельного списка ключевых слов на ленту больше нет.
RSS_FEEDS = [
    {
        "url": "https://www.starhit.ru/rss/",
        "source_name": "StarHit",
        "limit": 15,
    },
    {
        "url": "https://www.eg.ru/rss.xml",
        "source_name": "Экспресс газета",
        "limit": 15,
    },
]

# --- Стоп-темы: смерть / похороны / тяжёлые болезни ---
# В отличие от простого substring-поиска, который на сайте пропускал
# "умирающая" (не совпало с "умерла") и "смерти" (не совпало с "смерть"),
# здесь стемы ищутся с границей слова СЛЕВА (\b) и БЕЗ границы справа —
# это ловит любые падежи и формы: "умер/умерла/умерших/умирающий" все
# совпадут по стему "умер" + отдельному стему "умира".
DEATH_ILLNESS_STEMS = [
    # Смерть, в любой форме
    "смерт",            # смерть, смерти, смертельно
    "умер",             # умер, умерла, умершего
    "умира",            # умирающий, умирала, умирают
    "скончал",          # скончался, скончалась
    "не стало",         # "не стало актрисы" — частый эвфемизм
    "ушел из жизни",
    "ушёл из жизни",
    "ушла из жизни",
    "покойн",           # покойный, покойник
    "погиб",            # погиб, погибла, гибель
    "трагически",

    # Похороны / траур
    "похорон",
    "траур",
    "панихид",
    "кладбищ",
    "мемориал",
    "надгроб",
    "прощание с",       # "прощание с актрисой"
    "почтили память",   # "Такменев, Пьеха почтили память Соседова"
    "в память о",
    "светлая память",
    "годовщина ухода",
    "годовщина смерти",

    # Насильственная смерть / суицид
    "суицид",
    "самоубийств",
    "покончил",
    "покончила",
    "повесил",
    "убил",
    "убийств",
    "зарезал",
    "застрелил",
    "разбился",
    "разбилась",
    "пропал без вести",
    "пропала без вести",

    # Тяжёлые болезни / состояния
    "онкологи",
    "диагностировал",
    "неизлечим",
    "инсульт",
    "инфаркт",
    "реанимац",
    "паралич",
    "парализова",
]

# Короткие/неоднозначные слова требуют границы С ОБЕИХ сторон (\b...\b),
# иначе "рак" ложно совпадёт внутри "ракета", а "кома" — внутри "команда".
DEATH_ILLNESS_WHOLE_WORDS = [
    "рак",
    "кома",
    "изнасил",
    "насилие",
    "насильник",
]

# --- Стоп-темы: СВО, война, Украина, политика ---
# Хватает ОДНОГО совпадения в заголовке ИЛИ в тексте, чтобы статья была
# отброшена — задача полностью исключить эти темы из светского паблика,
# а не просто снизить их долю.
WAR_UKRAINE_POLITICS_STEMS = [
    # СВО / война
    "спецоперац",
    "сво ",             # с пробелом — не задевает другие слова на "сво"
    "войн",
    "военн",
    "фронт",
    "обстрел",
    "бпла",
    "дрон",
    "ракетн удар",
    "боевых действ",
    "мобилизац",
    "оккупац",

    # Украина и связанные топонимы/термины
    "украин",
    "зеленск",
    "порошенк",
    "зсу",
    "всу",
    "донбасс",
    "донецк",
    "луганск",
    "днр",
    "лнр",
    "мариуполь",
    "запорож",
    "херсон",
    "одесс",
    "харьков",
    "киев",
    "майдан",

    # Политика в целом
    "президент",
    "правительств",
    "министр",
    "депутат",
    "парламент",
    "госдум",
    "политик",
    "санкци",
    "выборы президент",
]

WAR_UKRAINE_POLITICS_WHOLE_WORDS = [
    "сво",
]

# --- Положительный фильтр: темы, которые реально интересны аудитории ---
# Мало того, что новость "про шоу-биз" в широком смысле (это уже
# гарантируют сами источники — StarHit и eg.ru) — нужно, чтобы она была
# ещё и про то, что цепляет женскую аудиторию: отношения, семья, внешность
# и личные переживания знаменитостей, а не любой инфоповод с их участием
# (например, "кто-то купил квартиру" сам по себе в эту категорию не
# попадает, если рядом нет ничего про отношения/семью/внешность).
# Достаточно ОДНОГО совпадения в заголовке ИЛИ в тексте — новости обычно
# короткие, и требовать несколько совпадений было бы слишком строго.
AUDIENCE_TOPIC_STEMS = [
    # Отношения
    "роман",            # роман, романтическ (стем длинный, "Романов" почти
                         # не встречается в шоу-биз лентах — риск принят)
    "отношени",
    "влюб",
    "жених",
    "невест",
    "помолвк",
    "свадьб",
    "развод",
    "измен",
    "расста",           # рассталась, расставание
    "воссоедин",
    "бойфренд",
    "возлюбл",
    "экс-муж",
    "экс-жена",
    "бывш",
    "любовник",
    "любовниц",

    # Семья / дети
    "беремен",
    "родила",
    "родил",
    "рожа",             # рожает, рожала, рожать — форма, которую не ловит "родил"
    "декрет",
    "ребен",
    "ребён",
    "дочь",
    "сын",
    "дети",
    "материнств",
    "отцовств",
    "супруг",
    "муж",              # длинный контекст статей шоу-биза снижает риск
                         # ложного срабатывания на "мужчина"/"мужество"
    "жена",
    "семь",              # семья, семейный

    # Внешность / стиль
    "похудел",
    "поправил",
    "пластик",
    "операц",           # чаще всего "пластическая операция" в этом контексте
    "диет",
    "наряд",
    "плать",
    "макияж",
    "стрижк",
    "ботокс",
    "фотосесс",
    "купальник",
    "внешност",
    "похорошел",

    # Личные откровения / эмоции
    "призналась",
    "признался",
    "откровени",
    "расплакалась",
    "расплакался",
    "пожаловалась",
    "пожаловался",
    "переживает",

    # Скандалы/слухи вокруг личной жизни (не путать с политическими скандалами —
    # те уже отсечены WAR_UKRAINE_POLITICS_STEMS выше)
    "скандал",
    "слух",
    "компромат",
    "разоблачени",
]

AUDIENCE_TOPIC_WHOLE_WORDS = [
    "секс",
]

# Стемы, которым нужен более точный regex вручную — обычный левый стем
# случайно ловит совсем другую тему:
#   "фигур" без уточнения совпадает с "фигурным катанием" (спорт);
#   "образ" без уточнения совпадает с "образованием"/"образец".
# Negative lookahead исключает именно эти конкретные слова, оставляя
# "фигура/фигуры/фигурой" (тело) и "образ/образа/образом" (стиль).
AUDIENCE_TOPIC_CUSTOM_PATTERNS = [
    re.compile(r"\bфигур(?!н)"),
    re.compile(r"\bобраз(?!ован|ец)"),
]


def _compile_stems(words):
    """Границы слова только слева — ловит любые падежи и суффиксы."""
    return [re.compile(r"\b" + re.escape(w)) for w in words]


def _compile_whole_words(words):
    """Границы слова с обеих сторон — для коротких/неоднозначных слов."""
    return [re.compile(r"\b" + re.escape(w) + r"\b") for w in words]


DEATH_ILLNESS_PATTERNS = (
    _compile_stems(DEATH_ILLNESS_STEMS)
    + _compile_whole_words(DEATH_ILLNESS_WHOLE_WORDS)
)
WAR_UKRAINE_POLITICS_PATTERNS = (
    _compile_stems(WAR_UKRAINE_POLITICS_STEMS)
    + _compile_whole_words(WAR_UKRAINE_POLITICS_WHOLE_WORDS)
)
AUDIENCE_TOPIC_PATTERNS = (
    _compile_stems(AUDIENCE_TOPIC_STEMS)
    + _compile_whole_words(AUDIENCE_TOPIC_WHOLE_WORDS)
    + AUDIENCE_TOPIC_CUSTOM_PATTERNS
)

# Максимум статей суммарно по всем лентам, которые попадут в
# filtered_articles.json за один запуск. rewrite_and_post.py всё равно
# сам пропустит уже опубликованные (posted_news.json) и попробует не
# больше MAX_ATTEMPTS_PER_RUN кандидатов — этот лимит просто ограничивает
# размер файла и объём работы feedparser'а.
MAX_TOTAL_ARTICLES = 40


def find_text(entry) -> str:
    """Пробуем несколько возможных полей, где может быть текст новости —
    как на сайте, так что описание совпадает 1-в-1."""
    if entry.get("summary"):
        return entry.get("summary").strip()
    if entry.get("description"):
        return entry.get("description").strip()
    if "content" in entry and entry.content:
        value = entry.content[0].get("value", "")
        if value:
            return value.strip()
    if entry.get("subtitle"):
        return entry.get("subtitle").strip()
    return ""


def find_image_url(entry):
    if "media_content" in entry and entry.media_content:
        url = entry.media_content[0].get("url")
        if url:
            return url
    if "media_thumbnail" in entry and entry.media_thumbnail:
        url = entry.media_thumbnail[0].get("url")
        if url:
            return url
    if "links" in entry:
        for link in entry.links:
            if link.get("type", "").startswith("image"):
                return link.get("href")
    if "enclosures" in entry:
        for enclosure in entry.enclosures:
            if enclosure.get("type", "").startswith("image"):
                return enclosure.get("href") or enclosure.get("url")
    return None


def normalize(text: str) -> str:
    """lower + ё→е, чтобы стемы вида 'смерт' ловили и 'смёрт...' формы."""
    return re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()


def matches_patterns(text: str, patterns) -> bool:
    return any(p.search(text) for p in patterns)


def is_excluded(title, text) -> bool:
    """Жёсткое исключение: смерть/похороны/тяжёлые болезни ИЛИ
    СВО/война/Украина/политика — хватает одного совпадения в заголовке
    или в тексте, чтобы статья не попала в подборку для бота."""
    haystack = normalize(f"{title} {text}")
    if matches_patterns(haystack, DEATH_ILLNESS_PATTERNS):
        return True
    if matches_patterns(haystack, WAR_UKRAINE_POLITICS_PATTERNS):
        return True
    return False


def is_audience_relevant(title, text) -> bool:
    """Положительный фильтр: статья должна быть не просто 'про шоу-биз',
    а именно про отношения/семью/внешность/личные переживания — то, что
    реально интересно женской аудитории. Одного совпадения достаточно."""
    haystack = normalize(f"{title} {text}")
    return matches_patterns(haystack, AUDIENCE_TOPIC_PATTERNS)


def entry_date_iso(entry) -> str:
    """Достаёт дату публикации записи в ISO-формате; если не получилось —
    используем текущее время как запасной вариант (не критично, дата не
    используется для дедупликации — та идёт по url)."""
    for field in ("published_parsed", "updated_parsed"):
        value = entry.get(field)
        if value:
            try:
                return datetime(*value[:6], tzinfo=timezone.utc).isoformat()
            except Exception:
                pass
    return datetime.now(timezone.utc).isoformat()


def fetch_feed(feed_config: dict) -> list:
    url = feed_config["url"]
    source_name = feed_config["source_name"]
    limit = feed_config.get("limit", 15)

    print(f"Читаю ленту: {source_name} ({url})")
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0"})
    except Exception as e:
        print(f"  Ошибка запроса к ленте {source_name}: {e}")
        return []

    if feed.bozo and not feed.entries:
        print(f"  Лента не читается: {getattr(feed, 'bozo_exception', 'неизвестно')}")
        return []

    print(f"  Всего записей в ленте: {len(feed.entries)}")

    articles = []
    excluded_count = 0
    off_topic_count = 0
    for entry in feed.entries[:limit]:
        title = entry.get("title", "").strip()
        source_url = entry.get("link", "")
        if not title or not source_url:
            continue

        text = find_text(entry)

        if is_excluded(title, text):
            excluded_count += 1
            continue
        if not is_audience_relevant(title, text):
            off_topic_count += 1
            continue

        articles.append({
            "title": title,
            "source": source_name,
            "url": source_url,
            "date": entry_date_iso(entry),
            "text": text,
            "image_url": find_image_url(entry),
        })

    print(f"  Прошло фильтр: {len(articles)} "
          f"(отсеяно тяжёлыми/политикой: {excluded_count}, "
          f"не по теме аудитории: {off_topic_count})")
    return articles


def main():
    all_articles = []
    seen_urls = set()

    for feed_config in RSS_FEEDS:
        for article in fetch_feed(feed_config):
            if article["url"] in seen_urls:
                continue
            seen_urls.add(article["url"])
            all_articles.append(article)

    # Свежие сверху — ленты и так отдают записи от новых к старым.
    all_articles = all_articles[:MAX_TOTAL_ARTICLES]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(all_articles, f, ensure_ascii=False, indent=2)

    print(f"Готово. Записано статей в {OUTPUT_FILE}: {len(all_articles)}")


if __name__ == "__main__":
    main()
