"""API мини-приложения: знакомство (ученик / родитель / учитель), согласие, настройки, родители, класс учителя.

Приложение вызывает те же методы бота, что и чат. Чтобы ответы бота «себе» не дублировались сообщениями в чат,
обработчик получает QuietApi: сообщения самому пользователю он не отправляет, а запоминает (их текст можно
показать в приложении), а уведомления другим людям (ребёнку, родителю, учителю, администратору) уходят как обычно.
"""
from typing import Any, Dict, List, Optional

from app.handlers.tutor.common import GRADE_TTL, SUBJECTS, topic_progress
from app.handlers.tutor.interests import INTERESTS, MAX_INTERESTS
from app.handlers.tutor.privacy import CONSENT_SHORT, POLICY, POLICY_VERSION
from app.schemas.payloads import NormalizedMessage
from app.services import timezones


class QuietApi:
    """Обёртка над MAX API: сообщения пользователю, который сейчас в приложении, не отправляются, а копятся."""

    def __init__(self, real, uid: int, chat_id: Optional[int] = None):
        self.real, self.uid, self.chat_id = real, uid, chat_id
        self.notes: List[str] = []

    def _mine(self, chat_id=None, user_id=None) -> bool:
        return user_id == self.uid or (chat_id is not None and chat_id == self.chat_id)

    async def send_message(self, chat_id=None, user_id=None, text: str = "", keyboard=None, attachments=None, **kw):
        if self._mine(chat_id, user_id):
            if text:
                self.notes.append(text)
            return {}
        return await self.real.send_message(chat_id=chat_id, user_id=user_id, text=text, keyboard=keyboard,
                                            attachments=attachments)

    async def send_voice(self, chat_id, audio_bytes):
        if self._mine(chat_id=chat_id):
            return {}
        return await self.real.send_voice(chat_id=chat_id, audio_bytes=audio_bytes)

    async def upload_image(self, *args, **kwargs):
        return None     # картинки-карточки для чата приложению не нужны

    def __getattr__(self, name):
        return getattr(self.real, name)


class AccountMixin:
    """Методы MiniAppAPI для знакомства и настроек (подмешиваются в MiniAppAPI)."""

    def _quiet(self, h, uid: int) -> NormalizedMessage:
        h.api = QuietApi(h.api, uid)
        return NormalizedMessage(chat_id=None, user_id=uid, message_id="", text=None, payload=None)

    @staticmethod
    def _last_note(h, default: str = "") -> str:
        notes = getattr(h.api, "notes", [])
        return notes[-1] if notes else default

    # ---------- знакомство ----------

    async def onboarding_info(self) -> Dict[str, Any]:
        return {"consent_text": CONSENT_SHORT, "grades": list(range(1, 12)), "policy_version": POLICY_VERSION}

    async def onboard_student(self, uid: int, name: Any, grade: Any, tz_offset: Any, consent: Any) -> Dict[str, Any]:
        from app.webapp.api import ApiError
        name = " ".join(str(name or "").split())[:64]
        if not name:
            raise ApiError(400, "Напиши, как тебя зовут")
        if not isinstance(grade, int) or grade not in SUBJECTS:
            raise ApiError(400, "Выбери класс")
        async with self._handler() as h:
            if h._consent_required() and not await h.has_consent(uid):
                if consent is not True:
                    raise ApiError(400, "Нужно согласие на обработку данных", "consent")
                stamp = f"{topic_progress.now().isoformat(timespec='seconds')} v{POLICY_VERSION} app"
                await h.redis.set_state(f"consent_{uid}", stamp)
                await h._save_profile(uid, consent_at=stamp)
            await h.redis.set_state(f"name_{uid}", name, ex=GRADE_TTL)
            await h.redis.set_state(f"grade_{uid}", str(grade), ex=GRADE_TTL)
            await h.redis.delete_state(f"awaiting_name_{uid}")
            fields: Dict[str, Any] = {"name": name, "grade": grade}
            profile = await h._load_profile(uid)
            if profile.get("tz_offset") is None and isinstance(tz_offset, int) and -720 <= tz_offset <= 840:
                fields["tz_offset"] = tz_offset          # часовой пояс телефона — спрашивать город не нужно
            await h._save_profile(uid, **fields)
            return {"ok": True}

    async def link_parent(self, uid: int, code: Any) -> Dict[str, Any]:
        from app.webapp.api import ApiError
        async with self._handler() as h:
            message = self._quiet(h, uid)
            digits = "".join(ch for ch in str(code or "") if ch.isdigit())
            child = await h.redis.get_state(f"pcode_{digits}") if len(digits) == 6 else None
            await h.link_parent(message, digits)
            children = await h._children_of(uid)
            if not child or int(child) not in children:
                raise ApiError(400, self._last_note(h, "Код не подошёл"), "code")
            return {"ok": True, "children": children}

    async def teacher_request(self, uid: int) -> Dict[str, Any]:
        async with self._handler() as h:
            message = self._quiet(h, uid)
            await h.choose_teacher(message)
            return {"teacher": await h._is_teacher(uid, explicit=True),
                    "note": next((n for n in h.api.notes if "запрос" in n.lower() or "кабинет" in n.lower()), "")}

    # ---------- настройки ----------

    async def settings(self, uid: int) -> Dict[str, Any]:
        async with self._handler() as h:
            profile = await h._load_profile(uid)
            grade = await h._get_grade(uid)
            code = await h.redis.get_state(f"my_tclass_{uid}")
            tclass = await h._tclass(code) if code else None
            teachers = []
            if tclass and uid in tclass["students"]:
                teachers = [await h._teacher_name(int(t)) for t in tclass["teachers"]]
            parents = await h._parents_of(uid)
            return {
                "name": profile.get("name") or await h.redis.get_state(f"name_{uid}") or "",
                "grade": grade,
                "timezone": timezones.zone_name(profile["tz_offset"]) if profile.get("tz_offset") is not None else None,
                "interests": await h._interests(uid),
                "interest_options": [{"id": k, "title": v[0]} for k, v in INTERESTS.items()],
                "max_interests": MAX_INTERESTS,
                "parents": [{"id": p["user_id"], "n": n, "since": p.get("since")} for n, p in enumerate(parents, 1)],
                "tclass": {"name": tclass["name"], "teachers": teachers} if teachers or (tclass and uid in tclass["students"]) else None,
                "children": [{"id": c, "name": await h._child_name(c)} for c in await h._children_of(uid)],
            }

    async def save_settings(self, uid: int, data: Dict[str, Any]) -> Dict[str, Any]:
        from app.webapp.api import ApiError
        async with self._handler() as h:
            if "name" in data:
                name = " ".join(str(data.get("name") or "").split())[:64]
                if not name:
                    raise ApiError(400, "Имя не может быть пустым")
                await h.redis.set_state(f"name_{uid}", name, ex=GRADE_TTL)
                await h._save_profile(uid, name=name)
            if "grade" in data:
                grade = data.get("grade")
                if not isinstance(grade, int) or grade not in SUBJECTS:
                    raise ApiError(400, "Нет такого класса")
                await h.redis.set_state(f"grade_{uid}", str(grade), ex=GRADE_TTL)
                await h._save_profile(uid, grade=grade)
            if "interests" in data:
                chosen = data.get("interests")
                if not isinstance(chosen, list) or not all(k in INTERESTS for k in chosen):
                    raise ApiError(400, "Неизвестное увлечение")
                import json
                await h.redis.set_state(f"interests_{uid}", json.dumps(list(dict.fromkeys(chosen))[:MAX_INTERESTS]))
        return await self.settings(uid)

    async def parent_code(self, uid: int) -> Dict[str, Any]:
        async with self._handler() as h:
            await h.show_parent_code(self._quiet(h, uid))       # тот же код, что в чате (действует сутки)
            return {"code": await h.redis.get_state(f"pcode_of_{uid}")}

    async def unlink_parent(self, uid: int, parent_id: Any) -> Dict[str, Any]:
        async with self._handler() as h:
            if isinstance(parent_id, int):
                await h.unlink_parent(self._quiet(h, uid), parent_id)
        return await self.settings(uid)

    async def join_class(self, uid: int, code: Any) -> Dict[str, Any]:
        from app.webapp.api import ApiError
        async with self._handler() as h:
            message = self._quiet(h, uid)
            await h.join_class(message, str(code or ""))
            joined = await h.redis.get_state(f"my_tclass_{uid}")
            clean = "".join(ch for ch in str(code or "").upper() if ch.isalnum())
            if joined != clean:
                raise ApiError(400, self._last_note(h, "Код класса не подошёл"), "code")
        return await self.settings(uid)

    async def leave_class(self, uid: int) -> Dict[str, Any]:
        async with self._handler() as h:
            await h.leave_class(self._quiet(h, uid))
        return await self.settings(uid)

    async def leave_child(self, uid: int, child: Any) -> Dict[str, Any]:
        async with self._handler() as h:
            if isinstance(child, int):
                await h.leave_child(self._quiet(h, uid), child)
            return {"children": await h._children_of(uid)}

    async def privacy(self) -> Dict[str, Any]:
        from app.core.config import settings as cfg
        contact = (getattr(cfg, "PRIVACY_CONTACT", "") or "").strip()
        return {"text": POLICY.format(contact=f" Связаться с нами: {contact}." if contact else "")}

    async def delete_me(self, uid: int) -> Dict[str, Any]:
        async with self._handler() as h:
            await h.delete_user_data(self._quiet(h, uid))
        return {"ok": True}
