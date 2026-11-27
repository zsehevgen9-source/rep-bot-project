"""Режим «только приложение»: кнопки уведомлений в чате MAX ведут в мини-приложение."""
from typing import Any, Dict, Optional

APP_BUTTON = "📱 Открыть Маячок"


def app_keyboard(keyboard: Optional[Dict[str, Any]], app_url: str) -> Optional[Dict[str, Any]]:
    """Режим «только приложение»: кнопки чата не работают — оставляем ссылки и добавляем «Открыть Маячок»."""
    links = [b for row in (keyboard or {}).get("buttons") or [] for b in row if b.get("type") == "link"]
    if not any(b.get("url") == app_url for b in links):
        links = [{"type": "link", "text": APP_BUTTON, "url": app_url}] + links
    return {"buttons": [[b] for b in links[:3]]}
