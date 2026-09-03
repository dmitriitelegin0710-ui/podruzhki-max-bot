# -*- coding: utf-8 -*-
"""
Бэкенд мини-аппа «Сканер состава» для канала в MAX.

ЧТО ДЕЛАЕТ ЭТОТ ФАЙЛ
---------------------
1. Принимает фото этикетки из мини-аппа, дёргает Yandex Vision (точку
   интеграции см. в analyze_label()) и отдаёт результат анализа —
   БЕЗ упоминания того, что это делает ИИ/OCR: пользователь получает
   готовую рекомендацию, а не "технологию".
2. Считает баланс сканирований на пользователя: 1 бесплатный + платные
   пакеты. Хранится в SQLite (файл scans.db, создаётся автоматически
   рядом со скриптом — отдельная БД не нужна).
3. Продаёт пакеты сканирований через ЮKassa: создаёт платёжную ссылку,
   принимает webhook об оплате, начисляет сканы.

ГДЕ ЭТО ДОЛЖНО ЗАПУСКАТЬСЯ
---------------------------
GitHub только хранит код — сам он ничего не выполняет. Нужен любой
постоянно работающий Python-процесс с открытым портом наружу:
  - свой VPS:              gunicorn -w 2 -b 0.0.0.0:8000 server:app
  - Render.com / Railway:  Start Command = "gunicorn server:app"
  - PythonAnywhere:        через их WSGI-конфиг
Мини-апп обращается к этому серверу по HTTPS (см. BACKEND_BASE_URL
в app.js мини-аппа) — у сервера обязательно должен быть HTTPS-домен,
иначе MAX/браузер заблокирует запросы со страницы, открытой по HTTPS.

НАСТРОЙКА (переменные окружения)
----------------------------------
MAX_BOT_TOKEN          — токен бота в MAX (для проверки initData)
YANDEX_VISION_API_KEY  — API-ключ сервисного аккаунта Yandex Cloud
YOOKASSA_SHOP_ID        — shopId из личного кабинета ЮKassa
YOOKASSA_SECRET_KEY     — секретный ключ ЮKassa
RETURN_URL              — куда ЮKassa вернёт пользователя после оплаты
                           (обычно ссылка на сам мини-апп в MAX)

Установка зависимостей: pip install -r requirements.txt

Для reg.ru (обычный хостинг, Passenger) — см. отдельно passenger_wsgi.py
и README.md, там пошагово. Секреты в этом случае задаются не через
переменные окружения ОС, а прямо в passenger_wsgi.py (Passenger не видит
export'ы из .bashrc), поэтому все константы ниже читаются через
os.environ.get(...) — это будет работать в обоих случаях, если
passenger_wsgi.py выставит os.environ[...] ДО импорта этого файла.
"""

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from urllib.parse import parse_qsl

import requests
from flask import Flask, request, jsonify, g

# ============================================================================
# КОНФИГ
# ============================================================================

MAX_BOT_TOKEN = os.environ.get("MAX_BOT_TOKEN", "")
YANDEX_VISION_API_KEY = os.environ.get("YANDEX_VISION_API_KEY", "")
YOOKASSA_SHOP_ID = os.environ.get("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.environ.get("YOOKASSA_SECRET_KEY", "")
RETURN_URL = os.environ.get("RETURN_URL", "https://xn--d1aeghrfjy.online/max/scan/")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scans.db")

FREE_TRIAL_SCANS = 1  # бесплатных сканирований на нового пользователя

# Пакеты сканирований на продажу. Ключ пакета передаётся с фронта как есть.
PACKAGES = {
    "pack_5":  {"scans": 5,  "price_rub": 149, "title": "5 сканирований"},
    "pack_15": {"scans": 15, "price_rub": 349, "title": "15 сканирований"},
    "pack_50": {"scans": 50, "price_rub": 899, "title": "50 сканирований"},
}

app = Flask(__name__)


# ============================================================================
# БАЗА ДАННЫХ (SQLite, один файл, без отдельного сервера БД)
# ============================================================================

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS balances (
            identity      TEXT PRIMARY KEY,
            free_used     INTEGER NOT NULL DEFAULT 0,
            paid_balance  INTEGER NOT NULL DEFAULT 0,
            created_at    INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
            order_id      TEXT PRIMARY KEY,
            identity      TEXT NOT NULL,
            package_id    TEXT NOT NULL,
            yk_payment_id TEXT,
            status        TEXT NOT NULL DEFAULT 'pending',
            created_at    INTEGER NOT NULL
        );
    """)
    conn.commit()
    conn.close()


def get_or_create_balance(identity: str) -> sqlite3.Row:
    db = get_db()
    row = db.execute("SELECT * FROM balances WHERE identity = ?", (identity,)).fetchone()
    if row is None:
        db.execute(
            "INSERT INTO balances (identity, free_used, paid_balance, created_at) VALUES (?, 0, 0, ?)",
            (identity, int(time.time())),
        )
        db.commit()
        row = db.execute("SELECT * FROM balances WHERE identity = ?", (identity,)).fetchone()
    return row


def scans_available(row: sqlite3.Row) -> int:
    free_left = max(0, FREE_TRIAL_SCANS - row["free_used"])
    return free_left + row["paid_balance"]


def consume_one_scan(identity: str):
    """Списывает 1 скан: сперва бесплатный лимит, потом платный баланс."""
    db = get_db()
    row = get_or_create_balance(identity)
    if row["free_used"] < FREE_TRIAL_SCANS:
        db.execute("UPDATE balances SET free_used = free_used + 1 WHERE identity = ?", (identity,))
    else:
        db.execute("UPDATE balances SET paid_balance = paid_balance - 1 WHERE identity = ?", (identity,))
    db.commit()


# ============================================================================
# ИДЕНТИФИКАЦИЯ ПОЛЬЗОВАТЕЛЯ — оба варианта, как договорились
# ============================================================================
#
# 1) Приоритет: initData из MAX WebApp — подделать нельзя, т.к. проверяется
#    подписью через bot-токен (MAX использует ту же схему, что и Telegram
#    Web Apps: HMAC-SHA256 с секретом на основе литерала "WebAppData").
# 2) Фолбэк: device_id, который фронт сам генерирует и хранит в localStorage,
#    если апп открыт не внутри MAX (например, для теста в обычном браузере).
#    Это легко обойти (снёс localStorage — получил новый бесплатный скан),
#    но лучше, чем ничего, пока нет полноценной авторизации.

def validate_max_init_data(init_data: str, bot_token: str):
    """Проверяет подпись initData и возвращает dict с данными пользователя,
    либо None если подпись неверна или bot_token не задан."""
    if not init_data or not bot_token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    user_raw = pairs.get("user")
    if not user_raw:
        return None
    try:
        return json.loads(user_raw)
    except json.JSONDecodeError:
        return None


def resolve_identity(req) -> str:
    """Возвращает стабильный идентификатор пользователя для этого запроса."""
    init_data = req.headers.get("X-Init-Data") or req.form.get("init_data") or req.args.get("init_data")
    user = validate_max_init_data(init_data, MAX_BOT_TOKEN)
    if user and user.get("id"):
        return f"max:{user['id']}"

    device_id = req.headers.get("X-Device-Id") or req.form.get("device_id") or req.args.get("device_id")
    if device_id:
        return f"dev:{device_id}"

    return None


# ============================================================================
# АНАЛИЗ ЭТИКЕТКИ — точка интеграции вашего готового скрипта
# ============================================================================

def call_yandex_vision_ocr(image_bytes: bytes) -> str:
    """Отправляет фото в Yandex Vision OCR и возвращает распознанный текст.

    Логика — из проверенного скрипта пользователя (test_yandex_vision.py),
    перенесена как есть: тот же endpoint, тот же разбор ответа, то же
    склеивание слов через пробел. Отличие только в источнике данных —
    здесь на входе уже готовые байты фото (из формы мини-аппа), а не
    путь к файлу на диске.
    """
    image_data = base64.b64encode(image_bytes).decode("utf-8")

    url = "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze"
    headers = {
        "Authorization": f"Api-Key {YANDEX_VISION_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "analyze_specs": [
            {
                "content": image_data,
                "features": [
                    {
                        "type": "TEXT_DETECTION",
                        "text_detection_config": {"language_codes": ["ru", "en"]},
                    }
                ],
            }
        ]
    }

    response = requests.post(url, headers=headers, data=json.dumps(body), timeout=30)
    response.raise_for_status()
    result = response.json()

    try:
        blocks = result["results"][0]["results"][0]["textDetection"]["pages"][0]["blocks"]
        full_text = []
        for block in blocks:
            for line in block.get("lines", []):
                words = [w["text"] for w in line.get("words", [])]
                full_text.append(" ".join(words))
        return "\n".join(full_text)
    except (KeyError, IndexError):
        return ""


def build_recommendation(raw_text: str, filter_id: str) -> dict:
    """Превращает распознанный состав в понятную рекомендацию.

    Здесь — простое правило-ориентированное демо (ключевые слова).
    Замените на вызов вашей логики/YandexGPT, если хотите более умный
    разбор состава. Пользователю мы НЕ показываем сырой текст OCR как
    "технологию" — только готовый вывод.
    """
    text_lower = raw_text.lower()

    red_flags = {
        "пальмовое масло": "пальмовое масло",
        "глутамат натрия": "усилитель вкуса (глутамат натрия)",
        "e621": "усилитель вкуса E621",
        "трансжир": "трансжиры",
        "сахар": "высокое содержание сахара",
        "консервант": "консерванты",
        "краситель": "красители",
    }
    allergens = {
        "молоко": "молоко/лактоза", "арахис": "арахис", "орех": "орехи",
        "глютен": "глютен", "соя": "соя", "яйцо": "яйцо",
    }

    found_flags = [label for kw, label in red_flags.items() if kw in text_lower]
    found_allergens = [label for kw, label in allergens.items() if kw in text_lower]

    if filter_id == "allergy" and found_allergens:
        verdict = "caution"
    elif filter_id == "kids" and found_flags:
        verdict = "caution"
    elif len(found_flags) >= 2:
        verdict = "bad"
    elif found_flags:
        verdict = "neutral"
    else:
        verdict = "good"

    verdict_text = {
        "good": "Полезно",
        "neutral": "Можно, но без фанатизма",
        "caution": "Обратите внимание",
        "bad": "Лучше не стоит",
    }[verdict]

    recommendations = []
    if filter_id == "kids":
        recommendations.append(
            "Для детского рациона выбирайте продукты с минимальным списком состава."
            if found_flags else "Состав простой — подходит для детского меню."
        )
    elif filter_id == "allergy":
        recommendations.append(
            f"В составе есть потенциальные аллергены: {', '.join(found_allergens)}."
            if found_allergens else "Явных аллергенов из частого списка не найдено."
        )
    elif filter_id == "healthy":
        recommendations.append(
            "Для ПП лучше поискать аналог с более коротким составом."
            if found_flags else "Состав в целом укладывается в принципы правильного питания."
        )
    else:
        recommendations.append("Ознакомьтесь с составом ниже перед покупкой.")

    if not raw_text.strip():
        return {
            "verdict": "unknown",
            "verdict_text": "Не удалось распознать состав",
            "warnings": [],
            "recommendations": ["Сделайте фото ближе и убедитесь, что текст в кадре не размыт."],
        }

    return {
        "verdict": verdict,
        "verdict_text": verdict_text,
        "warnings": found_flags,
        "recommendations": recommendations,
    }


def analyze_label(image_bytes: bytes, filter_id: str) -> dict:
    raw_text = call_yandex_vision_ocr(image_bytes)
    return build_recommendation(raw_text, filter_id)


# ============================================================================
# HTTP-РУЧКИ
# ============================================================================

@app.get("/api/balance")
def api_balance():
    identity = resolve_identity(request)
    if not identity:
        return jsonify({"error": "no_identity"}), 400
    row = get_or_create_balance(identity)
    return jsonify({
        "free_left": max(0, FREE_TRIAL_SCANS - row["free_used"]),
        "paid_balance": row["paid_balance"],
        "total_available": scans_available(row),
        "packages": PACKAGES,
    })


@app.post("/api/analyze")
def api_analyze():
    identity = resolve_identity(request)
    if not identity:
        return jsonify({"error": "no_identity"}), 400

    row = get_or_create_balance(identity)
    if scans_available(row) <= 0:
        return jsonify({"error": "no_scans_left", "packages": PACKAGES}), 402

    image = request.files.get("image")
    if not image:
        return jsonify({"error": "no_image"}), 400

    filter_id = request.form.get("filter", "none")

    try:
        result = analyze_label(image.read(), filter_id)
    except requests.RequestException:
        return jsonify({"error": "vision_unavailable"}), 502

    consume_one_scan(identity)
    row = get_or_create_balance(identity)
    result["scans_left"] = scans_available(row)
    return jsonify(result)


@app.post("/api/pay/create")
def api_pay_create():
    identity = resolve_identity(request)
    if not identity:
        return jsonify({"error": "no_identity"}), 400

    package_id = (request.get_json(silent=True) or {}).get("package_id") or request.form.get("package_id")
    package = PACKAGES.get(package_id)
    if not package:
        return jsonify({"error": "unknown_package"}), 400

    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
        return jsonify({"error": "payments_not_configured"}), 500

    order_id = str(uuid.uuid4())
    idempotence_key = str(uuid.uuid4())

    payload = {
        "amount": {"value": f"{package['price_rub']:.2f}", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": RETURN_URL},
        "description": f"Пакет сканирований: {package['title']}",
        "metadata": {"order_id": order_id, "identity": identity, "package_id": package_id},
    }
    resp = requests.post(
        "https://api.yookassa.ru/v3/payments",
        auth=(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY),
        headers={"Idempotence-Key": idempotence_key, "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    if resp.status_code >= 300:
        return jsonify({"error": "yookassa_error", "details": resp.text}), 502

    data = resp.json()
    db = get_db()
    db.execute(
        "INSERT INTO orders (order_id, identity, package_id, yk_payment_id, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
        (order_id, identity, package_id, data["id"], int(time.time())),
    )
    db.commit()

    return jsonify({"confirmation_url": data["confirmation"]["confirmation_url"]})


@app.post("/api/pay/webhook")
def api_pay_webhook():
    """ЮKassa шлёт уведомление об изменении статуса платежа. Мы не доверяем
    телу запроса напрямую — перепроверяем статус платежа через API ЮKassa
    (рекомендованный ими способ), это защищает от поддельных webhook'ов."""
    notification = request.get_json(silent=True) or {}
    payment_id = (notification.get("object") or {}).get("id")
    if not payment_id:
        return "", 400

    resp = requests.get(
        f"https://api.yookassa.ru/v3/payments/{payment_id}",
        auth=(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY),
        timeout=15,
    )
    if resp.status_code >= 300:
        return "", 502
    payment = resp.json()

    if payment.get("status") != "succeeded":
        return "", 200

    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE yk_payment_id = ?", (payment_id,)).fetchone()
    if not order or order["status"] == "paid":
        return "", 200  # уже начислено — идемпотентно, повторный webhook не задвоит

    package = PACKAGES.get(order["package_id"])
    if package:
        get_or_create_balance(order["identity"])
        db.execute(
            "UPDATE balances SET paid_balance = paid_balance + ? WHERE identity = ?",
            (package["scans"], order["identity"]),
        )
    db.execute("UPDATE orders SET status = 'paid' WHERE order_id = ?", (order["order_id"],))
    db.commit()
    return "", 200


@app.get("/api/health")
def api_health():
    return jsonify({"ok": True})


init_db()

if __name__ == "__main__":
    # Для локального теста. В проде запускать через gunicorn (см. шапку файла).
    app.run(host="0.0.0.0", port=8000, debug=True)
