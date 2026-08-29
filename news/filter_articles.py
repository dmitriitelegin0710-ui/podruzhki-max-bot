import json
import re
from pathlib import Path


INPUT_FILE = Path("news/articles.json")
OUTPUT_FILE = Path("news/filtered_articles.json")


# Слова и фразы, характерные для шоу-бизнеса и интересные молодой аудитории:
# актёры/актрисы/модели, блогеры/инфлюенсеры, сплетни/скандалы/интриги.
ENTERTAINMENT_KEYWORDS = [
    # Общее
    "шоу-бизнес",
    "шоу бизнес",
    "шоу-биз",
    "звезд",
    "знаменит",
    "селебр",
    "артист",
    "артистка",
    "актер",
    "актриса",
    "певец",
    "певица",
    "музыкант",
    "музыкантка",
    "комик",
    "телеведущ",
    "ведущ",
    "модель",
    "манекенщиц",
    "режиссер",
    "продюсер",

    # Блогеры / молодёжная среда — то, к чему стремимся по аудитории
    "блогер",
    "блогерш",
    "тиктокер",
    "тик-ток",
    "инфлюенсер",
    "стример",
    "стримерш",
    "youtube-звезда",
    "реалити-шоу",
    "реалити шоу",

    # Сплетни / скандалы / интриги — приоритетная тема
    "скандал",
    "сплетн",
    "интриг",
    "слух",
    "измен",
    "разоблач",
    "компромат",
    "уличил",
    "поймали на",
    "жестко ответил",
    "жёстко ответил",
    "разборк",
    "скандальн",
    "конфликт со звезд",

    # Музыка
    "песн",
    "альбом",
    "трек",
    "клип",
    "концерт",
    "тур",
    "гастрол",
    "музык",
    "новый сингл",

    # Кино и ТВ
    "фильм",
    "кино",
    "сериал",
    "шоу",
    "телешоу",
    "телепередач",
    "премьера",
    "съемк",
    "съёмк",
    "роль",
    "премии",
    "премия",
    "церемони",
    "оскар",
    "кинопрем",

    # Публичная жизнь знаменитостей
    "личная жизнь",
    "роман",
    "отношени",
    "свадьб",
    "развод",
    "беремен",
    "ребен",
    "ребён",
    "дочь",
    "сын",
    "возлюблен",
    "бойфренд",
    "муж",
    "жена",

    # Соцсети / внешний вид
    "instagram",
    "инстаграм",
    "тикток",
    "соцсет",
    "фотографи",
    "фото",
    "образ",
    "наряд",
    "плать",
    "красн дорожк",
]


# Признаки "возрастного"/наследного контента — не блокируем жёстко (иногда
# это реально актуальный мировой инфоповод), но используем, чтобы понижать
# такие статьи в очереди публикации в пользу более молодых и свежих тем.
LEGACY_KEYWORDS = [
    "советск",
    "народный артист",
    "заслуженный артист",
    "легенда сцены",
    "легенда советского",
    "юбилей",
    "ветеран сцены",
    "лет исполнилось",
]


# ЖЁСТКИЕ исключения — если совпало, статья отбрасывается ВСЕГДА,
# даже если в заголовке есть слово "актёр"/"певец"/"звезда" и т.п.
# Раньше именно это было багом: смерть/война проходили фильтр, потому что
# в заголовке попутно упоминался артист.
HARD_EXCLUDE_KEYWORDS = [
    # Смерть / похороны
    "умер",
    "умерла",
    "скончал",
    "не стало",
    "похорон",
    "прощание с",
    "траур",
    "погиб",
    "гибель",
    "мертв",

    # Война / military
    "взрыв",
    "обстрел",
    "атак",
    "бпла",
    "фронт",
    "военн",
    "войн",
    "оккупац",
    "снаряд",
    "ракетн удар",
]


# Темы, которые часто дают ложные совпадения (политика, спорт, техника и т.п.).
# Эти слова ищутся как ЦЕЛЫЕ СЛОВА (граница \b слева), а не подстрокой —
# иначе, например, "акци" ложно находится внутри "реакция".
EXCLUDED_KEYWORDS = [
    # Политика
    "президент",
    "правительств",
    "министр",
    "депутат",
    "парламент",
    "выборы президент",
    "выборы в",
    "политик",
    "партия",
    "санкци",

    # Криминал (не смертельный)
    "полици",
    "арестован",
    "задержан",
    "преступлен",

    # Спорт
    "футбол",
    "баскетбол",
    "хоккей",
    "теннис",
    "спортсмен",
    "матч",
    "чемпионат",

    # Игры / технологии
    "видеоигр",
    "компьютерн игр",
    "playstation",
    "xbox",
    "steam",
    "игров",
    "смартфон",
    "iphone",
    "android",

    # Животные
    "животн",
    "собак",
    "кошк",
    "котен",
    "щен",
    "зоопарк",

    # Недвижимость / бизнес
    "недвижим",
    "квартир",
    "ипотек",
    "рынок жилья",
    "акция",
    "акции",
    "биржа",
    "инвестиц",
    "банк",
    "кредит",

    # Общие новости
    "погода",
    "наводнен",
    "землетряс",
    "ураган",
    "пожар",
    "выставка",
    "живопис",
]


def normalize(text):
    """Приводит текст к удобному для поиска виду."""
    if not text:
        return ""

    text = text.lower()
    text = text.replace("ё", "е")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def _compile_word_patterns(keywords):
    """Компилирует ключевые слова в regex с границей слова слева (\\b),
    чтобы "акци" не находилось внутри "реакция" и т.п."""
    return [re.compile(r"\b" + re.escape(keyword)) for keyword in keywords]


ENTERTAINMENT_PATTERNS = _compile_word_patterns(ENTERTAINMENT_KEYWORDS)
EXCLUDED_PATTERNS = _compile_word_patterns(EXCLUDED_KEYWORDS)
HARD_EXCLUDE_PATTERNS = _compile_word_patterns(HARD_EXCLUDE_KEYWORDS)
LEGACY_PATTERNS = _compile_word_patterns(LEGACY_KEYWORDS)

# Слова, которые явно указывают на молодую/актуальную сплетню-тему —
# используются только для приоритизации, не для отбора.
YOUNG_GOSSIP_KEYWORDS = [
    "блогер",
    "тиктокер",
    "инфлюенсер",
    "стример",
    "скандал",
    "сплетн",
    "интриг",
    "слух",
    "измен",
    "разоблач",
    "компромат",
]
YOUNG_GOSSIP_PATTERNS = _compile_word_patterns(YOUNG_GOSSIP_KEYWORDS)


def contains_any(text, patterns):
    return any(pattern.search(text) for pattern in patterns)


def count_matches(text, patterns):
    return sum(1 for pattern in patterns if pattern.search(text))


def looks_like_comment_rules(text):
    """
    Отбрасывает страницы, где вместо статьи загрузились
    правила комментариев, политика сайта и т. п.
    """
    patterns = [
        "правила комментирования",
        "правила комментариев",
        "политика комментирования",
        "оставляя комментарий",
        "оставляя комментарии",
        "комментарии запрещены",
        "правила сайта",
        "пользовательское соглашение",
        "политика конфиденциальности",
    ]

    return any(p in text for p in patterns)


def is_good_article(article):
    """
    Возвращает True, если материал похож на полноценную
    новость шоу-бизнеса, подходящую для молодой аудитории.
    """

    title = normalize(article.get("title", ""))
    text = normalize(article.get("text", ""))

    if not title:
        return False

    if len(text) < 300:
        return False

    if looks_like_comment_rules(text):
        return False

    if len(text.split()) < 50:
        return False

    # ЖЁСТКИЙ фильтр — смерть/похороны/война отбрасываются ВСЕГДА,
    # независимо от того, есть ли в заголовке "актёр"/"звезда" и т.п.
    if contains_any(title, HARD_EXCLUDE_PATTERNS):
        return False

    if count_matches(text, HARD_EXCLUDE_PATTERNS) >= 2:
        return False

    title_entertainment = contains_any(title, ENTERTAINMENT_PATTERNS)
    text_entertainment_count = count_matches(text, ENTERTAINMENT_PATTERNS)

    title_excluded = contains_any(title, EXCLUDED_PATTERNS)
    text_excluded_count = count_matches(text, EXCLUDED_PATTERNS)

    if title_excluded and not title_entertainment:
        return False

    if text_excluded_count >= 4 and not title_entertainment:
        return False

    if title_entertainment:
        return True

    if text_entertainment_count >= 3:
        return True

    return False


def priority_score(article):
    """
    Чем выше число — тем раньше статья должна публиковаться.
    Молодёжные темы (блогеры/сплетни/скандалы) — в приоритете,
    "возрастной"/наследный контент (юбилеи, советские легенды) — в конец
    очереди, но не исключается совсем.
    """
    title = normalize(article.get("title", ""))
    text = normalize(article.get("text", ""))

    score = 0
    score += 3 * count_matches(title, YOUNG_GOSSIP_PATTERNS)
    score += 1 * count_matches(text, YOUNG_GOSSIP_PATTERNS)

    if contains_any(title, LEGACY_PATTERNS) or contains_any(text, LEGACY_PATTERNS):
        score -= 5

    return score


def make_clean_article(article):
    """Оставляет только нужные поля."""
    return {
        "title": article.get("title", "").strip(),
        "source": article.get("source", "").strip(),
        "url": article.get("url", "").strip(),
        "date": article.get("date", "").strip(),
        "text": article.get("text", "").strip(),
        "image_url": article.get("image_url"),
    }


def main():
    print("=" * 60)
    print("ФИЛЬТР НОВОСТЕЙ ШОУ-БИЗНЕСА")
    print("=" * 60)

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Не найден входной файл: {INPUT_FILE}"
        )

    with INPUT_FILE.open("r", encoding="utf-8") as f:
        articles = json.load(f)

    if not isinstance(articles, list):
        raise ValueError(
            "articles.json должен содержать JSON-массив."
        )

    print(f"Получено материалов: {len(articles)}")
    print()

    filtered = []
    rejected = []

    seen_urls = set()

    for article in articles:
        url = article.get("url", "").strip()

        if url and url in seen_urls:
            rejected.append((article.get("title", ""), "дубликат"))
            continue

        if url:
            seen_urls.add(url)

        if is_good_article(article):
            clean_article = make_clean_article(article)
            clean_article["_priority"] = priority_score(article)
            filtered.append(clean_article)

            print("✓ ОСТАВЛЕНО:")
            print(" ", clean_article["title"], f"(приоритет: {clean_article['_priority']})")
            print()
        else:
            rejected.append((article.get("title", ""), "не прошел фильтр"))

    # Сначала — самые "молодёжные"/скандальные темы, потом остальное;
    # "возрастной" контент уходит в конец, но не теряется совсем.
    filtered.sort(key=lambda a: a["_priority"], reverse=True)

    for article in filtered:
        del article["_priority"]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)

    print("=" * 60)
    print("ГОТОВО")
    print("=" * 60)
    print(f"Всего материалов: {len(articles)}")
    print(f"Оставлено: {len(filtered)}")
    print(f"Отброшено: {len(rejected)}")
    print(f"Файл: {OUTPUT_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()
