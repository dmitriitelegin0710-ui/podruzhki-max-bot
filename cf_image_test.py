"""
Тест качества генерации картинок через Cloudflare Workers AI.
Отдельный скрипт: основной бот он не трогает и ничего никуда не публикует.

Генерирует по несколько вариантов для набора промптов, похожих на ваши
рубрики (завтрак, уход за кожей, образ, Таро, мама с ребёнком, финансы,
вечерний ритуал и т.д.), плюс два «стресс-теста» (руки и лицо — самое
слабое место нейросетей). Всё сохраняется в out/ вместе с index.html —
галереей, где под каждой картинкой подписан промпт.

Переменные окружения:
  CF_ACCOUNT_ID, CF_API_TOKEN  — обязательно (GitHub Secrets)
  CF_MODEL    — по умолчанию @cf/black-forest-labs/flux-1-schnell
                для сравнения: @cf/stabilityai/stable-diffusion-xl-base-1.0
  SAMPLES     — сколько вариантов на каждый промпт (по умолчанию 2)
  STEPS       — шаги для FLUX (по умолчанию 4, максимум 8)
  WIDTH, HEIGHT — только для SDXL-моделей (например 768 и 1024 — вертикальный кадр)
  ONLY        — часть названия промпта, чтобы прогнать только его (например: manikur)
"""
import base64
import functools
import html
import os
import sys
import time

import requests

print = functools.partial(print, flush=True)

ACCOUNT_ID = os.environ["CF_ACCOUNT_ID"].strip()
API_TOKEN = os.environ["CF_API_TOKEN"].strip()
MODEL = os.environ.get("CF_MODEL") or "@cf/black-forest-labs/flux-1-schnell"
SAMPLES = int(os.environ.get("SAMPLES") or 2)
STEPS = int(os.environ.get("STEPS") or 4)
WIDTH = os.environ.get("WIDTH")
HEIGHT = os.environ.get("HEIGHT")
ONLY = (os.environ.get("ONLY") or "").strip().lower()
OUT_DIR = "out"

STYLE = "natural light, soft colors, high quality photo, no text, no watermark"

PROMPTS = [
    ("utro_zavtrak", "cozy morning breakfast on a light table by the window, coffee cup, croissant, fresh flowers"),
    ("recept", "healthy breakfast bowl with oatmeal, berries and nuts, food photography, top view"),
    ("krasota_ukhod", "woman applying face cream, skincare routine, bathroom, soft morning light"),
    ("stil_osen", "autumn outfit for a woman: beige trench coat, knit sweater, boots, street style"),
    ("ezoterika_taro", "tarot cards spread on a dark wooden table with candles and crystals, mystical atmosphere"),
    ("mama_rebenok", "mother and small child reading a book together on a cozy sofa at home"),
    ("finansy", "woman planning a family budget with notebook, calculator and coffee at a home desk"),
    ("vecher_ritual", "evening relaxation: candle, cup of herbal tea, soft blanket, warm cozy light"),
    ("istoriya_sila", "confident woman silhouette at sunset on a hill, inspiring mood"),
    ("test_ruki", "close-up of a woman's hands holding a cup of tea, five fingers on each hand, detailed hands"),
    ("test_litso", "portrait of a smiling woman about 35 years old, natural makeup, looking at the camera"),
]


def generate(prompt: str):
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{MODEL}"
    body = {"prompt": f"{prompt}, {STYLE}"}
    if "flux" in MODEL:
        body["steps"] = STEPS
    else:
        if WIDTH and HEIGHT:
            body["width"], body["height"] = int(WIDTH), int(HEIGHT)
    headers = {"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}

    last_error = None
    for attempt in (1, 2):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=(15, 60))
        except requests.exceptions.RequestException as e:
            last_error = f"сеть/таймаут: {e}"
            print(f"    попытка {attempt}: {last_error}")
            time.sleep(3)
            continue
        if resp.status_code == 200:
            ctype = resp.headers.get("content-type", "")
            if ctype.startswith("image/"):
                return resp.content
            data = resp.json()
            if data.get("success") is False:
                last_error = f"success=false: {str(data.get('errors'))[:300]}"
            else:
                return base64.b64decode(data["result"]["image"])
        else:
            last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
        print(f"    попытка {attempt}: {last_error}")
        time.sleep(3)
    raise RuntimeError(last_error)


def extension(data: bytes) -> str:
    return "png" if data[:4] == b"\x89PNG" else "jpg"


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Модель: {MODEL}, вариантов на промпт: {SAMPLES}")

    items = [(n, p) for n, p in PROMPTS if not ONLY or ONLY in n.lower()]
    if not items:
        print(f"ОШИБКА: по фильтру ONLY='{ONLY}' ничего не нашлось")
        sys.exit(1)

    gallery = []
    ok = failed = 0
    streak = 0
    total_time = 0.0

    for name, prompt in items:
        print(f"[{name}] {prompt}")
        for i in range(1, SAMPLES + 1):
            started = time.time()
            try:
                data = generate(prompt)
            except Exception as e:
                failed += 1
                streak += 1
                print(f"  вариант {i}: ОШИБКА — {e}")
                if streak >= 3:
                    print("ОСТАНОВКА: 3 ошибки подряд, дальше нет смысла. Проверьте токен/Account ID/права Workers AI.")
                    sys.exit(1)
                continue
            streak = 0
            elapsed = time.time() - started
            total_time += elapsed
            ok += 1
            filename = f"{name}_{i}.{extension(data)}"
            with open(os.path.join(OUT_DIR, filename), "wb") as f:
                f.write(data)
            print(f"  вариант {i}: {filename}, {len(data) // 1024} КБ, {elapsed:.1f} сек")
            gallery.append((filename, name, prompt))
            time.sleep(1)

    cards = "\n".join(
        f'<figure><img src="{html.escape(fn)}" loading="lazy">'
        f"<figcaption><b>{html.escape(n)}</b><br>{html.escape(p)}</figcaption></figure>"
        for fn, n, p in gallery
    )
    page = (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>Тест {html.escape(MODEL)}</title>"
        "<style>body{font-family:sans-serif;margin:16px;background:#f5f5f5}"
        "main{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px}"
        "figure{margin:0;background:#fff;border-radius:8px;padding:8px}"
        "img{width:100%;border-radius:6px}figcaption{font-size:12px;margin-top:6px}</style>"
        f"<h2>{html.escape(MODEL)}</h2><main>{cards}</main>"
    )
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)

    avg = total_time / ok if ok else 0
    print(f"ИТОГО: успешно {ok}, ошибок {failed}, среднее время {avg:.1f} сек на картинку")
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
