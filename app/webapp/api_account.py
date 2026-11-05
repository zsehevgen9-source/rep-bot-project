"""API мини-приложения: знакомство (ученик / родитель / учитель), согласие, настройки, родители, класс учителя.

Приложение вызывает те же методы бота, что и чат. Чтобы ответы бота «себе» не дублировались сообщениями в чат,
обработчик получает QuietApi: сообщения самому пользователю он не отправляет, а запоминает (их текст можно
показать в приложении), а уведомления другим людям (ребёнку, родителю, учителю, администратору) уходят как обычно.
"""
from typing import Any, Dict, List, Optional

from app.handlers.tutor.common import GRADE_TTL, SUBJECTS, load_json, topic_progress
from app.handlers.tutor.interests import INTERESTS, MAX_INTERESTS
from app.handlers.tutor.privacy import CONSENT_SHORT, POLICY, POLICY_VERSION
from app.schemas.payloads import NormalizedMessage
from app.services import timezones


class QuietApi:
    """Обёртка над MAX API: сообщения пользователю, который сейчас в приложении, не отправляются в чат MAX,
    а копятся в out (текст и кнопки) — приложение показывает их у себя. Уведомления другим людям уходят как обычно.
    Фото, присланные из приложения, лежат в Redis (appimg_<id>) — бот читает их по адресу «appimg:<id>»."""

    def __init__(self, real, uid: int, chat_id: Optional[int] = None, redis=None):
        self.real, self.uid, self.chat_id, self.redis = real, uid, chat_id, redis
        self.notes: List[str] = []
        self.out: List[Dict[str, Any]] = []

    def _mine(self, chat_id=None, user_id=None) -> bool:
        return user_id == self.uid or (chat_id is not None and chat_id == self.chat_id)

    async def send_message(self, chat_id=None, user_id=None, text: str = "", keyboard=None, attachments=None, **kw):
        if self._mine(chat_id, user_id):
            if text:
                self.notes.append(text)
            images = [a["payload"].get("url") for a in attachments or []
                      if a.get("type") == "image" and isinstance(a.get("payload"), dict) and a["payload"].get("url")]
            self.out.append({"text": text or "", "buttons": (keyboard or {}).get("buttons") or [], "images": images})
            return {}
        return await self.real.send_message(chat_id=chat_id, user_id=user_id, text=text, keyboard=keyboard,
                                            attachments=attachments)

    async def send_voice(self, chat_id, audio_bytes):
        if self._mine(chat_id=chat_id):
            return {}
        return await self.real.send_voice(chat_id=chat_id, audio_bytes=audio_bytes)

    async def _app_image(self, url) -> Optional[bytes]:
        import base64
        raw = await self.redis.get_state(f"appimg_{url[len('appimg:'):]}") if self.redis else None
        return base64.b64decode(raw) if raw else None

    async def download_image(self, url, *args, **kwargs):
        if str(url).startswith("appimg:"):
            return await self._app_image(url)
        return await self.real.download_image(url, *args, **kwargs)

    async def download_file(self, url, *args, **kwargs):
        if str(url).startswith("appimg:"):
            return await self._app_image(url)
        return await self.real.download_file(url, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.real, name)


class AccountMixin:
    """Методы MiniAppAPI для знакомства и настроек (подмешиваются в MiniAppAPI)."""

    def _quiet(self, h, uid: int) -> NormalizedMessage:
        h.api = QuietApi(h.api, uid, redis=h.redis)
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
                "voice": await h._voice_mode(uid),
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
            if "voice" in data:
                code = {"auto": "1", "button": "b", "off": "0"}.get(data.get("voice"))
                if not code:
                    raise ApiError(400, "Неизвестный режим голоса")
                await h.redis.set_state(f"voice_on_{uid}", code)
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

    async def family_code(self, uid: int) -> Dict[str, Any]:
        """Код родителя для детей (7 дней). Ребёнок вводит его у себя — как код класса учителя."""
        async with self._handler() as h:
            return {"code": await h.family_code(uid)}

    async def role_switch(self, uid: int) -> Dict[str, Any]:
        """🔄 Сменить роль: учитель/родитель → снова «Кто ты?» (классы учителя сохраняются)."""
        async with self._handler() as h:
            await h.switch_role(uid)
            return {"ok": True, "env_teacher": h._env_teacher(uid)}

    async def family_cancel(self, uid: int) -> Dict[str, Any]:
        """«Я не родитель»: вернуться к выбору роли (только пока дети не подключены)."""
        async with self._handler() as h:
            return {"ok": await h.cancel_parent(uid)}

    async def join_family(self, uid: int, code: Any) -> Dict[str, Any]:
        """Ученик вводит код родителя. Родитель получает сообщение в чат."""
        from app.webapp.api import ApiError
        async with self._handler() as h:
            digits = "".join(ch for ch in str(code or "") if ch.isdigit())
            data = load_json(await h.redis.get_state(f"fcode_{digits}"), None) if len(digits) == 6 else None
            await h.join_family(self._quiet(h, uid), digits)
            parent = data.get("uid") if isinstance(data, dict) else None
            if not parent or not await h._is_parent_of(parent, uid):
                raise ApiError(400, self._last_note(h, "Код не подошёл"), "code")
        return await self.settings(uid)

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

    # ---------- 💬 чат с Маячком прямо в приложении ----------

    @staticmethod
    def _messages(h) -> List[Dict[str, Any]]:
        """Ответы бота для приложения: «🔊 Послушать» убираем (голос приходит только в чат MAX)."""
        result = []
        for m in h.api.out:
            rows = [[b for b in row if not str(b.get("payload") or "").startswith("say_")] for row in m["buttons"]]
            result.append({**m, "buttons": [r for r in rows if r]})
        return result

    async def chat_send(self, uid: int, text: Any = None, payload: Any = None) -> Dict[str, Any]:
        """Сообщение или нажатие кнопки в чате приложения — бот отвечает так же, как в чате MAX."""
        from app.webapp.api import ApiError
        text = str(text or "").strip()[:2000]
        payload = str(payload or "").strip()[:200]
        if not text and not payload:
            raise ApiError(400, "Пустое сообщение")
        async with self._handler() as h:
            message = self._quiet(h, uid)
            if payload:
                message.payload = payload
                await h.handle_callback(message)
            else:
                message.text = text
                await h.handle_message(message)
            return {"messages": self._messages(h)}

    async def chat_photo(self, uid: int, image: Any) -> Dict[str, Any]:
        """Фото из приложения (домашка, расписание, решение задания учителя) — боту, как будто прислано в чат."""
        import base64
        import secrets
        from app.webapp.api import ApiError
        data = str(image or "")
        if "," in data[:100]:
            data = data.split(",", 1)[1]                    # data:image/jpeg;base64,...
        try:
            raw = base64.b64decode(data, validate=True)
        except Exception:
            raise ApiError(400, "Не получилось прочитать фото")
        if not 1000 < len(raw) <= 6 * 1024 * 1024:
            raise ApiError(400, "Фото слишком маленькое или слишком большое")
        key = secrets.token_hex(8)
        async with self._handler() as h:
            encoded = base64.b64encode(raw).decode()
            # Фото домашки и расписания бот разбирает сразу и забывает (живёт час, как в политике конфиденциальности)
            await h.redis.set_state(f"appimg_{key}", encoded, ex=3600)
            pending = load_json(await h.redis.get_state(f"asg_photo_{uid}"), None)
            message = self._quiet(h, uid)
            message.file_url, message.file_kind, message.file_name = f"appimg:{key}", "image", "photo.jpg"
            await h.handle_message(message)
            if pending:
                # Фото решения задания учителя учитель смотрит позже — его храним 60 дней (как ссылку на фото в чате)
                data = await h._tclass(pending[0])
                task = next((a for a in (data or {}).get("assignments", []) if a["id"] == pending[1]), None)
                photo = ((task or {}).get("results", {}).get(str(uid)) or {}).get("photo") or {}
                if photo.get("url") == f"appimg:{key}":
                    await h.redis.set_state(f"appimg_{key}", encoded, ex=60 * 86400)
            return {"messages": self._messages(h)}

    async def chat_open(self, uid: int, action: Any, params: Any) -> Dict[str, Any]:
        """Действие из приложения (урок, разбор ошибки, домашка, задание дня, меню) — сразу в чате приложения."""
        from app.webapp.api import ApiError
        params = params if isinstance(params, dict) else {}
        async with self._handler() as h:
            grade = await self._grade(h, uid)
            message = self._quiet(h, uid)
            sid = params.get("subject_id")
            if action in ("lesson", "ask_topic") and sid not in {s for _, s in SUBJECTS[grade]}:
                raise ApiError(400, "Такого предмета нет в твоём классе")
            await h._reset_modes(uid)
            if action == "lesson" and str(params.get("topic") or "").strip():
                await h.start_lesson(message, grade, sid, str(params["topic"]).strip())
            elif action == "ask_topic":
                await h.ask_lesson_topic(message, sid)
            elif action == "fix" and isinstance(params.get("n"), int):
                state = await h._test_state(uid)
                if not state or not state.get("done") or not 0 <= params["n"] < len(state["answers"]):
                    raise ApiError(409, "Этот тест уже устарел.", "expired")
                await h.fix_test_question(message, params["n"])
            elif action == "homework":
                await h.ask_homework(message)
            elif action == "today":
                await h.send_daily(message)
            elif action == "menu":
                await h.show_subjects(message, grade)
            else:
                raise ApiError(400, "Неизвестное действие")
            return {"messages": self._messages(h)}

    # ---------- 🔊 голос Маячка и 🎤 голосовой ответ ученика ----------

    async def voice_say(self, uid: int, text: Any) -> Optional[bytes]:
        """Озвучка ответа Маячка для приложения (MP3). Тем же голосом и с тем же кешем, что в чате MAX."""
        from app.handlers.tutor.core import TTS_VOICE_KEY
        from app.services.audio import to_mp3
        from app.services.voice import prepare_for_speech
        from app.webapp.api import ApiError
        clean = prepare_for_speech(str(text or ""))[:1500]
        if len(clean) < 2:
            raise ApiError(400, "Нечего озвучивать")
        async with self._handler() as h:
            if await h._voice_mode(uid) == "off":
                raise ApiError(409, "Голос выключен в настройках", "voice_off")
            voice = load_json(await h.redis.get_state(TTS_VOICE_KEY), None)
            audio = await h._speech(clean, voice)
            mp3 = await to_mp3(audio) if audio else None
            if not mp3:
                raise ApiError(503, "Не получилось озвучить — попробуй ещё раз", "tts")
            return mp3

    async def voice_listen(self, uid: int, audio: Any) -> Dict[str, Any]:
        """Голосовой ответ из приложения: распознаём (Yandex SpeechKit) и отвечаем, как на голосовое в чате."""
        import base64
        import secrets
        from app.webapp.api import ApiError
        data = str(audio or "")
        if "," in data[:100]:
            data = data.split(",", 1)[1]
        try:
            raw = base64.b64decode(data, validate=True)
        except Exception:
            raise ApiError(400, "Не получилось прочитать запись")
        if not 500 < len(raw) <= 3 * 1024 * 1024:
            raise ApiError(400, "Запись слишком короткая или слишком длинная")
        key = secrets.token_hex(8)
        async with self._handler() as h:
            await h.redis.set_state(f"appimg_{key}", base64.b64encode(raw).decode(), ex=600)   # только на распознавание
            message = self._quiet(h, uid)
            message.audio_url = f"appimg:{key}"
            await h.handle_message(message)
            await h.redis.delete_state(f"appimg_{key}")
            return {"messages": self._messages(h)}

    async def privacy(self) -> Dict[str, Any]:
        from app.core.config import settings as cfg
        contact = (getattr(cfg, "PRIVACY_CONTACT", "") or "").strip()
        return {"text": POLICY.format(contact=f" Связаться с нами: {contact}." if contact else "")}

    async def delete_me(self, uid: int) -> Dict[str, Any]:
        async with self._handler() as h:
            await h.delete_user_data(self._quiet(h, uid))
        return {"ok": True}
