"""Голос, профиль ученика, состояние."""
import base64
import hashlib

from app.handlers.tutor.common import *  # noqa: F401,F403
from app.services import cards
from app.services.audio import to_ogg_opus
from app.services.task_cache import TaskCache
from app.services.voice import prepare_for_speech, speakable  # noqa: F401  (speakable — для старого импорта)

TTS_CACHE_TTL = 30 * 24 * 3600   # голосовое в кеше живёт 30 дней
TTS_CACHE_MAX = 200 * 1024        # длинные голосовые (уроки) не кешируем — они почти не повторяются
SAY_TTL = 2 * 24 * 3600          # кнопка «🔊 Послушать» работает два дня
VOICE_MODES = {"1": "auto", "b": "button", "0": "off"}   # своя настройка ученика в Redis (voice_on_<id>)
TTS_VOICE_KEY = "tts_voice"   # голос, выбранный администратором командой /voice (общий для всех учеников)


class CoreMixin:
    """Голос, профиль ученика, состояние."""

    async def _send_with_voice(
        self,
        message: NormalizedMessage,
        text: str,
        keyboard: Optional[dict] = None,
        voice_text: Optional[str] = None,
        attachments: Optional[list] = None,
        speak: bool = True,
    ):
        """Текст + голосовое. speak=False — экран-меню без озвучки (меню и так видно, а озвучка стоит денег)."""
        # Озвучка по кнопке: голосовое делается (и оплачивается) только когда ученик нажмёт «🔊 Послушать»
        if speak and message.chat_id and await self._voice_mode(message.user_id) == "button":
            spoken = prepare_for_speech(voice_text if voice_text is not None else text)
            if len(spoken) >= 2:
                key = hashlib.sha1(spoken.encode()).hexdigest()[:12]
                await self.redis.set_state(f"say_{key}", spoken, ex=SAY_TTL)
                rows = list((keyboard or {}).get("buttons") or [])
                keyboard = _kb(rows + [[_btn("🔊 Послушать", f"say_{key}")]])
        # 1. Мгновенно отправляем текст (и картинку, если есть)
        await self.answer(message, text, keyboard=keyboard, attachments=attachments)
        await self._send_certificates(message)

        # 2. Голос: сразу, по кнопке «🔊 Послушать» или никак — по настройке ученика (нужен chat_id)
        if not speak or not message.chat_id:
            return
        mode = await self._voice_mode(message.user_id)
        if mode == "off":
            return
        try:
            # Текст для голоса: без эмодзи и разметки, формулы словами, паузы на месте переносов строк
            clean_text = prepare_for_speech(voice_text if voice_text is not None else text)
            if len(clean_text) < 2:
                return
            if mode == "button":
                return   # кнопку «🔊 Послушать» добавили к сообщению выше
            voice = load_json(await self.redis.get_state(TTS_VOICE_KEY), None)
            audio_data = await self._speech(clean_text, voice)
            if audio_data:
                await self.api.send_voice(chat_id=message.chat_id, audio_bytes=audio_data)
            else:
                logger.error("Голосовое не получилось: SpeechKit не вернул звук")
        except Exception as e:
            logger.error(f"Общая ошибка озвучки: {e}")

    async def _quiet(self, message: NormalizedMessage, text: str, **kwargs):
        """Экран без озвучки (меню, настройки, прогресс)."""
        kwargs.pop("voice_text", None)
        await self._send_with_voice(message, text, speak=False, **kwargs)

    # ---------- голос: включён ли и кеш ----------

    async def _voice_mode(self, uid: int) -> str:
        """Как озвучивать этому ученику: "auto" — сразу голосовым, "button" — по кнопке «🔊 Послушать», "off" — никак.
        Своя настройка ученика («⚙️ Настройки → Голос») важнее VOICE_MODE из .env:
        all — всем сразу, young — 1–4 классам сразу (остальным никак), button — всем по кнопке, off — никому."""
        own = await self.redis.get_state(f"voice_on_{uid}")
        if own in VOICE_MODES:
            return VOICE_MODES[own]
        mode = (getattr(settings, "VOICE_MODE", "young") or "young").strip().lower()
        if mode == "all":
            return "auto"
        if mode in ("off", "button"):
            return mode
        grade = await self._get_grade(uid)
        return "auto" if grade is None or grade <= 4 else "off"

    async def _voice_enabled(self, uid: int) -> bool:
        return await self._voice_mode(uid) != "off"

    async def say_again(self, message: NormalizedMessage, key: str) -> None:
        """Кнопка «🔊 Послушать»: озвучить сообщение, к которому она прикреплена."""
        text = await self.redis.get_state(f"say_{key}") if re.fullmatch(r"[0-9a-f]{12}", key) else None
        if not text or not message.chat_id:
            await self.answer(message, "Это сообщение уже нельзя послушать — оно устарело 🙈")
            return
        await self._typing(message)
        voice = load_json(await self.redis.get_state(TTS_VOICE_KEY), None)
        audio = await self._speech(text, voice)
        if audio:
            await self.api.send_voice(chat_id=message.chat_id, audio_bytes=audio)
        else:
            await self.answer(message, "Не получилось озвучить — попробуй ещё раз чуть позже 🙈")

    async def _speech(self, text: str, voice: Optional[dict]) -> bytes:
        """Озвучка с кешем: одинаковые фразы («Выбери предмет», похвала, вопросы из банка) озвучиваются один раз,
        дальше голосовое берётся из Redis бесплатно."""
        use_cache = getattr(settings, "TTS_CACHE", True)
        key = "tts:" + hashlib.sha1(json.dumps([voice, text], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if use_cache:
            cached = await self.redis.get_state(key)
            if cached:
                try:
                    ai_client.count_usage("tts_cached")
                    return base64.b64decode(cached)
                except ValueError:
                    pass
        audio = await ai_client.synthesize_speech(text, voice=voice)
        if audio and use_cache and len(audio) <= TTS_CACHE_MAX:
            await self.redis.set_state(key, base64.b64encode(audio).decode(), ex=TTS_CACHE_TTL)
        return audio

    # ---------- лимит запросов к ИИ ----------

    async def _ai_spent_today(self, uid: Optional[int] = None) -> int:
        day = topic_progress.now().date().isoformat()
        key = f"ai_usage_{uid}_{day}" if uid else f"ai_usage_total_{day}"
        return int(await self.redis.get_state(key) or 0)

    async def _ai_spend(self, uid: int) -> bool:
        """Учитывает одно «тяжёлое» действие ИИ. False — лимит на сегодня исчерпан (ничего не учтено)."""
        limit = int(getattr(settings, "AI_DAILY_LIMIT", 0) or 0)
        spent = await self._ai_spent_today(uid)
        if limit and spent >= limit and not self._is_admin(uid):
            logger.info("Ученик %s исчерпал дневной лимит ИИ (%d)", uid, limit)
            return False
        day = topic_progress.now().date().isoformat()
        await self.redis.set_state(f"ai_usage_{uid}_{day}", str(spent + 1), ex=2 * DAY)
        total = await self._ai_spent_today()
        await self.redis.set_state(f"ai_usage_total_{day}", str(total + 1), ex=8 * DAY)
        return True

    async def _ai_limit_reached(self, message: NormalizedMessage) -> None:
        grade = await self._get_grade(message.user_id)
        await self._send_with_voice(
            message,
            "На сегодня новых заданий от меня хватит 🙂 Ты отлично потрудился! Можно повторить пройденное — "
            "или продолжим завтра со свежими силами.",
            keyboard=_kb([[_btn("🔁 Повторение", "plan_regular")], [_back(f"subjects_{grade}" if grade else "menu")]]),
        )

    async def _send_certificates(self, message: NormalizedMessage) -> None:
        """Грамоты за новый уровень и достижения (их копит _reward) — картинками."""
        await self._send_lighthouse_news(message)
        raw = await self.redis.get_state(f"cert_{message.user_id}")
        if not raw:
            return
        await self.redis.delete_state(f"cert_{message.user_id}")
        name = await self.redis.get_state(f"name_{message.user_id}") or "Ученик"
        for title, subtitle in load_json(raw, []):
            title = re.sub(r"^[^\w«]+", "", title)   # эмодзи в начале названия достижения картинка не нарисует
            await self._send_card(message, cards.certificate_card, name, title, subtitle)

    async def _send_card(self, message: NormalizedMessage, render, *args, text: str = "",
                         keyboard: Optional[dict] = None, to_user: Optional[int] = None, **kwargs) -> bool:
        """Картинка-карточка: render(*args, **kwargs) → PNG, загрузка в MAX, отправка.
        False — карточки выключены или что-то не вышло (тогда хватает обычного текста)."""
        if not getattr(settings, "IMAGE_CARDS", True) or not cards.available():
            return False
        try:
            png = await asyncio.to_thread(render, *args, **kwargs)
            token = await self.api.upload_image(png, filename="card.png")
            if not token:
                return False
            await self.api.send_message(user_id=to_user or message.user_id, text=text, keyboard=keyboard,
                                        attachments=[{"type": "image", "payload": {"token": token}}])
            return True
        except Exception as e:
            logger.warning("Не удалось отправить карточку: %s", e)
            return False

    async def _typing(self, message: NormalizedMessage) -> None:
        """Показывает в чате «печатает…», пока бот думает (если MAX это поддерживает)."""
        send_action = getattr(self.api, "send_action", None)
        if message.chat_id and send_action:
            try:
                await send_action(message.chat_id, "typing_on")
            except Exception:
                pass

    async def _voice_to_text(self, message: NormalizedMessage) -> Optional[str]:
        """Голосовое сообщение -> текст. Если не вышло — сам отвечает ученику и возвращает None."""
        audio = await self.api.download_file(message.audio_url)
        logger.info("Голосовое: скачано %s байт, начало %r", len(audio) if audio else 0, audio[:8] if audio else b"")
        if audio:
            audio = await to_ogg_opus(audio)
        text = (await ai_client.recognize_speech(audio)).strip() if audio else ""
        logger.info("Голосовое: распознано %d символов", len(text))
        if not text:
            await self.answer(
                message,
                "Ой, я не расслышал 🙈 Напиши, пожалуйста, ответ сообщением.",
            )
            return None
        # Показываем, что услышали: ребёнок (или родитель) сразу видит, если бот ослышался
        await self.answer(message, f"🎤 Я услышал: «{text}»")
        return text

    @property
    def bank(self) -> TaskCache:
        """Свой банк проверенных заданий (см. app/services/task_cache.py)."""
        return TaskCache(self.redis)

    @property
    def profiles(self) -> ProfileStore:
        return ProfileStore(self.db)

    async def _load_profile(self, user_id: int) -> Dict[str, Any]:
        """Профиль из PostgreSQL; если база недоступна — копия из Redis."""
        profile = await self.profiles.get(user_id)
        if profile is None:
            profile = load_json(await self.redis.get_state(f"profile_{user_id}"), {})
        return profile or {}

    async def _save_profile(self, user_id: int, **fields: Any) -> None:
        cached = load_json(await self.redis.get_state(f"profile_{user_id}"), {})
        cached.update(fields)
        # default=str: даты (например, время последней активности) сохраняются строкой
        await self.redis.set_state(f"profile_{user_id}", json.dumps(cached, ensure_ascii=False, default=str), ex=GRADE_TTL)
        await self.profiles.save(user_id, **fields)

    async def _load_progress(self, user_id: int) -> Dict[str, Any]:
        return load_json((await self._load_profile(user_id)).get("topic_progress"), {})

    async def _save_progress(self, user_id: int, progress: Dict[str, Any]) -> None:
        await self._save_profile(user_id, topic_progress=json.dumps(progress, ensure_ascii=False))

    async def _get_grade(self, user_id: int) -> Optional[int]:
        value = await self.redis.get_state(f"grade_{user_id}")
        try:
            grade = int(value) if value else None
        except ValueError:
            grade = None
        if grade is None:
            # Redis мог очиститься — берём класс из профиля
            grade = (await self._load_profile(user_id)).get("grade")
            if grade in SUBJECTS:
                await self.redis.set_state(f"grade_{user_id}", str(grade), ex=GRADE_TTL)
        return grade if grade in SUBJECTS else None

    async def _context(self, user_id: int) -> Dict[str, Any]:
        """Что ИИ знает об ученике: имя, класс, текущий предмет."""
        return {
            "name": await self.redis.get_state(f"name_{user_id}"),
            "grade": await self._get_grade(user_id),
            "subject": await self.redis.get_state(f"current_subj_{user_id}"),
        }

    async def _reset_modes(self, user_id: int) -> None:
        """Любое нажатие кнопки сбрасывает «режимы ожидания», чтобы старое состояние не мешало."""
        if await self.redis.get_state(f"renaming_{user_id}") == "true":
            # Начал менять имя и передумал — следующее сообщение уже не имя
            await self.redis.delete_state(f"awaiting_name_{user_id}")
        for key in ("awaiting_topic", "awaiting_exam_subject", "task_active", "diag_mode", "renaming", "stest_mode", "awaiting_tz",
                    "awaiting_schedule", "awaiting_lesson_topic", "awaiting_textbook",
                    "awaiting_homework", "awaiting_parent_code", "awaiting_parent_sched", "awaiting_family_code", "awaiting_goal_prize", "awaiting_tann",
                    "awaiting_tclass", "awaiting_tasg", "awaiting_join", "awaiting_tname", "awaiting_tlink", "awaiting_fc_topic"):
            await self.redis.delete_state(f"{key}_{user_id}")
