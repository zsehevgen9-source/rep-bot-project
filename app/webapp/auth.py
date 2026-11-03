"""Проверка данных запуска мини-приложения (initData) — по алгоритму MAX:
https://dev.max.ru/docs/webapps/validation

MAX подписывает initData токеном бота. Подделать подпись без токена нельзя, поэтому по проверенной
initData сервер точно знает, какой пользователь MAX открыл приложение.
"""
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional
from urllib.parse import unquote

MAX_AGE = 24 * 3600   # данные запуска старше суток не принимаем


def sign(fields: Dict[str, str], bot_token: str) -> str:
    """Подпись набора полей (без hash) — так её считает MAX."""
    launch_params = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, launch_params.encode(), hashlib.sha256).hexdigest()


def validate_init_data(init_data: str, bot_token: str, max_age: int = MAX_AGE,
                       now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Проверенные данные запуска ({"user": {...}, "auth_date": ..., ...}) или None, если подпись неверна."""
    if not init_data or not bot_token:
        return None
    if init_data.startswith("WebAppData="):          # на случай, если прислали весь фрагмент ссылки
        init_data = unquote(init_data[len("WebAppData="):])
    pairs = [p.partition("=") for p in init_data.split("&") if p]
    hashes = [v for k, _, v in pairs if k == "hash"]
    if len(hashes) != 1:
        return None
    fields: Dict[str, str] = {}
    for k, _, v in pairs:
        if k != "hash":
            fields[k] = unquote(v)
    if not hmac.compare_digest(sign(fields, bot_token), hashes[0]):
        return None
    try:
        auth_date = int(fields.get("auth_date") or 0)
    except ValueError:
        return None
    # auth_date может прийти в миллисекундах
    if auth_date > 10 ** 11:
        auth_date //= 1000
    if max_age and abs((now or time.time()) - auth_date) > max_age:
        return None
    data: Dict[str, Any] = dict(fields)
    for key in ("user", "chat"):
        if key in data:
            try:
                data[key] = json.loads(data[key])
            except ValueError:
                return None
    user = data.get("user")
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        return None
    return data
