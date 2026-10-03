"""
Генерация картинки к посту через Cloudflare Workers AI (FLUX.1 schnell)
по ТЕКСТУ самого поста.

Как это работает:
  1) build_image_prompt(): берёт готовый текст поста и просит YandexGPT
     (тот же ключ, что уже используется для постов) выделить главную тему
     и описать подходящую фотографию ОДНОЙ строкой по-английски:
     главный предмет/сцена, 2-4 ключевых объекта, обстановка, настроение.
     Это небольшой запрос (до ~120 токенов ответа). Если YandexGPT недоступен
     или ответил не по-английски — берутся статичные английские
     photo_keywords рубрики из rubrics.json (как для Pexels).
  2) generate_image_bytes(): отправляет описание в Cloudflare и возвращает
     байты картинки (JPEG/PNG).

Переменные окружения (GitHub Secrets):
  CF_ACCOUNT_ID, CF_API_TOKEN   — без них is_configured() == False, и
                                  rubric_post_to_max.py просто берёт фото как раньше
  YANDEX_API_KEY, YANDEX_FOLDER_ID — уже есть в проекте
  CF_MODEL (необяз.)  — по умолчанию @cf/black-forest-labs/flux-1-schnell
  CF_STEPS (необяз.)  — шаги FLUX, 1-8 (по умолчанию 4)
"""
import base64
import os
import re

import requests

CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "").strip()
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "").strip()
CF_MODEL = os.environ.get("CF_MODEL") or "@cf/black-forest-labs/flux-1-schnell"
CF_STEPS = int(os.environ.get("CF_STEPS") or 4)

YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY", "")
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID", "")
YANDEXGPT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
YANDEXGPT_MODEL_URI = "gpt://{folder_id}/yandexgpt-lite/rc"

STYLE_SUFFIX = "natural light, soft colors, high quality photo, no text, no letters, no watermark"
MAX_POST_CHARS_FOR_PROMPT = 1500

DESCRIBE_INSTRUCTIONS = """Ты подбираешь иллюстрацию к посту для женского паблика. Прочитай текст поста и опиши ОДНУ фотографию, которая точно отражает его главную тему.

Требования:
- пиши ПО-АНГЛИЙСКИ, одной строкой, 15-30 слов;
- назови главный предмет или сцену из текста, 2-4 ключевых объекта, обстановку и настроение;
- если речь о конкретной карте Таро, блюде, продукте, предмете одежды, ситуации — покажи именно это;
- без текста и букв на изображении, без брендов, без реальных людей и знаменитостей;
- в ответе ТОЛЬКО описание, без кавычек, без пояснений.

Текст поста:
"""


def is_configured() -> bool:
    return bool(CF_ACCOUNT_ID and CF_API_TOKEN)


def _plain_text(text: str) -> str:
    """Убирает MAX-разметку (**, _, ++) и лишние пустые строки."""
    text = re.sub(r"\*\*|\+\+", "", text)
    text = re.sub(r"(?<!\w)_|_(?!\w)", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _cyrillic_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    cyr = sum(1 for c in letters if "Ѐ" <= c <= "ӿ")
    return cyr / len(letters)


def _describe_with_yandexgpt(post_text: str):
    if not (YANDEX_API_KEY and YANDEX_FOLDER_ID):
        return None
    body = {
        "modelUri": YANDEXGPT_MODEL_URI.format(folder_id=YANDEX_FOLDER_ID),
        "completionOptions": {"stream": False, "temperature": 0.3, "maxTokens": 120},
        "messages": [{
            "role": "user",
            "text": DESCRIBE_INSTRUCTIONS + _plain_text(post_text)[:MAX_POST_CHARS_FOR_PROMPT],
        }],
    }
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
    resp = requests.post(YANDEXGPT_URL, headers=headers, json=body, timeout=40)
    resp.raise_for_status()
    raw = resp.json()["result"]["alternatives"][0]["message"]["text"]
    line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
    line = line.strip("\"'«»` ")
    return line[:300] or None


def build_image_prompt(post_text: str, fallback_keywords=None) -> str:
    """Описание картинки по тексту поста (английский). При любой проблеме —
    статичные ключевые слова рубрики."""
    description = None
    try:
        description = _describe_with_yandexgpt(post_text)
    except Exception as e:
        print(f"Картинка: не удалось описать пост через YandexGPT ({e}), беру ключевые слова рубрики")

    if description and _cyrillic_share(description) > 0.3:
        print("Картинка: YandexGPT ответил не по-английски, беру ключевые слова рубрики")
        description = None

    if not description:
        description = ", ".join(fallback_keywords or []) or "cozy lifestyle scene for women, soft light"
    return f"{description}, {STYLE_SUFFIX}"


def generate_image_bytes(prompt: str) -> bytes:
    """Картинка по готовому английскому описанию. Бросает исключение при ошибке."""
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"
    body = {"prompt": prompt}
    if "flux" in CF_MODEL:
        body["steps"] = CF_STEPS
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}

    last_error = "неизвестная ошибка"
    for attempt in (1, 2):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=(15, 60))
        except requests.exceptions.RequestException as e:
            last_error = f"сеть/таймаут: {e}"
            continue
        if resp.status_code != 200:
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            continue
        if resp.headers.get("content-type", "").startswith("image/"):
            return resp.content
        data = resp.json()
        if data.get("success") is False:
            last_error = f"success=false: {str(data.get('errors'))[:200]}"
            continue
        return base64.b64decode(data["result"]["image"])
    raise RuntimeError(last_error)
