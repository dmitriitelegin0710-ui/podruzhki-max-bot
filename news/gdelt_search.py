import json
import time
import requests
from bs4 import BeautifulSoup

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
OUTPUT_FILE = "news/articles.json"

params = {
    "query": "entertainment sourcelang:russian",
    "mode": "artlist",
    "format": "json",
    "maxrecords": 20,
    "timespan": "24h",
    "sort": "datedesc",
}

headers = {
    "User-Agent": "Mozilla/5.0"
}


def extract_text(url):
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=20
        )

        if response.status_code != 200:
            return ""

        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup([
            "script",
            "style",
            "nav",
            "header",
            "footer",
            "aside",
            "form"
        ]):
            tag.decompose()

        paragraphs = []

        for p in soup.find_all("p"):
            text = p.get_text(" ", strip=True)

            if len(text) >= 40:
                paragraphs.append(text)

        return "\n".join(paragraphs)

    except Exception as e:
        print("Ошибка загрузки:", e)
        return ""


print("Запрашиваем GDELT...")

response = requests.get(
    GDELT_URL,
    params=params,
    headers=headers,
    timeout=60
)

response.raise_for_status()

data = response.json()
articles = data.get("articles", [])

print("GDELT нашёл:", len(articles))

result = []

for number, article in enumerate(articles, 1):

    title = article.get("title", "")
    url = article.get("url", "")
    domain = article.get("domain", "")
    date = article.get("seendate", "")

    print(f"\n[{number}/{len(articles)}] {title}")
    print("Источник:", domain)

    if not url:
        continue

    text = extract_text(url)

    print("Текст:", len(text), "символов")

    if len(text) < 300:
        print("Пропускаем — текста недостаточно.")
        continue

    result.append({
        "title": title,
        "source": domain,
        "url": url,
        "date": date,
        "text": text
    })

    time.sleep(1)


with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(
        result,
        f,
        ensure_ascii=False,
        indent=2
    )

print("\n==============================")
print("ГОТОВО")
print("Статей сохранено:", len(result))
print("Файл:", OUTPUT_FILE)
print("==============================")
