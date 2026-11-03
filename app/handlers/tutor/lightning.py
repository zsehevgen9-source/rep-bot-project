"""Игра «⚡ Молния»: 60 секунд, вопросы кнопками, 3 жизни, рекорд и картинка с результатом.

Redis: zap_<uid> — текущая игра, zap_best_<uid> — рекорды {"math": 17, "eng": 9}.
Время считаем по часам бота (topic_progress.now): ответ после 60-й секунды уже не засчитывается.
Нейросеть и голос не используются — игра бесплатная для Yandex Cloud.
"""
from app.handlers.tutor.common import *  # noqa: F401,F403
from app.services import cards
from app.services import lightning as zap_rules


class LightningMixin:

    def _zap_modes(self, grade: Optional[int]) -> List[str]:
        return ["math", "eng"] if (grade or 1) >= 2 else ["math"]

    async def _zap_best(self, uid: int) -> Dict[str, int]:
        data = load_json(await self.redis.get_state(f"zap_best_{uid}"), {})
        return data if isinstance(data, dict) else {}

    async def show_lightning(self, message: NormalizedMessage) -> None:
        uid = message.user_id
        grade = await self._get_grade(uid)
        best = await self._zap_best(uid)
        lines = ["⚡ Молния\n",
                 f"За {zap_rules.GAME_SECONDS} секунд ответь на как можно больше вопросов. "
                 f"Жизней — {'❤️' * zap_rules.LIVES}: каждая ошибка забирает одну.",
                 "Отвечай кнопками — чем быстрее, тем больше успеешь!"]
        records = [f"{zap_rules.MODES[m]}: {best[m]}" for m in self._zap_modes(grade) if best.get(m)]
        if records:
            lines.append("\n🏆 Твои рекорды — " + " · ".join(records))
        rows = [[_btn(zap_rules.MODES[m], f"zap_go_{m}") for m in self._zap_modes(grade)],
                [_back(f"subjects_{grade}" if grade else "back_to_menu")]]
        await self._quiet(message, "\n".join(lines), keyboard=_kb(rows))

    async def start_lightning(self, message: NormalizedMessage, mode: str) -> None:
        uid = message.user_id
        grade = await self._get_grade(uid) or 1
        if mode not in self._zap_modes(grade):
            mode = "math"
        state = {"mode": mode, "grade": grade, "start": topic_progress.now().timestamp(), "score": 0, "wrong": 0,
                 "lives": zap_rules.LIVES, "n": 0, "game": random.randint(1000, 9999)}
        await self._zap_next(message, state, "🏁 Поехали!")

    async def _zap_next(self, message: NormalizedMessage, state: Dict[str, Any], head: str) -> None:
        q = zap_rules.question(state["mode"], state["grade"])
        state["n"] += 1
        state["q"] = q
        await self.redis.set_state(f"zap_{message.user_id}", json.dumps(state, ensure_ascii=False), ex=600)
        left = max(0, int(zap_rules.GAME_SECONDS - (topic_progress.now().timestamp() - state["start"])))
        lives = "❤️" * state["lives"] + "🖤" * (zap_rules.LIVES - state["lives"])
        text = f"{head}\n\n⚡ {state['score']} · {lives} · ⏱ {left} с\n\n{q['text']}"
        tag = f"{state['game']}_{state['n']}"
        buttons = [_btn(opt, f"zap_a_{tag}_{i}") for i, opt in enumerate(q["options"])]
        rows = [buttons] if _row_fits(buttons) else _fit_rows(buttons, max_per_row=2)
        rows.append([_btn("⏹ Стоп", "zap_stop")])
        await self._quiet(message, text, keyboard=_kb(rows))

    async def answer_lightning(self, message: NormalizedMessage, payload: str) -> None:
        """payload: zap_a_<игра>_<номер вопроса>_<вариант>."""
        uid = message.user_id
        state = load_json(await self.redis.get_state(f"zap_{uid}"), None)
        try:
            game, n, choice = (int(x) for x in payload[len("zap_a_"):].split("_"))
        except ValueError:
            game = n = choice = -1
        if not state or state.get("game") != game or state.get("n") != n:
            await self._quiet(message, "⚡ Эта игра уже закончилась.", keyboard=_kb([[_btn("⚡ Сыграть ещё", "zap")]]))
            return
        q = state["q"]
        elapsed = topic_progress.now().timestamp() - state["start"]
        if elapsed > zap_rules.GAME_SECONDS:
            await self._zap_finish(message, state, "⏱ Время вышло! Последний ответ не успел.")
            return
        if choice == q["answer"]:
            state["score"] += 1
            head = "✅ Верно!"
        else:
            state["wrong"] += 1
            state["lives"] -= 1
            head = f"❌ Было: {q['options'][q['answer']]}"
            if state["lives"] <= 0:
                await self._zap_finish(message, state, f"{head}\n💔 Жизни закончились!")
                return
        await self._zap_next(message, state, head)

    async def stop_lightning(self, message: NormalizedMessage) -> None:
        state = load_json(await self.redis.get_state(f"zap_{message.user_id}"), None)
        if not state:
            await self.show_lightning(message)
            return
        await self._zap_finish(message, state, "⏹ Игра остановлена.")

    async def _zap_finish(self, message: NormalizedMessage, state: Dict[str, Any], head: str) -> None:
        uid = message.user_id
        await self.redis.delete_state(f"zap_{uid}")
        mode, score = state["mode"], state["score"]
        best = await self._zap_best(uid)
        new_record = score > best.get(mode, 0)
        if new_record:
            best[mode] = score
            await self.redis.set_state(f"zap_best_{uid}", json.dumps(best))
        grade = await self._get_grade(uid)
        lines = [head, "", f"⚡ Результат: {score} {_answers_word(score)} · {zap_rules.rank(score)}"]
        if new_record and score:
            lines.append("🎉 Новый рекорд!")
        elif best.get(mode):
            lines.append(f"🏆 Рекорд: {best[mode]}")
        points = zap_rules.points_for(score)
        if points:
            profile = await self._load_profile(uid)
            rewards = load_json(profile.get("rewards"), {})
            rewards["points"] = rewards.get("points", 0) + points
            await self._save_profile(uid, rewards=json.dumps(rewards, ensure_ascii=False))
            mode_r = rewards_rules.mode_for_grade(grade)
            lines.append(rewards_rules.gained_label(points, mode_r) or f"+{points} очков")
        rows = [[_btn("⚡ Ещё раз", f"zap_go_{mode}")], [_back(f"subjects_{grade}" if grade else "back_to_menu")]]
        text = "\n".join(lines)
        name = await self.redis.get_state(f"name_{uid}") or "Ученик"
        caption = name + (f", {grade} класс" if grade else "")
        sent = await self._send_card(message, cards.lightning_card, caption, zap_rules.MODES[mode], score,
                                     max(best.get(mode, 0), score), new_record and score > 0,
                                     zap_rules.rank(score), score, state["wrong"], text=text, keyboard=_kb(rows))
        if not sent:
            await self._quiet(message, text, keyboard=_kb(rows))


def _answers_word(n: int) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return "верных"
    return {1: "верный"}.get(n % 10, "верных")
