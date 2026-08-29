import json
import re
from pathlib import Path
from urllib.parse import urlparse


INPUT_FILE = Path("news/articles.json")
OUTPUT_FILE = Path("news/filtered_articles.json")


def is_russian_domain(url: str) -> bool:
    """Оставляем только .ru-домены — sourcelang:russian в GDELT ловит
    русскоязычные сайты по всему миру (.kz, .by, .com и т.п.), а не
    только российские."""
    domain = urlparse(url).netloc.lower()
    return domain.endswith(".ru")


# Слова и фразы, характерные для шоу-бизнеса.
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
    "режиссер",
    "продюсер",

    # Музыка
    "песн",
    "альбом",
    "трек",
    "клип",
    "концерт",
    "тур",
    "гастрол",
    "музык",
    "премьера песни",
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
    "семь",
    "возлюблен",
    "бойфренд",
    "муж",
    "жена",

    # Социальные сети / внешний вид
    "instagram",
    "инстаграм",
    "соцсет",
    "социальн сет",
    "фотографи",
    "фото",
    "образ",
    "наряд",
    "плать",
    "красн дорожк",
]


# Темы, которые часто дают ложные совпадения.
# ВАЖНО: эти слова ищутся как ЦЕЛЫЕ СЛОВА (с границей \b слева), а не как
# произвольная подстрока — иначе, например, "акци" ложно находится внутри
# "реакция", "банк" — внутри "банкет", "фронт" — внутри "конфронтация",
# "арм" — внутри "Армани". Раньше это могло отбраковывать совершенно
# нормальные новости о звёздах из-за одного случайного слова в заголовке.
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
    "войн",
    "военн",
    "армия",
    "фронт",

    # Криминал / происшествия
    "убийств",
    "убил",
    "убита",
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
]


def normalize(text):
    """Приводит текст к удобному для поиска виду."""
    if not text:
        return ""

    text = text.lower()
    text = text.replace("ё", "е")

    # Убираем лишние пробелы.
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def _compile_word_patterns(keywords):
    """Компилирует ключевые слова в regex с границей слова слева (\\b),
    чтобы "акци" не находилось внутри "реакция" и т.п. Слова из списков —
    это, как правило, корни/начала слов ("развод", "певиц"), поэтому
    границу справа не ставим специально: она бы сломала совпадения вроде
    "певица" при ключе "певиц"."""
    return [re.compile(r"\b" + re.escape(keyword)) for keyword in keywords]


ENTERTAINMENT_PATTERNS = _compile_word_patterns(ENTERTAINMENT_KEYWORDS)
EXCLUDED_PATTERNS = _compile_word_patterns(EXCLUDED_KEYWORDS)


def contains_any(text, patterns):
    """Проверяет наличие хотя бы одного ключевого слова (по границе слова)."""
    return any(pattern.search(text) for pattern in patterns)


def count_matches(text, patterns):
    """Считает количество совпавших тематических ключей (по границе слова)."""
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
    новость шоу-бизнеса.
    """

    if not is_russian_domain(article.get("url", "")):
        return False

    title = normalize(article.get("title", ""))
    text = normalize(article.get("text", ""))

    # Заголовок и текст должны существовать.
    if not title:
        return False

    if len(text) < 300:
        return False

    # Если вместо статьи скачались правила/служебная страница.
    if looks_like_comment_rules(text):
        return False

    # Слишком маленький текст относительно огромной страницы.
    if len(text.split()) < 50:
        return False

    # Проверяем заголовок отдельно.
    title_entertainment = contains_any(title, ENTERTAINMENT_PATTERNS)

    # Основной текст.
    text_entertainment_count = count_matches(text, ENTERTAINMENT_PATTERNS)

    # Исключающие темы.
    title_excluded = contains_any(title, EXCLUDED_PATTERNS)
    text_excluded_count = count_matches(text, EXCLUDED_PATTERNS)

    # Если в заголовке явно политика/спорт/etc. —
    # материал не подходит.
    if title_excluded and not title_entertainment:
        return False

    # Если в тексте слишком много признаков посторонней темы.
    if text_excluded_count >= 4 and not title_entertainment:
        return False

    # Хороший вариант:
    # 1. развлекательное слово есть в заголовке;
    # 2. либо есть несколько признаков шоу-бизнеса в тексте.
    if title_entertainment:
        return True

    if text_entertainment_count >= 3:
        return True

    return False


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

    with INPUT_FILE.open(
        "r",
        encoding="utf-8"
    ) as f:
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

        # Убираем дубли.
        if url and url in seen_urls:
            rejected.append(
                (article.get("title", ""), "дубликат")
            )
            continue

        if url:
            seen_urls.add(url)

        if is_good_article(article):
            clean_article = make_clean_article(article)
            filtered.append(clean_article)

            print("✓ ОСТАВЛЕНО:")
            print(" ", clean_article["title"])
            print()
        else:
            rejected.append(
                (article.get("title", ""), "не прошел фильтр")
            )

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with OUTPUT_FILE.open(
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            filtered,
            f,
            ensure_ascii=False,
            indent=2
        )

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
