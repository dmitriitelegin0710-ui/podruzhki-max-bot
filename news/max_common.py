"""
Общие функции для публикации в MAX — используются и rubric_post_to_max.py
(после переноса, опционально), и новостным скриптом news/rewrite_and_post.py.
Ничего в rubric_post_to_max.py не меняется автоматически — этот модуль
можно подключить туда позже, отдельно и без риска для рабочего бота.
Требуемые переменные окружения:
  MAX_BOT_TOKEN, MAX_CHAT_ID, PEXELS_API_KEY
"""
import os
import random
import requests
BOT_TOKEN = os.environ["MAX_BOT_TOKEN"]
CHAT_ID = os.environ["MAX_CHAT_ID"]
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")
API_BASE = "https://platform-api2.max.ru"
DEFAULT_BUTTON_TEXT = "Читать на сайте"
DOWNLOAD_HEADERS = {"User-Agent": "Mozilla/5.0"}
def fetch_pexels_image(keywords: list):
    """ИЗМЕНЕНО: раньше выбиралось случайное слово из списка (random.choice)
    и случайное фото из всех 10 найденных результатов — из-за этого
    итоговое фото часто было лишь отдалённо в тему. Теперь ключевые слова
    пробуются ПО ОЧЕРЕДИ (ожидается, что список идёт от самого точного к
    самому общему — см. generate_photo_keywords в rewrite_and_post.py), и
    как только по какому-то слову находятся фото, случайный выбор идёт не
    из всех 10, а из топ-3 самых релевантных по версии Pexels — так фото
    остаётся точным по смыслу, но не повторяется день ото дня."""
    if not keywords or not PEXELS_API_KEY:
        return None
    for keyword in keywords:
        try:
            resp = requests.get(
                "https://api.pexels.com/v1/search",
                params={"query": keyword, "per_page": 10, "orientation": "portrait"},
                headers={"Authorization": PEXELS_API_KEY},
                timeout=20,
            )
            resp.raise_for_status()
            photos = resp.json().get("photos") or []
            if not photos:
                print(f"Pexels: по запросу '{keyword}' ничего не нашлось, пробую следующее слово")
                continue
            top_matches = photos[:3]
            return random.choice(top_matches)["src"]["large"]
        except Exception as e:
            print(f"Pexels: ошибка запроса ({keyword}) — {e}")
    return None
def upload_media_and_get_token(media_url: str):
    meta_resp = requests.post(
        f"{API_BASE}/uploads",
        params={"type": "image"},
        headers={"Authorization": BOT_TOKEN},
        timeout=30,
    )
    meta_resp.raise_for_status()
    meta = meta_resp.json()
    upload_url = meta["url"]
    token = meta.get("token")
    # User-Agent нужен, т.к. некоторые сайты-источники блокируют запросы
    # без него (в т.ч. при скачивании их og:image для новостных постов).
    media_bytes = requests.get(media_url, timeout=60, headers=DOWNLOAD_HEADERS).content
    files = {"data": ("image.jpg", media_bytes)}
    upload_resp = requests.post(upload_url, files=files, timeout=120)
    upload_resp.raise_for_status()
    if not token:
        try:
            body = upload_resp.json()
            token = body.get("token")
            if not token and "photos" in body:
                first_photo = next(iter(body["photos"].values()))
                token = first_photo.get("token")
        except Exception:
            pass
    return token
def build_link_button_attachment(url, button_text=None):
    if not url:
        return None
    text = button_text or DEFAULT_BUTTON_TEXT
    return {
        "type": "inline_keyboard",
        "payload": {"buttons": [[{"type": "link", "text": text, "url": url}]]},
    }
def send_message(text: str, attachments=None) -> requests.Response:
    url = f"{API_BASE}/messages?chat_id={CHAT_ID}"
    headers = {"Authorization": BOT_TOKEN}
    payload = {"text": text, "format": "markdown"}
    if attachments:
        payload["attachments"] = attachments
    return requests.post(url, headers=headers, json=payload, timeout=30)
