"""Выбор голоса бота на слух: /voices — образцы всех голосов, /voice <номер> — выбрать (только администратор)."""
from app.handlers.tutor.common import *  # noqa: F401,F403
from app.handlers.tutor.core import TTS_VOICE_KEY
from app.services.voice import VOICE_CHOICES, prepare_for_speech

SAMPLE = ("Привет! Я Маячок, твой репетитор. Давай решим задачу вместе: поезд проехал двести сорок километров за три часа. "
          "С какой скоростью он ехал? Подумай, а потом напиши ответ. У тебя получится!")


class VoiceAdminMixin:
    async def show_voices(self, message: NormalizedMessage) -> None:
        current = load_json(await self.redis.get_state(TTS_VOICE_KEY), None)
        current_name = (current or {}).get("voice") or getattr(settings, "TTS_VOICE", "")
        await self.answer(
            message,
            "🎙 Послушай голоса — сейчас пришлю образец каждого.\n"
            f"Сейчас бот говорит голосом: {current_name or 'из настроек .env'}.\n"
            "Чтобы выбрать, напиши /voice и номер, например: /voice 2\n"
            "Вернуть голос из настроек .env: /voice 0",
        )
        sample = prepare_for_speech(SAMPLE)
        for n, (model, voice, role, title) in enumerate(VOICE_CHOICES, 1):
            await self.answer(message, f"{n}. {title}")
            if not message.chat_id:
                continue
            audio = await ai_client.synthesize_speech(sample, voice={"model": model, "voice": voice, "role": role})
            if audio:
                await self.api.send_voice(chat_id=message.chat_id, audio_bytes=audio)
            else:
                await self.answer(message, "   (этот голос сейчас недоступен — ошибка в логах бота)")

    async def choose_voice(self, message: NormalizedMessage, arg: str) -> None:
        arg = arg.strip()
        if arg == "0":
            await self.redis.delete_state(TTS_VOICE_KEY)
            await self._send_with_voice(message, "Готово! Вернул голос из настроек .env.")
            return
        if not arg.isdigit() or not 1 <= int(arg) <= len(VOICE_CHOICES):
            await self.answer(message, f"Напиши номер голоса от 1 до {len(VOICE_CHOICES)}, например: /voice 2")
            return
        model, voice, role, title = VOICE_CHOICES[int(arg) - 1]
        await self.redis.set_state(TTS_VOICE_KEY, json.dumps({"model": model, "voice": voice, "role": role}))
        await self._send_with_voice(message, f"Готово! Теперь я говорю так: {title}. Так меня будут слышать все ученики.")
