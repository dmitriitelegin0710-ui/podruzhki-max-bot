import requests

URL = "https://api.gdeltproject.org/api/v2/doc/doc"

queries = [
    "актриса",
    "певица",
    "актер",
    "звезда",
    "шоу-бизнес",
    "шоубизнес",
    "кино",
    "телевидение",
    "музыка",
    "артист",
]

all_articles = {}

for query in queries:
    print(f"\n=== ПОИСК: {query} ===")

    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": 10,
        "timespan": "24h",
        "sort": "datedesc",
    }

    try:
        response = requests.get(URL, params=params, timeout=30)
        response.raise_for_status()

        data = response.json()
        articles = data.get("articles", [])

        print(f"Найдено: {len(articles)}")

        for article in articles:
            url = article.get("url")

            if url:
                all_articles[url] = article

    except Exception as e:
        print(f"Ошибка: {e}")


print("\n")
print("=" * 70)
print(f"ВСЕГО УНИКАЛЬНЫХ НОВОСТЕЙ: {len(all_articles)}")
print("=" * 70)

for number, article in enumerate(all_articles.values(), 1):
    print(f"\n#{number}")
    print("Дата:", article.get("seendate"))
    print("Заголовок:", article.get("title"))
    print("Источник:", article.get("domain"))
    print("URL:", article.get("url"))
    print("-" * 70)
