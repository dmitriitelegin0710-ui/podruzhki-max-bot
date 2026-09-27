"""
Собственная база фото под rubrics.json — теперь хранится в бакете
Yandex Object Storage (liftdocs-files), а не в самом репозитории.

Идея: в бакете есть папка photo_bank_max_bot/, а внутри неё — по одной
папке на каждую рубрику, названной точно так же, как "key" рубрики в
rubrics.json (utro_privet, krasota, ezoterika и т.д.). Внутри каждой
папки лежат несколько десятков фото, вручную подобранных под тему —
никакого отдельного файла-реестра (photo_bank.json) больше не нужно,
список файлов просто читается из самого бакета через S3 API.

Yandex Object Storage полностью совместим с Amazon S3, поэтому доступ
идёт через стандартную библиотеку boto3 с эндпоинтом Яндекса. Ссылка на
конкретное фото каждый раз подписывается (presigned URL) — так работает
независимо от того, публичный бакет или приватный, и не зависит от
настроек ACL, которые могут случайно измениться.

Это ДОПОЛНЕНИЕ, а не замена Pexels: если в бакете для рубрики нет папки
или она пустая (или сам бакет недоступен по какой-то причине) —
get_own_photo() просто возвращает None, и rubric_post_to_max.py как и
раньше идёт дальше в Pexels. Ничего не ломается.

--- Ротация, чтобы не повторять одно и то же фото ---
photo_bank_state.json (создаётся и обновляется автоматически, руками его
трогать не нужно) хранит для каждой рубрики список недавно использованных
файлов. Пока в пуле есть неиспользованные недавно варианты — берётся один
из них; когда все использованы — ограничение снимается и выбор идёт
заново по всему пулу.
ВАЖНО: чтобы эта ротация реально работала между запусками (а не сбрасывалась
каждый раз, потому что GitHub Actions runner — одноразовый), rubric_post.yml
должен коммитить photo_bank_state.json обратно в репозиторий после каждого
запуска — так же, как это уже сделано для posted_rubrics.json/posted_news.json.

Требуемые GitHub Secrets:
  YC_ACCESS_KEY_ID, YC_SECRET_ACCESS_KEY — статический ключ сервисного
    аккаунта с ролью storage.viewer на бакет liftdocs-files.
Требуемая зависимость: boto3 (добавьте в requirements.txt).
"""
import json
import os
import random

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError, BotoCoreError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False

PHOTO_BANK_STATE_FILE = "photo_bank_state.json"

YC_BUCKET_NAME = os.environ.get("YC_BUCKET_NAME", "liftdocs-files")
YC_BUCKET_PREFIX = os.environ.get("YC_BUCKET_PREFIX", "photo_bank_max_bot")
YC_ENDPOINT_URL = "https://storage.yandexcloud.net"
YC_REGION = "ru-central1"

# Сколько секунд действует подписанная ссылка на фото. Она используется
# сразу же (upload_media_and_get_token скачивает файл в этом же запуске),
# поэтому 5 минут с большим запасом хватает.
PRESIGNED_URL_TTL_SECONDS = 300

# Расширения, которые считаем фотографиями — на случай, если в папке
# случайно окажется не-картинка (например, .DS_Store на Mac).
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_s3_client():
    access_key = os.environ.get("YC_ACCESS_KEY_ID")
    secret_key = os.environ.get("YC_SECRET_ACCESS_KEY")
    if not access_key or not secret_key:
        print("Своя база фото: YC_ACCESS_KEY_ID/YC_SECRET_ACCESS_KEY не заданы, пропускаю")
        return None

    return boto3.client(
        "s3",
        endpoint_url=YC_ENDPOINT_URL,
        region_name=YC_REGION,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
    )


def _list_photo_keys(s3_client, rubric_key: str) -> list:
    """Возвращает список ключей (путей внутри бакета) всех фото в папке
    рубрики. Папка считается пустой, если в ней 0 файлов с расширением
    из IMAGE_EXTENSIONS (сама "папка" в S3 — это просто общий префикс,
    отдельного объекта-папки может не быть, а может быть пустой ключ,
    оканчивающийся на "/" — он тоже отфильтровывается)."""
    prefix = f"{YC_BUCKET_PREFIX}/{rubric_key}/"
    keys = []
    continuation_token = None

    while True:
        kwargs = {"Bucket": YC_BUCKET_NAME, "Prefix": prefix}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token

        response = s3_client.list_objects_v2(**kwargs)
        for obj in response.get("Contents", []):
            key = obj["Key"]
            if key.lower().endswith(IMAGE_EXTENSIONS):
                keys.append(key)

        if response.get("IsTruncated"):
            continuation_token = response.get("NextContinuationToken")
        else:
            break

    return keys


def get_own_photo(rubric_key: str, weekday_index: int = None):
    """Возвращает подписанную (presigned) ссылку на фото из бакета для
    этой рубрики, стараясь не повторять недавно использованные фото.
    weekday_index сейчас не используется — все фото рубрики лежат в одной
    общей папке без разбивки по дням недели (параметр оставлен только
    ради совместимости вызова в rubric_post_to_max.py).
    Возвращает None при любой проблеме (нет ключей, бакет недоступен,
    в папке рубрики нет фото и т.п.) — тогда caller идёт дальше в Pexels,
    как и раньше."""
    if not BOTO3_AVAILABLE:
        print("Своя база фото: boto3 не установлен, пропускаю (добавьте boto3 в requirements.txt)")
        return None

    s3_client = _get_s3_client()
    if s3_client is None:
        return None

    try:
        candidates = _list_photo_keys(s3_client, rubric_key)
    except (ClientError, BotoCoreError) as e:
        print(f"Своя база фото: ошибка обращения к бакету — {e}")
        return None

    if not candidates:
        return None

    state = _load_json(PHOTO_BANK_STATE_FILE, {})
    used_recently = state.get(rubric_key, [])

    available = [c for c in candidates if c not in used_recently] or candidates
    chosen = random.choice(available)

    # "Не повторять последние N", где N — примерно половина пула, чтобы
    # фото реально успевало "отдохнуть" перед повтором.
    window = max(1, len(candidates) // 2)
    state[rubric_key] = (used_recently + [chosen])[-window:]
    _save_json(PHOTO_BANK_STATE_FILE, state)

    try:
        return s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": YC_BUCKET_NAME, "Key": chosen},
            ExpiresIn=PRESIGNED_URL_TTL_SECONDS,
        )
    except (ClientError, BotoCoreError) as e:
        print(f"Своя база фото: не удалось подписать ссылку на {chosen} — {e}")
        return None
