"""Проверка данных запуска мини-приложения (initData) — по алгоритму MAX:
https://dev.max.ru/docs/webapps/validation

MAX подписывает initData токеном бота. Подделать подпись без токена нельзя, поэтому по проверенной
initData сервер точно знает, какой пользователь MAX открыл приложение.

Разные клиенты MAX (Android, iPhone, компьютер, веб) присылают одни и те же данные немного по-разному:
значения закодированы в URL-виде или нет, пробел как «+» или «%20», вся строка закодирована ещё раз.
Поэтому строку разбираем несколькими способами и принимаем, если подпись сошлась хотя бы для одного.
Это не ослабляет защиту: без токена бота подходящую подпись не получить ни при каком разборе.
Если не сошлось — пишем в журнал причину (без самих данных пользователя), чтобы было видно, что не так.
"""
import hashlib
import hmac
import json
import logging
import re
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import unquote, unquote_plus

logger = logging.getLogger(__name__)

MAX_AGE = 24 * 3600   # данные запуска старше суток не принимаем

# Поля, которые MAX кладёт в initData. По ним делим строку, если значения пришли незакодированными
# (тогда внутри JSON, например в ссылке на аватар, может встретиться «&»).
KNOWN_KEYS = ("auth_date", "chat", "hash", "ip", "query_id", "start_param", "user", "receiver",
              "chat_type", "chat_instance", "can_send_after", "signature")
_KNOWN_SPLIT = re.compile(r"&(?=(?:%s)=)" % "|".join(KNOWN_KEYS))


def sign(fields: Dict[str, str], bot_token: str) -> str:
    """Подпись набора полей (без hash) — так её считает MAX."""
    launch_params = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, launch_params.encode(), hashlib.sha256).hexdigest()


def _strip_prefix(init_data: str) -> str:
    init_data = init_data.strip().lstrip("#?")
    for prefix in ("WebAppData=", "tgWebAppData="):
        if init_data.startswith(prefix):
            init_data = unquote(init_data[len(prefix):])   # прислали весь фрагмент ссылки
            break
    return init_data


def _sources(init_data: str) -> List[str]:
    """Сама строка и, если она целиком закодирована ещё раз, её раскодированная версия."""
    out = [init_data]
    if "%3D" in init_data.upper() and ("&" not in init_data or "%26" in init_data.upper()):
        decoded = unquote(init_data)
        if decoded != init_data:
            out.append(decoded)
    return out


def _variants(init_data: str) -> Iterator[Tuple[str, List[Tuple[str, str]]]]:
    """Способы разбить строку на пары (ключ, значение) и раскодировать значения."""
    decoders = (("url", unquote), ("url+", unquote_plus), ("raw", lambda v: v))
    for source in _sources(init_data):
        splits = {"&": source.split("&")}
        known = _KNOWN_SPLIT.split(source)
        if known != splits["&"]:
            splits["known"] = known
        for split_name, parts in splits.items():
            raw_pairs = [p.partition("=") for p in parts if p]
            for dec_name, dec in decoders:
                yield f"{split_name}/{dec_name}", [(k, dec(v)) for k, _, v in raw_pairs]


def _check(pairs: List[Tuple[str, str]], bot_token: str) -> Optional[Dict[str, str]]:
    hashes = [v for k, v in pairs if k == "hash"]
    if len(hashes) != 1:
        return None
    fields = {k: v for k, v in pairs if k != "hash"}
    if len(fields) != len([k for k, _ in pairs if k != "hash"]):
        return None                                   # одинаковые ключи — не наш формат
    if hmac.compare_digest(sign(fields, bot_token), hashes[0].strip().lower()):
        return fields
    return None


def _describe(init_data: str) -> str:
    """Что пришло — без значений: только имена полей и форма записи (для журнала)."""
    keys = sorted({p.partition("=")[0] for p in _KNOWN_SPLIT.split(init_data) if p})[:15]
    return (f"длина={len(init_data)}, поля={keys}, "
            f"user_закодирован={'user=%7B' in init_data or 'user%3D%257B' in init_data}, "
            f"есть_плюс={'+' in init_data}")


def validate_init_data(init_data: str, bot_token: str, max_age: int = MAX_AGE,
                       now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Проверенные данные запуска ({"user": {...}, "auth_date": ..., ...}) или None, если подпись неверна."""
    if not init_data or not bot_token:
        return None
    init_data = _strip_prefix(init_data)
    fields: Optional[Dict[str, str]] = None
    tried = 0
    for _name, pairs in _variants(init_data):
        tried += 1
        fields = _check(pairs, bot_token)
        if fields is not None:
            break
    if fields is None:
        logger.warning("Мини-приложение: подпись MAX не сошлась (способов разбора: %s). %s", tried, _describe(init_data))
        return None
    try:
        auth_date = int(float(fields.get("auth_date") or 0))
    except ValueError:
        logger.warning("Мини-приложение: непонятная дата запуска. %s", _describe(init_data))
        return None
    # auth_date может прийти в миллисекундах
    if auth_date > 10 ** 11:
        auth_date //= 1000
    if max_age and abs((now or time.time()) - auth_date) > max_age:
        logger.warning("Мини-приложение: данные запуска устарели (разница %s ч). Пусть закроет приложение "
                       "и откроет заново.", round(abs((now or time.time()) - auth_date) / 3600, 1))
        return None
    data: Dict[str, Any] = dict(fields)
    for key in ("user", "chat"):
        if key in data:
            data[key] = _load_json(data[key])
            if data[key] is None:
                logger.warning("Мини-приложение: поле %s не читается как JSON. %s", key, _describe(init_data))
                return None
    user = data.get("user")
    if isinstance(user, dict) and isinstance(user.get("id"), str) and user["id"].isdigit():
        user["id"] = int(user["id"])
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        logger.warning("Мини-приложение: в данных нет номера пользователя. %s", _describe(init_data))
        return None
    return data


def _load_json(value: str) -> Any:
    """JSON поля user/chat; если клиент прислал его ещё раз закодированным — раскодируем."""
    for candidate in (value, unquote(value), unquote_plus(value)):
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return None
