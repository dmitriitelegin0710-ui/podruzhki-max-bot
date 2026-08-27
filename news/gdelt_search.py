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


def get_gdelt():
    for attempt in range(1, 4):
        print(f"Попытка GDELT: {attempt}/3")

        try:
            response = requests.get(
                GDELT_URL,
                params=params,
                headers=headers,
                timeout=60
            )

            print("HTTP:", response.status_code)

            if response.status_code == 429:
                if attempt < 3:
                    print("GDELT временно ограничил запросы.")
                    print("Ждём 60 секунд...")
                    time.sleep(60)
                    continue

                print("GDELT всё ещё возвращает 429.")
                return None

            response.raise_for_status()
            return response.json()

        except requests.exceptions.Timeout:
            print("Таймаут GDELT.")

            if attempt < 3:
                print("Ждём 30 секунд...")
                time.sleep(30)

        except requests.exceptions.RequestException as e:
            print("Ошибка GDELT:", e)

            if attempt < 3:
                print("Ждём 30 секунд...")
                time.sleep(30)

    return None


def extract_text(url):
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=20
        )

        if response.status_code != 200:
            return ""

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

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
        print("Ошибка загрузки статьи:", e)
        return ""


print("Запрашиваем GDELT...")
print()

data = get_gdelt()

if data is None:
    print()
    print("GDELT недоступен после нескольких попыток.")
    print("Запуск остановлен.")
    raise SystemExit(1)

articles = data.get("articles", [])

print()
print("GDELT нашёл:", len(articles))

result = []

for number, article in enumerate(articles, 1):

    title = article.get("title", "")
    url = article.get("url", "")
    domain = article.get("domain", "")
    date = article.get("seendate", "")

    print()
    print(f"[{number}/{len(articles)}] {title}")
    print("Источник:", domain)

    if not url:
        print("Нет URL — пропускаем.")
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


with open(
    OUTPUT_FILE,
    "w",
    encoding="utf-8"
) as f:

    json.dump(
        result,
        f,
        ensure_ascii=False,
        indent=2
    )


print()
print("=" * 50)
print("ГОТОВО")
print("Статей сохранено:", len(result))
print("Файл:", OUTPUT_FILE)
print("=" * 50)
