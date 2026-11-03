/* Маячок — мини-приложение для MAX.
 * Данные берутся с сервера бота (/api/...). Каждый запрос подписан MAX (заголовок X-Init-Data),
 * поэтому сервер точно знает, кто открыл приложение. Открытое не из MAX — работает демо-режим.
 * Весь текст вставляется через textContent (функция el) — HTML из данных никогда не выполняется.
 */
(function () {
  "use strict";

  const WA = window.WebApp || null;
  const INIT = (WA && WA.initData) || "";
  // config.js: MAYACHOK_API — адрес сервера бота (пусто — тот же сайт), MAYACHOK_DEMO — всегда демо (для GitHub Pages)
  const API_BASE = (window.MAYACHOK_API || "").replace(/\/$/, "");
  const DEMO = !INIT || !!window.MAYACHOK_DEMO;
  const START = (WA && WA.initDataUnsafe && WA.initDataUnsafe.start_param) || new URLSearchParams(location.search).get("startapp") || "";

  const SUBJ = {
    math: ["🔢", "#6366f1"], geo_math: ["📐", "#8b5cf6"], rus: ["✍️", "#ef4444"], read: ["📖", "#f59e0b"],
    lit: ["📚", "#d97706"], world: ["🌍", "#22c55e"], eng: ["🔤", "#3b82f6"], hist: ["🏛️", "#ea580c"],
    soc: ["⚖️", "#a855f7"], geo: ["🗺️", "#14b8a6"], bio: ["🌿", "#16a34a"], phys: ["🧲", "#0ea5e9"],
    chem: ["🧪", "#d946ef"], inf: ["💻", "#4f46e5"], obzr: ["🛡️", "#dc2626"],
  };
  const STATUS = {
    known: ["знает", "var(--green)"], mastered: ["закреплено", "var(--green)"],
    learning: ["изучаем", "var(--orange)"], weak: ["подтянуть", "var(--red)"],
  };
  const DAY_FULL = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"];

  const $app = document.getElementById("app");
  const $tabs = document.getElementById("tabs");
  const $toast = document.getElementById("toast");

  const S = { data: null, tab: "home", child: null, back: null, quiz: null };

  // ---------- маленькие помощники ----------

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "style") node.setAttribute("style", value);
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }
  const mascot = (mood, cls) => el("img", { src: `img/${mood || "happy"}.png`, alt: "Маячок", class: cls || "mascot" });
  const subjIcon = (id) => (SUBJ[id] || ["📘", "#6366f1"]);
  const tint = (color) => `background:${color}22;color:${color}`;
  function pct(a, b) { return b ? Math.round((100 * a) / b) : 0; }
  function plural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
    return many;
  }
  function toast(text, ms) {
    $toast.textContent = text;
    $toast.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => ($toast.hidden = true), ms || 2600);
  }
  function setBack(handler) {
    S.back = handler;
    if (!WA || !WA.BackButton) return;
    try { handler ? WA.BackButton.show() : WA.BackButton.hide(); } catch (e) { /* старые версии MAX */ }
  }
  if (WA && WA.BackButton) {
    try { WA.BackButton.onClick(() => S.back && S.back()); } catch (e) { /* нет кнопки — не страшно */ }
  }
  function openChat() {
    const link = S.data && S.data.bot_link;
    if (!link) return;
    try {
      if (WA && WA.openMaxLink) WA.openMaxLink(link); else window.open(link, "_blank");
    } catch (e) { window.open(link, "_blank"); }
  }
  function confetti() {
    const box = el("div", { class: "confetti" });
    const colors = ["#facc15", "#ef4444", "#4f46e5", "#22c55e", "#7c3aed", "#14b8a6"];
    for (let i = 0; i < 60; i++) {
      box.append(el("i", {
        style: `left:${Math.random() * 100}%;background:${colors[i % colors.length]};` +
          `animation-duration:${1.6 + Math.random() * 1.6}s;animation-delay:${Math.random() * .5}s`,
      }));
    }
    document.body.append(box);
    setTimeout(() => box.remove(), 4000);
  }

  // ---------- сервер ----------

  async function api(path, body) {
    if (DEMO) return Demo.call(path, body);
    const response = await fetch(API_BASE + path, {
      method: body ? "POST" : "GET",
      headers: Object.assign({ "X-Init-Data": INIT }, body ? { "Content-Type": "application/json" } : {}),
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = {};
    try { data = await response.json(); } catch (e) { /* пустой ответ */ }
    if (!response.ok) {
      const error = new Error(data.error || "Нет связи с Маячком. Попробуй ещё раз.");
      error.code = data.code;
      throw error;
    }
    return data;
  }
  const withChild = (path) => (S.child ? `${path}${path.includes("?") ? "&" : "?"}child=${S.child}` : path);

  async function chatAction(action, params) {
    try {
      await api("/api/chat", Object.assign({ action }, params || {}));
      toast("Маячок пишет тебе в чат 💬");
      setTimeout(openChat, 700);
    } catch (e) { toast(e.message); }
  }

  // ---------- каркас: вкладки ----------

  function tabsFor(d) {
    if (d.role === "parent_view") {
      return [["report", "📋", "Отчёт"], ["progress", "📊", "Прогресс"], ["schedule", "🗓", "Расписание"],
        ...(d.children.length ? [["kids", "👨‍👩‍👧", "Дети"]] : [])];
    }
    if (d.role !== "student") return [];
    const tabs = [["home", "🏠", "Главная"], ["train", "🎯", "Тренажёр"], ["games", "🎮", "Игры"], ["progress", "📊", "Прогресс"], ["schedule", "🗓", "Расписание"]];
    if (d.children.length) tabs.push(["kids", "👨‍👩‍👧", "Дети"]);
    return tabs;
  }

  function renderTabs() {
    const tabs = tabsFor(S.data);
    $tabs.hidden = !tabs.length || !!S.quiz;
    $tabs.replaceChildren(...tabs.map(([id, ico, name]) => el("button", {
      class: "tab" + (S.tab === id ? " active" : ""),
      onclick: () => go(id),
    }, el("span", { class: "t-ico" }, ico), name)));
  }

  function go(tab) {
    S.tab = tab;
    S.quiz = null;
    setBack(null);
    window.scrollTo(0, 0);
    render();
  }

  function page(...children) {
    const top = DEMO ? [el("div", { class: "demo-banner" }, window.MAYACHOK_DEMO
      ? "👀 Демо-версия: данные выдуманные — так приложение выглядит у ученика."
      : "👀 Демо-режим: так приложение выглядит у ученика. Настоящие данные — когда откроете его из MAX.")] : [];
    $app.replaceChildren(...top, ...children);
  }

  function render() {
    const d = S.data;
    renderTabs();
    if (d.role === "new") return renderNew(d);
    if (d.role === "parent") return renderKids(d);
    const screens = {
      home: renderHome, train: renderTrain, games: renderGames, progress: renderProgress, schedule: renderSchedule,
      report: renderReport, kids: renderKids,
    };
    (screens[S.tab] || renderHome)(d);
  }

  async function load() {
    try {
      S.data = await api(withChild("/api/state"));
    } catch (e) {
      page(el("div", { class: "empty" }, mascot("happy", ""), el("p", {}, e.message),
        el("button", { class: "btn", style: "margin-top:14px", onclick: load }, "Попробовать ещё раз")));
      return;
    }
    if (S.data.role === "parent_view" && !["report", "progress", "schedule"].includes(S.tab)) S.tab = "report";
    render();
  }

  // ---------- новый пользователь ----------

  function renderNew() {
    page(
      el("div", { class: "hero" },
        el("div", { class: "kicker" }, "Маячок · репетитор"),
        el("h1", {}, "Привет!"),
        el("p", { class: "sub" }, "Я Маячок. Давай сначала познакомимся в чате — выбери там свой класс."),
        mascot("cheer", "mascot big")),
      el("div", { class: "section" }, el("button", { class: "btn", onclick: openChat }, "💬 Перейти в чат")),
    );
  }

  // ---------- главная ----------

  function heroBlock(d, title, sub, mood) {
    const chips = [el("span", { class: "chip" }, `${d.grade} класс`)];
    if (d.level) chips.push(el("span", { class: "chip" }, `★ ${d.level}`));
    return el("div", { class: "hero" },
      el("div", { class: "kicker" }, "Маячок · репетитор"),
      el("h1", {}, title),
      sub ? el("p", { class: "sub" }, sub) : null,
      el("div", { class: "chip-row" }, chips),
      mascot(mood || "happy"));
  }

  function statTiles(d) {
    return el("div", { class: "stats" },
      el("div", { class: "stat" }, el("div", { class: "label" }, "Очки"),
        el("div", { class: "value", style: "color:var(--indigo)" }, `${d.points}`),
        el("div", { class: "label" }, d.to_next ? `до уровня ${d.to_next}` : d.unit)),
      el("div", { class: "stat" }, el("div", { class: "label" }, "Серия"),
        el("div", { class: "value", style: "color:var(--orange)" }, `${d.streak} 🔥`),
        el("div", { class: "label" }, plural(d.streak, "день", "дня", "дней") + " подряд")),
      el("div", { class: "stat" }, el("div", { class: "label" }, "Неделя"),
        el("div", { class: "value", style: "color:var(--teal)" }, `${d.week_done}/${d.week_goal}`),
        el("div", { class: "bar" }, el("i", { style: `width:${pct(d.week_done, d.week_goal)}%` }))));
  }

  function renderHome(d) {
    const hour = new Date().getHours();
    const hello = hour < 12 ? "Доброе утро" : hour < 18 ? "Привет" : "Добрый вечер";
    const lessons = d.today.lessons;
    const todayBlock = lessons.length
      ? el("div", { class: "list" }, lessons.map((l) => {
        const [ico, color] = subjIcon(l.id);
        return el("button", { class: "item", onclick: () => lessonSheet(l) },
          el("div", { class: "ico", style: tint(color) }, ico),
          el("div", { class: "body" }, el("div", { class: "title" }, l.title), el("div", { class: "meta" }, "Закрепим, что было на уроке")),
          el("span", { class: "go" }, "›"));
      }))
      : el("div", { class: "card empty" }, d.today.weekday === "Вс" ? "Сегодня выходной 🌤 Можно повторить что-нибудь в тренажёре."
        : "Расписание на сегодня не внесено.", el("div", { class: "btn-row" },
        el("button", { class: "btn secondary small", style: "margin:0 auto", onclick: () => go("schedule") }, "🗓 Внести расписание")));

    const due = d.due.length
      ? el("div", { class: "list" }, d.due.map((t) => {
        const [ico, color] = subjIcon(t.subject_id);
        return el("button", { class: "item", onclick: () => topicSheet(t) },
          el("div", { class: "ico", style: tint(color) }, ico),
          el("div", { class: "body" }, el("div", { class: "title" }, t.topic),
            el("div", { class: "meta" }, `${t.subject}${t.weak ? " · стоит подтянуть" : " · пора повторить"}`)),
          el("span", { class: "go" }, "›"));
      }))
      : el("div", { class: "card empty" }, "Повторять пока нечего — всё свежее в памяти ✨");

    page(
      heroBlock(d, `${hello}, ${d.name || "друг"}!`, lessons.length ? "Готов закрепить сегодняшние уроки?" : "Чем займёмся сегодня?", "happy"),
      statTiles(d),
      d.games ? chestBanner(d.games.chest) : null,
      d.games ? el("div", { class: "section" }, lighthouseCard(d.games.lighthouse)) : null,
      el("div", { class: "section" },
        el("div", { class: "section-head" }, el("h2", {}, `Сегодня · ${d.today.weekday}`)), todayBlock),
      el("div", { class: "section" },
        el("div", { class: "section-head" }, el("h2", {}, "Пора повторить")), due),
      el("div", { class: "section" },
        el("div", { class: "btn-row" },
          el("button", { class: "btn", onclick: () => go("train") }, "🎯 Тренажёр"),
          el("button", { class: "btn secondary", onclick: () => chatAction("homework") }, "📸 Домашка"))),
    );
  }

  // ---------- листы снизу ----------

  function sheet(...children) {
    const back = el("div", { class: "sheet-back" });
    const box = el("div", { class: "sheet" }, el("div", { class: "grip" }), ...children);
    const close = () => { back.remove(); box.remove(); setBack(prevBack); };
    const prevBack = S.back;
    back.addEventListener("click", close);
    document.body.append(back, box);
    setBack(close);
    return close;
  }

  function lessonSheet(lesson) {
    const subject = (S.data.subjects || []).find((s) => s.id === lesson.id) || { now: [] };
    let close;
    close = sheet(
      el("h2", {}, lesson.title),
      el("p", { class: "muted", style: "margin:6px 0 14px" }, "Что проходили на уроке? Я объясню тему, дам пробный пример и задания на оценку."),
      subject.now.length ? el("div", { class: "list" }, subject.now.map((topic) => el("button", {
        class: "item", onclick: () => { close(); chatAction("lesson", { subject_id: lesson.id, topic }); },
      }, el("div", { class: "ico", style: tint(subjIcon(lesson.id)[1]) }, "📘"),
        el("div", { class: "body" }, el("div", { class: "title" }, topic), el("div", { class: "meta" }, "Урок по этой теме в чате")),
        el("span", { class: "go" }, "›")))) : null,
      el("div", { class: "btn-row" },
        el("button", { class: "btn secondary", onclick: () => { close(); chatAction("ask_topic", { subject_id: lesson.id }); } }, "✏️ Другая тема"),
        el("button", { class: "btn", onclick: () => { close(); startQuiz(lesson.id); } }, "🎯 Тест")),
    );
  }

  function topicSheet(t) {
    let close;
    close = sheet(
      el("h2", {}, t.topic),
      el("p", { class: "muted", style: "margin:6px 0 14px" }, t.subject),
      el("div", { class: "list" },
        el("button", { class: "item", onclick: () => { close(); startQuiz(t.subject_id, t.topic); } },
          el("div", { class: "ico", style: tint("#14b8a6") }, "🎯"),
          el("div", { class: "body" }, el("div", { class: "title" }, "Тест по теме"), el("div", { class: "meta" }, "Прямо здесь, несколько вопросов")),
          el("span", { class: "go" }, "›")),
        el("button", { class: "item", onclick: () => { close(); chatAction("lesson", { subject_id: t.subject_id, topic: t.topic }); } },
          el("div", { class: "ico", style: tint("#4f46e5") }, "📘"),
          el("div", { class: "body" }, el("div", { class: "title" }, "Объясни тему"), el("div", { class: "meta" }, "Урок с примерами в чате")),
          el("span", { class: "go" }, "›"))),
    );
  }

  // ---------- тренажёр ----------

  function renderTrain(d) {
    page(
      el("div", { class: "section-head", style: "margin-top:4px" }, el("h2", { style: "font-size:26px" }, "Тренажёр"),
        el("span", { class: "note" }, "выбери предмет")),
      el("div", { class: "subjects" }, d.subjects.map((s) => {
        const [ico, color] = subjIcon(s.id);
        const total = s.good + s.learning + s.weak;
        return el("button", { class: "subject", onclick: () => subjectSheet(s) },
          el("div", { class: "ico", style: tint(color) }, ico),
          el("div", { class: "name" }, s.title),
          el("div", { class: "bar", style: "margin-top:0" }, el("i", { style: `width:${pct(s.good, total)}%;background:${color}` })),
          el("div", { class: "lvl" }, `Уровень: ${s.level}`));
      })),
    );
  }

  function subjectSheet(s) {
    let close;
    const topics = [...s.now.map((t) => ({ topic: t, note: "сейчас по программе" })),
      ...s.topics.filter((t) => t.status === "weak" || t.status === "learning").slice(0, 4)
        .map((t) => ({ topic: t.topic, note: STATUS[t.status][0] }))];
    const seen = new Set();
    close = sheet(
      el("h2", {}, s.title),
      el("p", { class: "muted", style: "margin:6px 0 14px" },
        `Уровень сложности: ${s.level}` + (s.textbook ? ` · учебник: ${s.textbook}` : "")),
      el("button", { class: "btn", onclick: () => { close(); startQuiz(s.id); } }, "🎲 Тест по предмету"),
      topics.length ? el("div", { class: "section" }, el("div", { class: "section-head" }, el("h2", {}, "Или по теме")),
        el("div", { class: "list" }, topics.filter((t) => !seen.has(t.topic) && seen.add(t.topic)).map((t) => el("button", {
          class: "item", onclick: () => { close(); startQuiz(s.id, t.topic); },
        }, el("div", { class: "ico", style: tint(subjIcon(s.id)[1]) }, "🎯"),
          el("div", { class: "body" }, el("div", { class: "title" }, t.topic), el("div", { class: "meta" }, t.note)),
          el("span", { class: "go" }, "›"))))) : null,
    );
  }

  async function startQuiz(subjectId, topic) {
    S.quiz = { loading: true };
    renderTabs();
    setBack(() => go(S.tab));
    page(el("div", { class: "loading" },
      el("div", { class: "lighthouse" }, el("div", { class: "beam" }), mascot("happy", "")),
      el("h2", {}, "Маячок составляет задания…"),
      el("p", {}, "Проверяю каждый вопрос, чтобы не было ошибок. Это займёт до минуты.")));
    let test;
    try {
      test = await api("/api/train/start", { subject_id: subjectId, topic: topic || null });
    } catch (e) {
      S.quiz = null;
      page(el("div", { class: "empty", style: "padding-top:40px" }, mascot("happy", ""), el("p", {}, e.message),
        el("div", { class: "btn-row" },
          el("button", { class: "btn secondary", onclick: () => go("train") }, "К предметам"),
          e.code !== "limit" ? el("button", { class: "btn", onclick: () => startQuiz(subjectId, topic) }, "Ещё раз") : null)));
      renderTabs();
      return;
    }
    S.quiz = { test, index: 0, answers: [], subjectId, topic, busy: false };
    renderQuestion();
  }

  function renderQuestion(feedback) {
    const quiz = S.quiz;
    const q = quiz.test.questions[quiz.index];
    const total = quiz.test.questions.length;
    setBack(() => {
      let close;
      close = sheet(el("h2", {}, "Закончить тест?"),
        el("p", { class: "muted", style: "margin:6px 0 14px" }, "Ответы этого теста не сохранятся."),
        el("div", { class: "btn-row" },
          el("button", { class: "btn secondary", onclick: () => close() }, "Продолжить"),
          el("button", { class: "btn", onclick: () => { close(); go("train"); } }, "Закончить")));
    });
    const options = q.options.map((option, i) => el("button", {
      class: "option", "data-option": option, onclick: () => answer(option),
    }, el("span", { class: "num" }, i + 1), el("span", {}, option)));
    page(
      el("div", { class: "quiz-top" },
        el("div", { class: "bar" }, el("i", { style: `width:${pct(quiz.index, total)}%;background:var(--indigo)` })),
        el("span", { class: "count" }, `${quiz.index + 1}/${total}`)),
      el("div", { class: "question" },
        el("div", { class: "topic" }, q.topic || quiz.test.subject), q.q),
      el("div", { class: "options" }, options),
      el("div", { class: "feedback", id: "feedback" }, feedback || null),
    );
  }

  async function answer(choice) {
    const quiz = S.quiz;
    if (!quiz || quiz.busy) return;
    quiz.busy = true;
    let result;
    try {
      result = await api("/api/train/answer", { index: quiz.index, choice });
    } catch (e) {
      quiz.busy = false;
      toast(e.message);
      if (e.code === "expired" || e.code === "answered") go("train");
      return;
    }
    quiz.answers.push(result.correct);
    const button = $app.querySelector(`[data-option="${CSS.escape(choice)}"]`);
    if (button) button.classList.add(result.correct ? "right" : "wrong");
    const praise = ["Верно! 🎉", "Точно в цель! 🎯", "Молодец! 💪", "Так держать! ✨"];
    const fb = document.getElementById("feedback");
    fb.replaceChildren(mascot(result.correct ? "cheer" : "wink", ""),
      el("div", { class: "say" }, result.correct ? praise[quiz.index % praise.length] : "Не страшно! Разберём эту ошибку после теста."));
    setTimeout(() => {
      quiz.busy = false;
      if (result.done) return renderResult(result.summary);
      quiz.index += 1;
      renderQuestion();
    }, result.correct ? 850 : 1400);
  }

  function ring(right, total) {
    const share = total ? right / total : 0;
    const color = share >= 0.8 ? "var(--green)" : share >= 0.5 ? "var(--teal)" : share >= 0.25 ? "var(--orange)" : "var(--red)";
    const r = 80, c = 2 * Math.PI * r;
    const svg = `<svg width="190" height="190" viewBox="0 0 190 190"><circle cx="95" cy="95" r="${r}" fill="none" stroke="var(--line)" stroke-width="18"/>` +
      `<circle cx="95" cy="95" r="${r}" fill="none" stroke="${color}" stroke-width="18" stroke-linecap="round" stroke-dasharray="${c}" stroke-dashoffset="${c}">` +
      `<animate attributeName="stroke-dashoffset" from="${c}" to="${c * (1 - share)}" dur=".9s" fill="freeze"/></circle></svg>`;
    const box = el("div", { class: "ring" });
    box.innerHTML = svg;   // разметка своя, без данных с сервера
    box.append(el("div", { class: "center" }, el("b", {}, `${right}/${total}`), el("span", {}, `${pct(right, total)}%`)));
    return box;
  }

  function renderResult(summary) {
    const quiz = S.quiz;
    const share = summary.total ? summary.right / summary.total : 0;
    const mood = share >= 0.8 ? "cheer" : share >= 0.5 ? "wink" : "happy";
    const title = share === 1 ? "Безупречно!" : share >= 0.8 ? "Отличный результат!" : share >= 0.5 ? "Хорошо!" : "Тест был непростой";
    setBack(() => go("train"));
    if (share >= 0.8) confetti();
    const level = summary.level_change > 0 ? `🚀 Дальше будет сложнее: уровень «${summary.level_after}»`
      : summary.level_change < 0 ? `Немного снизим сложность: уровень «${summary.level_after}»` : null;
    page(el("div", { class: "result" },
      mascot(mood, ""), el("h2", { style: "font-size:26px;font-weight:900;margin-top:6px" }, title),
      ring(summary.right, summary.total),
      el("div", { class: "answers" }, quiz.answers.map((ok, i) => el("i", { style: `background:var(--${ok ? "green" : "red"})` }, i + 1))),
      summary.reward ? el("p", { style: "margin-top:14px;font-weight:800" }, summary.reward) : null,
      level ? el("p", { class: "muted", style: "margin-top:6px" }, level) : null),
      summary.wrong.length ? el("div", { class: "section" },
        el("div", { class: "section-head" }, el("h2", {}, "Разобрать ошибки")),
        el("p", { class: "muted", style: "margin:0 4px 10px" }, "Правильный ответ не показываю — в чате я помогу найти его самому подсказками."),
        el("div", { class: "list" }, summary.wrong.slice(0, 4).map((n) => el("button", {
          class: "item", onclick: () => chatAction("fix", { n }),
        }, el("div", { class: "ico", style: tint("#ef4444") }, n + 1),
          el("div", { class: "body" }, el("div", { class: "title" }, quiz.test.questions[n].q),
            el("div", { class: "meta" }, "Разобрать в чате")),
          el("span", { class: "go" }, "›"))))) : null,
      el("div", { class: "btn-row", style: "margin-top:20px" },
        el("button", { class: "btn secondary", onclick: () => { S.quiz = null; refresh("train"); } }, "К предметам"),
        el("button", { class: "btn", onclick: () => startQuiz(quiz.subjectId, quiz.topic) }, "🎲 Ещё тест")),
    );
  }

  async function refresh(tab) {
    try { S.data = await api(withChild("/api/state")); } catch (e) { /* покажем старые данные */ }
    go(tab);
  }

  // ---------- прогресс ----------

  function renderProgress(d) {
    const max = Math.max(1, ...d.week.map((w) => w.solved));
    const subjects = d.subjects.filter((s) => s.good + s.learning + s.weak > 0);
    const have = d.achievements.filter((a) => a.have).length;
    page(
      d.role === "parent_view" ? null : heroBlock(d, "Мой прогресс", `${d.points} ${d.unit} · ${d.mode_name}`, have ? "cheer" : "happy"),
      el("div", { class: "section" },
        el("div", { class: "section-head" }, el("h2", {}, "Неделя"), el("span", { class: "note" }, `решено: ${d.report.solved}`)),
        el("div", { class: "card" }, el("div", { class: "week" }, d.week.map((w) => el("div", { class: "col" + (w.today ? " today" : "") },
          el("span", { class: "n" }, w.solved || ""),
          el("div", { class: "pillar" + (w.solved ? " on" : ""), style: `height:${Math.max(8, (100 * w.solved) / max)}%` }),
          el("span", { class: "d" }, w.day)))))),
      el("div", { class: "section" },
        el("div", { class: "section-head" }, el("h2", {}, "Предметы"), el("span", { class: "note" }, "сколько тем уже знает")),
        subjects.length ? el("div", { class: "list" }, subjects.map((s) => {
          const [ico, color] = subjIcon(s.id);
          const total = s.good + s.learning + s.weak;
          return el("button", { class: "item", onclick: () => topicsSheet(s) },
            el("div", { class: "ico", style: tint(color) }, ico),
            el("div", { class: "body" },
              el("div", { class: "title" }, s.title),
              el("div", { class: "bar" }, el("i", { style: `width:${pct(s.good, total)}%;background:${color}` })),
              el("div", { class: "meta", style: "margin-top:4px" },
                `${pct(s.good, total)}% · ${total} ${plural(total, "тема", "темы", "тем")}` + (s.weak ? ` · подтянуть: ${s.weak}` : ""))),
            el("span", { class: "go" }, "›"));
        })) : el("div", { class: "card empty" }, mascot("happy", ""), "Пока пусто. Пройди первый тест в тренажёре — и здесь появятся темы!")),
      d.marks.length ? el("div", { class: "section" },
        el("div", { class: "section-head" }, el("h2", {}, "Оценки за уроки")),
        el("div", { class: "list" }, d.marks.slice(0, 10).map((m) => el("div", { class: "item" },
          el("div", { class: `mark m${m.mark}` }, m.mark),
          el("div", { class: "body" }, el("div", { class: "title" }, m.topic || m.subject),
            el("div", { class: "meta" }, `${m.subject} · ${(m.date || "").split("-").reverse().slice(0, 2).join(".")}`)))))) : null,
      el("div", { class: "section" },
        el("div", { class: "section-head" }, el("h2", {}, "Достижения"), el("span", { class: "note" }, `${have} из ${d.achievements.length}`)),
        el("div", { class: "badges" }, d.achievements.map((a) => {
          const [emoji, ...rest] = a.title.split(" ");
          return el("button", { class: "badge" + (a.have ? "" : " locked"), onclick: () => toast(a.have ? `${a.title} — получено!` : `Как получить: ${a.how}`) },
            el("span", { class: "big" }, emoji), rest.join(" "));
        }))),
    );
  }

  function topicsSheet(s) {
    sheet(
      el("h2", {}, s.title),
      el("p", { class: "muted", style: "margin:6px 0 14px" }, `Уровень: ${s.level}`),
      el("div", { class: "list" }, s.topics.map((t) => {
        const [label, color] = STATUS[t.status] || ["", "var(--line)"];
        return el("div", { class: "item" }, el("span", { class: "dot", style: `background:${color}` }),
          el("div", { class: "body" }, el("div", { class: "title" }, t.topic)), el("span", { class: "meta" }, label));
      })),
    );
  }

  // ---------- расписание ----------

  async function renderSchedule() {
    page(el("div", { class: "empty" }, "Загружаю расписание…"));
    let data;
    try { data = await api(withChild("/api/schedule")); } catch (e) { page(el("div", { class: "empty" }, e.message)); return; }
    const schedule = data.schedule || {};
    const titles = Object.fromEntries(data.subjects.map((s) => [s.id, s.title]));
    const today = new Date().getDay();
    let day = today >= 1 && today <= 6 ? today - 1 : 0;
    let dirty = false;

    async function save() {
      try {
        const res = await api("/api/schedule", { schedule, child: S.child });
        Object.assign(schedule, res.schedule);
        dirty = false;
        toast("Расписание сохранено ✅");
        draw();
      } catch (e) { toast(e.message); }
    }
    function change(fn) { fn(schedule[day] = schedule[day] || []); if (!schedule[day].length) delete schedule[day]; dirty = true; draw(); }

    function draw() {
      const lessons = schedule[day] || [];
      page(
        el("div", { class: "section-head", style: "margin-top:4px" }, el("h2", { style: "font-size:26px" }, "Расписание"),
          el("span", { class: "note" }, S.data.role === "parent_view" ? S.data.name : "уроки по дням")),
        el("div", { class: "days" }, data.days.map((name, i) => el("button", {
          class: "day" + (i === day ? " active" : ""), onclick: () => { day = i; draw(); },
        }, name, el("small", {}, (schedule[i] || []).length || "—")))),
        el("div", { class: "section", style: "margin-top:12px" },
          el("div", { class: "section-head" }, el("h2", {}, DAY_FULL[day])),
          lessons.length ? el("div", { class: "list" }, lessons.map((sid, i) => {
            const [ico, color] = subjIcon(sid);
            return el("div", { class: "lesson-row" }, el("span", { class: "n" }, i + 1),
              el("div", { class: "item" }, el("div", { class: "ico", style: tint(color) }, ico),
                el("div", { class: "body" }, el("div", { class: "title" }, titles[sid] || sid))),
              i > 0 ? el("button", { class: "icon-btn", "aria-label": "Выше", onclick: () => change((l) => l.splice(i - 1, 2, l[i], l[i - 1])) }, "↑")
                : el("span", { class: "icon-btn", style: "visibility:hidden" }),
              el("button", { class: "icon-btn", "aria-label": "Убрать", onclick: () => change((l) => l.splice(i, 1)) }, "✕"));
          })) : el("div", { class: "card empty" }, "Уроков нет. Добавь их кнопками ниже — по порядку, как в дневнике.")),
        el("div", { class: "section" },
          el("div", { class: "section-head" }, el("h2", {}, "Добавить урок")),
          el("div", { class: "picker" }, data.subjects.map((s) => el("button", {
            class: "chip", onclick: () => change((l) => l.length < 10 && l.push(s.id)),
          }, subjIcon(s.id)[0], " ", s.title)))),
        el("div", { class: "section" }, el("button", { class: "btn", disabled: !dirty, onclick: save }, dirty ? "💾 Сохранить" : "✅ Сохранено")),
        el("p", { class: "muted", style: "margin:12px 4px 0;font-size:14px" },
          "Физкультуру, музыку и другие уроки без заданий можно не добавлять. Фото расписания можно прислать Маячку в чат."),
      );
    }
    draw();
  }

  // ---------- родитель ----------

  function renderKids(d) {
    page(
      el("div", { class: "hero" },
        el("div", { class: "kicker" }, "Маячок · для родителей"),
        el("h1", {}, "Кабинет родителя"),
        el("p", { class: "sub" }, "Успехи ребёнка, оценки за уроки и расписание."),
        mascot("wink")),
      el("div", { class: "section" },
        el("div", { class: "section-head" }, el("h2", {}, "Дети")),
        d.children.length ? el("div", { class: "list" }, d.children.map((c) => el("button", {
          class: "item", onclick: () => { S.child = c.id; S.tab = "report"; load(); },
        }, el("div", { class: "ico", style: tint("#7c3aed") }, "🎒"),
          el("div", { class: "body" }, el("div", { class: "title" }, c.name), el("div", { class: "meta" }, c.grade ? `${c.grade} класс` : "класс не выбран")),
          el("span", { class: "go" }, "›")))) : el("div", { class: "card empty" }, "Пока никто не подключён. Попросите ребёнка открыть в чате «⚙️ Настройки → 👨‍👩‍👧 Родитель» и прислать вам код.")),
      S.child || d.role === "student" ? el("div", { class: "section" }, el("button", {
        class: "btn secondary", onclick: () => { S.child = null; S.tab = "home"; load(); },
      }, "← Мой профиль")) : null,
    );
  }

  function renderReport(d) {
    const r = d.report;
    setBack(() => { S.child = null; S.tab = "kids"; load(); });
    page(
      heroBlock(d, d.name || "Ребёнок", "Отчёт за последние 7 дней", r.solved ? "cheer" : "happy"),
      el("div", { class: "stats" },
        el("div", { class: "stat" }, el("div", { class: "label" }, "Дни занятий"), el("div", { class: "value", style: "color:var(--indigo)" }, `${r.days}/7`)),
        el("div", { class: "stat" }, el("div", { class: "label" }, "Решено"), el("div", { class: "value", style: "color:var(--teal)" }, r.solved)),
        el("div", { class: "stat" }, el("div", { class: "label" }, "С 1-й попытки"),
          el("div", { class: "value", style: "color:var(--green)" }, r.accuracy === null ? "—" : `${r.accuracy}%`))),
      el("div", { class: "section" },
        el("div", { class: "section-head" }, el("h2", {}, "Оценки за уроки")),
        d.marks.length ? el("div", { class: "list" }, d.marks.slice(0, 8).map((m) => el("div", { class: "item" },
          el("div", { class: `mark m${m.mark}` }, m.mark),
          el("div", { class: "body" }, el("div", { class: "title" }, m.topic || m.subject), el("div", { class: "meta" }, m.subject)))))
          : el("div", { class: "card empty" }, "Оценок за уроки пока нет.")),
      el("div", { class: "section" },
        el("div", { class: "section-head" }, el("h2", {}, "Стоит подтянуть")),
        d.weak.length ? el("div", { class: "list" }, d.weak.slice(0, 6).map((t) => el("div", { class: "item" },
          el("span", { class: "dot", style: "background:var(--red)" }),
          el("div", { class: "body" }, el("div", { class: "title" }, t.topic), el("div", { class: "meta" }, t.subject)))))
          : el("div", { class: "card empty" }, "Слабых тем нет 👍")),
      el("div", { class: "section" },
        el("div", { class: "section-head" }, el("h2", {}, "Серия и награды")),
        el("div", { class: "card" }, `🔥 ${d.streak} ${plural(d.streak, "день", "дня", "дней")} подряд · ${d.points} ${d.unit}` +
          (d.level ? ` · уровень «${d.level}»` : "") + ` · достижений: ${d.achievements.filter((a) => a.have).length}`)),
    );
  }


  // ---------- игры: Мой маяк, Сундук дня, Молния, Карточки ----------

  function lighthouseCard(lh, big) {
    const next = lh.next;
    const share = next ? Math.min(1, lh.streak / next.days) : 1;
    let note;
    if (lh.at_risk) note = "🧊 Вчера был пропуск — заморозка держит серию. Позанимайся сегодня!";
    else if (lh.today) note = "Сегодня ты уже занимался ✅";
    else if (lh.streak) note = "Сегодня ещё не занимался — реши задание, чтобы маяк рос.";
    else note = "Позанимайся сегодня — и появится фундамент маяка.";
    return el("button", { class: "lh-card" + (big ? " big" : ""), onclick: () => (S.tab === "games" ? lighthouseSheet(lh) : go("games")) },
      el("img", { class: "lh-scene", src: `img/lh${lh.built}.png`, alt: "Мой маяк" }),
      el("div", { class: "lh-info" },
        el("div", { class: "lh-title" }, "🗼 Мой маяк",
          el("span", { class: "lh-streak" }, `🔥 ${lh.streak}`)),
        el("div", { class: "lh-next" }, next ? `До «${next.title}»: ещё ${next.left} ${plural(next.left, "день", "дня", "дней")}` : "Маяк построен полностью! 🎆"),
        el("div", { class: "bar" }, el("i", { style: `width:${Math.round(share * 100)}%;background:var(--orange)` })),
        el("div", { class: "lh-chips" },
          el("span", { class: "chip" }, `🏆 рекорд ${lh.best}`),
          el("span", { class: "chip" }, lh.freeze ? "🧊 заморозка есть" : "🧊 на след. неделе")),
        el("p", { class: "muted lh-note" }, note)));
  }

  function lighthouseSheet(lh) {
    const titles = ["фундамент", "стены маяка", "окна и дверь", "флаг на крыше", "огонь маяка", "чайки", "лодка у причала", "звёздное небо", "праздничный салют"];
    const days = [1, 2, 3, 5, 7, 10, 14, 21, 30];
    sheet(
      el("h2", {}, "🗼 Мой маяк"),
      el("p", { class: "muted", style: "margin:6px 0 14px" }, "Каждый день занятий — новый этап. Выходные серию не прерывают. Заморозка раз в неделю спасает серию, если пропущен один день."),
      el("div", { class: "list" }, titles.map((t, i) => el("div", { class: "item" + (lh.streak >= days[i] ? "" : " dim") },
        el("div", { class: "ico", style: tint(lh.streak >= days[i] ? "#f59e0b" : "#9ca3af") }, lh.streak >= days[i] ? "✓" : days[i]),
        el("div", { class: "body" }, el("div", { class: "title" }, t), el("div", { class: "meta" }, `${days[i]} ${plural(days[i], "день", "дня", "дней")} подряд`))))));
  }

  function chestBanner(ch) {
    if (ch.ready) {
      return el("div", { class: "section" }, el("button", { class: "chest-banner ready", onclick: openChest },
        el("span", { class: "chest-ico" }, "🎁"),
        el("div", { class: "body" }, el("div", { class: "title" }, "Сундук дня готов!"), el("div", { class: "meta" }, "Нажми, чтобы открыть")),
        el("span", { class: "go" }, "›")));
    }
    const text = ch.opened ? "Сундук открыт — новый завтра" : `До сундука: ещё ${ch.left} ${plural(ch.left, "задание", "задания", "заданий")}`;
    return el("div", { class: "section" }, el("button", { class: "chest-banner", onclick: openAlbum },
      el("span", { class: "chest-ico" }, ch.opened ? "📒" : "🔒"),
      el("div", { class: "body" }, el("div", { class: "title" }, text),
        el("div", { class: "bar" }, el("i", { style: `width:${ch.opened ? 100 : pct(ch.need - ch.left, ch.need)}%;background:var(--gold)` })),
        el("div", { class: "meta" }, `Альбом: ${ch.collected} из ${ch.total}`)),
      el("span", { class: "go" }, "›")));
  }

  function renderGames(d) {
    const g = d.games;
    const best = g.zap.best || {};
    page(
      el("div", { class: "section-head", style: "margin-top:4px" }, el("h2", { style: "font-size:26px" }, "Игры"),
        el("span", { class: "note" }, "учимся играя")),
      el("div", { class: "game zap" },
        el("div", { class: "game-head" }, el("span", { class: "game-ico" }, "⚡"),
          el("div", {}, el("div", { class: "game-title" }, "Молния"), el("div", { class: "game-sub" }, "60 секунд · 3 жизни · кто больше?"))),
        el("div", { class: "btn-row" }, g.zap.modes.map((m) => el("button", { class: "btn light", onclick: () => startZap(m.id) },
          m.title, best[m.id] ? el("small", {}, ` 🏆${best[m.id]}`) : null)))),
      g.cards.available ? el("button", { class: "game cards", onclick: () => openCards() },
        el("div", { class: "game-head" }, el("span", { class: "game-ico" }, "🃏"),
          el("div", {}, el("div", { class: "game-title" }, "Карточки"),
            el("div", { class: "game-sub" }, g.cards.due ? `Пора повторить: ${g.cards.due}` : "Термины, даты, формулы — запоминаем надолго")),
          el("span", { class: "go" }, "›"))) : null,
      el("button", { class: "game chest", onclick: g.chest.ready ? openChest : openAlbum },
        el("div", { class: "game-head" }, el("span", { class: "game-ico" + (g.chest.ready ? " shake" : "") }, "🎁"),
          el("div", {}, el("div", { class: "game-title" }, g.chest.ready ? "Сундук дня — открыть!" : "Сундук и альбом"),
            el("div", { class: "game-sub" }, g.chest.ready ? "Внутри наклейка, факт, звание или очки"
              : g.chest.opened ? `Сегодня открыт · альбом ${g.chest.collected}/${g.chest.total}`
                : `Откроется после ${g.chest.need} заданий · ещё ${g.chest.left}`)),
          el("span", { class: "go" }, "›"))),
      el("div", { class: "section" }, lighthouseCard(g.lighthouse, true)),
    );
  }

  // ---- ⚡ Молния ----

  async function startZap(mode) {
    S.quiz = { loading: true };
    renderTabs();
    setBack(() => go("games"));
    page(el("div", { class: "loading" }, el("div", { class: "zap-big" }, "⚡"), el("h2", {}, "Готовлю вопросы…")));
    let game;
    try { game = await api("/api/zap/start", { mode }); } catch (e) {
      S.quiz = null; toast(e.message); go("games"); return;
    }
    const z = { game, i: 0, score: 0, lives: game.lives, answers: [], over: false, start: 0, timer: null };
    S.quiz = { zap: z };
    setBack(() => {
      let close;
      close = sheet(el("h2", {}, "Остановить игру?"),
        el("div", { class: "btn-row" },
          el("button", { class: "btn secondary", onclick: () => close() }, "Играть дальше"),
          el("button", { class: "btn", onclick: () => { close(); zapEnd(z, "stop"); } }, "Стоп")));
    });
    // Обратный отсчёт 3-2-1
    for (const n of ["3", "2", "1"]) {
      if (!S.quiz || S.quiz.zap !== z) return;
      page(el("div", { class: "countdown" }, el("span", {}, n), el("p", {}, game.title)));
      await new Promise((r) => setTimeout(r, 700));
    }
    if (!S.quiz || S.quiz.zap !== z) return;
    z.start = performance.now();
    zapDraw(z);
    const tick = () => {
      if (z.over || !S.quiz || S.quiz.zap !== z) return;
      const left = Math.max(0, game.seconds - (performance.now() - z.start) / 1000);
      const bar = document.getElementById("zap-time");
      const num = document.getElementById("zap-sec");
      if (bar) { bar.style.width = `${(100 * left) / game.seconds}%`; bar.classList.toggle("hurry", left < 10); }
      if (num) num.textContent = Math.ceil(left);
      if (left <= 0) return zapEnd(z, "time");
      z.timer = requestAnimationFrame(tick);
    };
    z.timer = requestAnimationFrame(tick);
  }

  function zapDraw(z, flash) {
    const q = z.game.questions[z.i];
    if (!q) return zapEnd(z, "done");
    const hearts = "❤️".repeat(z.lives) + "🖤".repeat(z.game.lives - z.lives);
    page(
      el("div", { class: "zap-top" },
        el("span", { class: "zap-score" }, `⚡ ${z.score}`),
        el("span", { class: "zap-lives" }, hearts),
        el("span", { class: "zap-sec", id: "zap-sec" }, z.game.seconds)),
      el("div", { class: "bar zap-bar" }, el("i", { id: "zap-time", style: "width:100%" })),
      el("div", { class: "zap-q" + (flash ? ` ${flash}` : "") }, q.text),
      el("div", { class: "zap-options" + (q.options.length > 3 ? " four" : "") }, q.options.map((o, i) => el("button", {
        class: "zap-opt", onclick: (ev) => zapAnswer(z, i, ev.currentTarget),
      }, o))),
      el("p", { class: "muted", style: "text-align:center;margin-top:14px", id: "zap-hint" }, flash === "bad" ? `Было: ${z.lastRight}` : " "),
    );
    const left = Math.max(0, z.game.seconds - (performance.now() - z.start) / 1000);
    const bar = document.getElementById("zap-time");
    if (bar) bar.style.width = `${(100 * left) / z.game.seconds}%`;
    const num = document.getElementById("zap-sec");
    if (num) num.textContent = Math.ceil(left);
  }

  function zapAnswer(z, choice, button) {
    if (z.over || z.lock) return;
    const q = z.game.questions[z.i];
    const ok = choice === q.answer;
    z.answers.push(choice);
    z.lock = true;
    button.classList.add(ok ? "right" : "wrong");
    try { if (WA && WA.HapticFeedback) WA.HapticFeedback.impactOccurred(ok ? "light" : "heavy"); } catch (e) { /* нет вибрации */ }
    if (ok) z.score += 1; else { z.lives -= 1; z.lastRight = q.options[q.answer]; }
    setTimeout(() => {
      z.lock = false;
      if (z.over) return;
      if (z.lives <= 0) return zapEnd(z, "lives");
      z.i += 1;
      zapDraw(z, ok ? "good" : "bad");
    }, ok ? 180 : 450);
  }

  async function zapEnd(z, why) {
    if (z.over) return;
    z.over = true;
    cancelAnimationFrame(z.timer);
    page(el("div", { class: "loading" }, el("div", { class: "zap-big" }, "⚡"), el("h2", {}, "Считаю очки…")));
    let res;
    try { res = await api("/api/zap/finish", { game: z.game.game, answers: z.answers }); } catch (e) {
      S.quiz = null; toast(e.message); go("games"); return;
    }
    if (res.new_record) confetti();
    const head = { time: "⏱ Время вышло!", lives: "💔 Жизни закончились", stop: "⏹ Игра остановлена", done: "🏁 Вопросы закончились!" }[why];
    setBack(() => refresh("games"));
    page(el("div", { class: "result zap-result" },
      el("p", { class: "muted", style: "font-weight:800" }, head),
      el("div", { class: "zap-score-big" }, res.score),
      el("p", { style: "font-weight:900;font-size:18px" }, `${plural(res.score, "верный ответ", "верных ответа", "верных ответов")} · ${res.rank}`),
      res.new_record ? el("div", { class: "record" }, "🎉 НОВЫЙ РЕКОРД!") : el("p", { class: "muted", style: "margin-top:6px" }, `🏆 Рекорд: ${res.best}`),
      res.reward ? el("p", { style: "margin-top:10px;font-weight:800" }, res.reward) : null),
      el("div", { class: "btn-row", style: "margin-top:24px" },
        el("button", { class: "btn secondary", onclick: () => { S.quiz = null; refresh("games"); } }, "К играм"),
        el("button", { class: "btn", onclick: () => startZap(z.game.mode) }, "⚡ Ещё раз")));
  }

  // ---- 🎁 Сундук ----

  async function openChest() {
    S.quiz = { chest: true };
    renderTabs();
    setBack(null);
    page(el("div", { class: "chest-open" }, el("div", { class: "chest-box shake" }, "🎁"), el("h2", {}, "Открываем сундук…")));
    const wait = new Promise((r) => setTimeout(r, 1400));
    let prize;
    try { [prize] = await Promise.all([api("/api/chest/open", {}), wait]); } catch (e) {
      S.quiz = null; toast(e.message); refresh(S.tab); return;
    }
    const label = { common: "Обычная наклейка", rare: "✨ Редкая наклейка", legendary: "🌟 Легендарная наклейка",
      fact: "💡 Интересный факт", title: "🏅 Новое звание", bonus: "💰 Бонусные очки" }[prize.rarity] || "";
    if (["rare", "legendary", "title"].includes(prize.rarity)) confetti();
    let note = "";
    if (prize.kind === "sticker") note = prize.duplicate ? `Такая уже есть — повторка превращается в очки: ${prize.points_text}` : `В альбоме: ${prize.collected} из ${prize.total}`;
    else if (prize.kind === "title") note = "Теперь его видно в меню бота 😎";
    else note = prize.points_text || "";
    setBack(() => { S.quiz = null; refresh(S.tab); });
    page(
      el("div", { class: `prize r-${prize.rarity}` },
        el("div", { class: "prize-label" }, label),
        el("div", { class: "prize-emoji" }, prize.emoji),
        el("div", { class: "prize-name" + (prize.kind === "fact" ? " fact" : "") }, prize.name),
        note ? el("div", { class: "prize-note" }, note) : null),
      el("p", { class: "muted", style: "text-align:center;margin-top:12px" }, "Следующий сундук — завтра"),
      el("div", { class: "btn-row" },
        el("button", { class: "btn secondary", onclick: () => { S.quiz = null; openAlbum(); } }, "📒 Альбом"),
        el("button", { class: "btn", onclick: () => { S.quiz = null; refresh(S.tab); } }, "Готово")));
  }

  async function openAlbum() {
    S.quiz = { album: true };
    renderTabs();
    setBack(() => { S.quiz = null; refresh("games"); });
    page(el("div", { class: "empty" }, "Открываю альбом…"));
    let a;
    try { a = await api("/api/album"); } catch (e) { S.quiz = null; toast(e.message); go("games"); return; }
    const have = a.stickers.filter((s) => s.count).length;
    const groups = [["common", "Обычные"], ["rare", "Редкие ✨"], ["legendary", "Легендарные 🌟"]];
    page(
      el("div", { class: "section-head", style: "margin-top:4px" }, el("h2", { style: "font-size:26px" }, "📒 Альбом"),
        el("span", { class: "note" }, `${have} из ${a.stickers.length}`)),
      el("div", { class: "bar", style: "height:12px" }, el("i", { style: `width:${pct(have, a.stickers.length)}%;background:var(--gold)` })),
      ...groups.map(([r, name]) => {
        const list = a.stickers.filter((s) => s.rarity === r);
        return el("div", { class: "section" },
          el("div", { class: "section-head" }, el("h2", {}, name), el("span", { class: "note" }, `${list.filter((s) => s.count).length}/${list.length}`)),
          el("div", { class: "album" }, list.map((s) => el("button", {
            class: `sticker r-${r}` + (s.count ? "" : " missing"),
            onclick: () => toast(s.count ? `${s.name}${s.count > 1 ? ` · ×${s.count}` : ""}` : "Ещё не найдена — открывай сундуки!"),
          }, el("span", { class: "s-emoji" }, s.count ? s.emoji : "❔"), el("span", { class: "s-name" }, s.count ? s.name : "???")))));
      }),
      a.titles.length ? el("div", { class: "section" }, el("div", { class: "section-head" }, el("h2", {}, "🏅 Звания")),
        el("div", { class: "picker" }, a.titles.map((t) => el("span", { class: "chip" + (t === a.title ? " gold" : "") }, t)))) : null,
      el("p", { class: "muted", style: "margin:16px 4px 0;font-size:14px" }, "Сундук открывается раз в день после 3 решённых заданий. Повторка превращается в очки, а если 7 сундуков подряд без редкой — следующая точно редкая ✨"),
    );
  }

  // ---- 🃏 Карточки ----

  async function openCards(note) {
    S.quiz = { cards: true };
    renderTabs();
    setBack(() => { S.quiz = null; refresh("games"); });
    page(el("div", { class: "empty" }, "Достаю карточки…"));
    let c;
    try { c = await api("/api/cards"); } catch (e) { S.quiz = null; toast(e.message); go("games"); return; }
    if (typeof note === "string" && note) toast(note, 3200);
    const months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
    const nice = (iso) => { const [, m, dd] = iso.split("-").map(Number); return `${dd} ${months[m - 1]}`; };
    page(
      el("div", { class: "section-head", style: "margin-top:4px" }, el("h2", { style: "font-size:26px" }, "🃏 Карточки"),
        el("span", { class: "note" }, `запомнено ${c.learned} из ${c.total}`)),
      c.due.length ? el("button", { class: "btn", onclick: () => cardsSession(c.due) }, `🔁 Повторить (${c.due_total})`)
        : el("div", { class: "card empty" }, c.total ? `✅ На сегодня всё повторено!${c.next_due ? ` Следующее повторение — ${nice(c.next_due)}.` : ""}`
          : "Здесь будут карточки: термины, даты, формулы. Выбери тему — я составлю набор."),
      el("div", { class: "section" }, el("button", { class: "btn secondary", onclick: () => newDeckSheet(c.subjects) }, "➕ Новый набор")),
      c.decks.length ? el("div", { class: "section" }, el("div", { class: "section-head" }, el("h2", {}, "Мои наборы")),
        el("div", { class: "picker" }, c.decks.map((t) => el("span", { class: "chip" }, t)))) : null,
      el("p", { class: "muted", style: "margin:16px 4px 0;font-size:14px" }, "Вспомни ответ, переверни карточку и честно нажми «Помню» или «Не помню». Забытое вернётся скоро, выученное — через 1, 3, 7, 14 и 30 дней."),
    );
  }

  function newDeckSheet(subjects) {
    let close;
    const body = el("div", {});
    const pickSubject = () => body.replaceChildren(
      el("p", { class: "muted", style: "margin:6px 0 12px" }, "По какому предмету?"),
      el("div", { class: "picker" }, subjects.map((s) => el("button", { class: "chip", onclick: () => pickTopic(s) }, subjIcon(s.id)[0], " ", s.title))));
    async function pickTopic(s) {
      body.replaceChildren(el("p", { class: "muted" }, "Смотрю темы по программе…"));
      let topics = [];
      try { topics = (await api(`/api/cards/topics?subject_id=${encodeURIComponent(s.id)}`)).topics; } catch (e) { /* можно ввести свою */ }
      const input = el("input", { class: "input", placeholder: "Или напиши свою тему", maxlength: "100" });
      body.replaceChildren(
        el("p", { class: "muted", style: "margin:6px 0 12px" }, `${s.title}: выбери тему`),
        topics.length ? el("div", { class: "list" }, topics.map((t) => el("button", { class: "item", onclick: () => make(s, t) },
          el("div", { class: "ico", style: tint(subjIcon(s.id)[1]) }, "🃏"),
          el("div", { class: "body" }, el("div", { class: "title" }, t)), el("span", { class: "go" }, "›")))) : null,
        el("div", { class: "btn-row" }, input, el("button", { class: "btn small", onclick: () => input.value.trim() && make(s, input.value.trim()) }, "Ок")));
    }
    async function make(s, topic) {
      body.replaceChildren(el("div", { class: "loading", style: "padding-top:6px" }, el("div", { class: "zap-big" }, "🃏"),
        el("h2", {}, "Составляю карточки…"), el("p", {}, topic)));
      try {
        const r = await api("/api/cards/new", { subject_id: s.id, topic });
        close();
        openCards(r.added ? `✅ Набор «${r.topic}»: ${r.added} новых карточек` : "Эти карточки у тебя уже есть");
      } catch (e) { toast(e.message); pickTopic(s); }
    }
    close = sheet(el("h2", {}, "➕ Новый набор"), body);
    pickSubject();
  }

  function cardsSession(due) {
    const queue = due.slice();
    const again = new Set();
    let i = 0, right = 0, wrong = 0, reward = "";
    setBack(() => openCards());
    const draw = () => {
      if (i >= queue.length) return finish();
      const c = queue[i];
      const card = el("div", { class: "flip" },
        el("div", { class: "flip-in" },
          el("div", { class: "face front" }, el("div", { class: "topic" }, c.subject), el("div", { class: "q" }, c.front), el("div", { class: "hint" }, "Нажми, чтобы перевернуть")),
          el("div", { class: "face back" }, el("div", { class: "topic" }, c.topic), el("div", { class: "q" }, c.back))));
      const btns = el("div", { class: "btn-row", hidden: true },
        el("button", { class: "btn secondary", onclick: () => grade(false) }, "❌ Не помню"),
        el("button", { class: "btn", onclick: () => grade(true) }, "✅ Помню"));
      card.addEventListener("click", () => { card.classList.add("flipped"); btns.hidden = false; });
      page(el("div", { class: "quiz-top" },
        el("div", { class: "bar" }, el("i", { style: `width:${pct(i, queue.length)}%;background:var(--violet)` })),
        el("span", { class: "count" }, `${i + 1}/${queue.length}`)), card, btns);
    };
    async function grade(ok) {
      const c = queue[i];
      try {
        const r = await api("/api/cards/grade", { id: c.id, remembered: ok });
        if (r.reward) reward = r.reward;
      } catch (e) { toast(e.message); }
      if (ok) right += 1; else { wrong += 1; if (!again.has(c.id)) { again.add(c.id); queue.push(c); } }
      i += 1;
      draw();
    }
    function finish() {
      if (right >= 5) confetti();
      page(el("div", { class: "result" }, mascot(right >= wrong ? "cheer" : "wink", ""),
        el("h2", { style: "font-size:26px;font-weight:900;margin-top:6px" }, "Тренировка готова!"),
        el("p", { style: "margin-top:8px;font-weight:800" }, `Помню: ${right} · повторим ещё: ${wrong}`),
        reward ? el("p", { class: "muted", style: "margin-top:8px;white-space:pre-line" }, reward) : null),
        el("div", { class: "btn-row", style: "margin-top:20px" },
          el("button", { class: "btn secondary", onclick: () => { S.quiz = null; refresh("games"); } }, "К играм"),
          el("button", { class: "btn", onclick: () => openCards() }, "🃏 Карточки")));
    }
    draw();
  }

  // ---------- демо-режим (открыто не из MAX) ----------

  const Demo = {
    test: null,
    state() {
      const subj = [["Математика", "math", 8, 3, 2], ["Русский язык", "rus", 6, 4, 1], ["Литература", "lit", 3, 1, 0],
        ["История", "hist", 2, 2, 1], ["Биология", "bio", 4, 0, 0], ["География", "geo", 1, 1, 0],
        ["Английский язык", "eng", 5, 2, 0], ["Информатика", "inf", 2, 1, 0]];
      const days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
      const todayIdx = (new Date().getDay() + 6) % 7;
      return {
        role: "student", bot_link: "", children: [], name: "Маша", grade: 5, mode: "coach", mode_name: "Тренер 💪",
        points: 640, unit: "⚡", level: "Знаток", to_next: 360, streak: 4, week_goal: 15, week_done: 11,
        week: days.map((_, i) => {
          const day = days[(todayIdx + 1 + i) % 7];
          return { day, solved: [3, 0, 5, 2, 6, 1, 4][i], first_try: [2, 0, 4, 2, 5, 1, 3][i], today: i === 6 };
        }),
        report: { days: 6, solved: 21, accuracy: 81 },
        achievements: [["🌱 Первый шаг", 1], ["🎓 Всё понятно", 1], ["🧭 Разведчик", 1], ["📅 Всё по плану", 1],
          ["🗓 Режим дня", 0], ["🔥 Три дня подряд", 1], ["🔥 Неделя без пропусков", 0], ["🧠 10 заданий", 1],
          ["🏅 50 заданий", 0]].map(([title, have]) => ({ title, have: !!have, how: "занимайся каждый день" })),
        subjects: subj.map(([title, id, good, learning, weak]) => ({
          id, title, good, learning, weak, level: good > 5 ? "повышенный" : "базовый", textbook: id === "math" ? "Виленкин" : null,
          now: id === "math" ? ["Обыкновенные дроби", "Сложение дробей"] : [],
          topics: [{ topic: "Обыкновенные дроби", status: "weak" }, { topic: "Натуральные числа", status: "known" },
            { topic: "Проценты", status: "learning" }].slice(0, Math.max(1, Math.min(3, good))),
        })),
        marks: [{ date: "2026-10-30", subject: "Математика", topic: "Сложение дробей", mark: 5 },
          { date: "2026-10-29", subject: "Русский язык", topic: "Безударные гласные", mark: 4 },
          { date: "2026-10-28", subject: "История", topic: "Древний Египет", mark: 3 }],
        due: [{ subject: "Математика", subject_id: "math", topic: "Обыкновенные дроби", weak: true },
          { subject: "Русский язык", subject_id: "rus", topic: "Безударные гласные", weak: false }],
        weak: [{ subject: "Математика", subject_id: "math", topic: "Обыкновенные дроби" }],
        today: { weekday: days[todayIdx], lessons: [{ id: "math", title: "Математика" }, { id: "rus", title: "Русский язык" }, { id: "eng", title: "Английский язык" }] },
        games: {
          lighthouse: { streak: 5, best: 8, freeze: true, today: false, at_risk: false, built: 4, stages: 9,
            next: { days: 7, title: "огонь маяка", left: 2 } },
          chest: { ready: !this.chestOpened, opened: !!this.chestOpened, left: 0, need: 3, collected: 7 + (this.chestOpened ? 1 : 0), total: 30, title: "Звездочёт" },
          zap: { best: { math: 14, eng: 9 }, modes: [{ id: "math", title: "🔢 Счёт" }, { id: "eng", title: "🇬🇧 Слова" }] },
          cards: { available: true, due: 4 },
        },
      };
    },
    chestOpened: false,
    zapQuestion(mode) {
      if (mode === "eng") {
        const words = [["cat", "кошка"], ["dog", "собака"], ["house", "дом"], ["sun", "солнце"], ["book", "книга"], ["water", "вода"], ["friend", "друг"], ["school", "школа"]];
        const pick = words.slice().sort(() => Math.random() - 0.5).slice(0, 4);
        const options = pick.map((w) => w[1]).sort(() => Math.random() - 0.5);
        return { text: `«${pick[0][0]}» — это…`, options, answer: options.indexOf(pick[0][1]) };
      }
      const a = 2 + Math.floor(Math.random() * 8), b = 2 + Math.floor(Math.random() * 8);
      const right = a * b;
      const set = new Set([right]);
      while (set.size < 4) set.add(right + [1, -1, 2, -2, 10, -10, a, -b][Math.floor(Math.random() * 8)]);
      const options = [...set].sort(() => Math.random() - 0.5).map(String);
      return { text: `${a} × ${b} = ?`, options, answer: options.indexOf(String(right)) };
    },
    cardsBox: [
      { id: "d1", front: "Сколько будет 3/4 от 20?", back: "15", subject: "Математика", topic: "Дроби" },
      { id: "d2", front: "Что такое подлежащее?", back: "Главный член предложения: кто? или что?", subject: "Русский язык", topic: "Члены предложения" },
      { id: "d3", front: "В каком году основан Петербург?", back: "1703", subject: "История", topic: "Пётр I" },
      { id: "d4", front: "Формула площади прямоугольника", back: "S = a · b", subject: "Математика", topic: "Площадь" },
    ],
    schedule: { "0": ["math", "rus", "eng"], "1": ["lit", "hist", "math"], "2": ["bio", "rus", "inf"] },
    async call(path, body) {
      await new Promise((r) => setTimeout(r, path.includes("train/start") ? 1200 : 150));
      if (path.startsWith("/api/state")) return this.state();
      if (path.startsWith("/api/schedule") && !body) {
        return { days: ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб"], schedule: JSON.parse(JSON.stringify(this.schedule)),
          subjects: this.state().subjects.map((s) => ({ id: s.id, title: s.title })) };
      }
      if (path === "/api/schedule") { this.schedule = body.schedule; return { ok: true, schedule: body.schedule }; }
      if (path === "/api/train/start") {
        this.test = [["Сколько будет 3/8 + 2/8?", ["5/8", "5/16", "1/8", "6/8"], "5/8", "Сложение дробей"],
          ["Какая дробь больше: 2/3 или 3/5?", ["2/3", "3/5", "они равны", "нельзя сравнить"], "2/3", "Сравнение дробей"],
          ["Найди 25% от 80.", ["20", "25", "40", "16"], "20", "Проценты"],
          ["Сократи дробь 6/9.", ["2/3", "3/6", "1/3", "6/9"], "2/3", "Сокращение дробей"],
          ["Чему равно 1,5 + 2,25?", ["3,75", "3,7", "4,75", "3,25"], "3,75", "Десятичные дроби"]];
        this.answers = [];
        return { subject: "Математика", level: "базовый",
          questions: this.test.map(([q, options, , topic]) => ({ q, options, topic })) };
      }
      if (path === "/api/train/answer") {
        const correct = body.choice === this.test[body.index][2];
        this.answers.push(correct);
        const done = this.answers.length === this.test.length;
        const right = this.answers.filter(Boolean).length;
        return { correct, done, summary: done ? { right, total: this.test.length,
          wrong: this.answers.map((ok, i) => (ok ? -1 : i)).filter((i) => i >= 0), level_change: right >= 4 ? 1 : 0,
          level_after: "повышенный", reward: `+${right * 20} ⚡ · всего ${640 + right * 20} ⚡` } : null };
      }
      if (path === "/api/chat") return { ok: true };
      if (path === "/api/zap/start") {
        this.zap = Array.from({ length: 40 }, () => this.zapQuestion(body.mode));
        return { game: 1, mode: body.mode, title: body.mode === "eng" ? "🇬🇧 Слова" : "🔢 Счёт", seconds: 60, lives: 3, questions: this.zap, best: 14 };
      }
      if (path === "/api/zap/finish") {
        let score = 0, wrong = 0;
        for (let i = 0; i < body.answers.length && wrong < 3; i++) { if (body.answers[i] === this.zap[i].answer) score++; else wrong++; }
        const rank = score >= 25 ? "Повелитель молний ⚡⚡⚡" : score >= 15 ? "Гроза задачника ⚡⚡" : score >= 8 ? "Быстрый разум ⚡" : "Разминка";
        return { score, wrong, best: Math.max(14, score), new_record: score > 14, rank, reward: score >= 5 ? `+${Math.floor(score / 5) * 10} ⚡` : "" };
      }
      if (path === "/api/chest/open") {
        this.chestOpened = true;
        const prizes = [{ kind: "sticker", emoji: "🦊", name: "Лис-хитрец", rarity: "common", duplicate: false, points_text: "", collected: 8, total: 30 },
          { kind: "sticker", emoji: "🐉", name: "Дракон знаний", rarity: "rare", duplicate: false, points_text: "", collected: 8, total: 30 },
          { kind: "fact", emoji: "💡", name: "Осьминог может пробовать еду на вкус своими щупальцами.", rarity: "fact", points_text: "+50 ⚡", collected: 7, total: 30 },
          { kind: "title", emoji: "🏅", name: "Хранитель огня", rarity: "title", points_text: "", collected: 7, total: 30 }];
        return prizes[Math.floor(Math.random() * prizes.length)];
      }
      if (path === "/api/album") {
        const all = [["🐱", "Котик-отличник", "common"], ["🐶", "Пёс-почемучка", "common"], ["🦊", "Лис-хитрец", "common"], ["🐻", "Мишка-соня", "common"],
          ["🐼", "Панда-математик", "common"], ["🐨", "Коала-мечтатель", "common"], ["🐸", "Лягушка-путешественница", "common"], ["🐧", "Пингвин-капитан", "common"],
          ["🦄", "Единорог", "rare"], ["🐉", "Дракон знаний", "rare"], ["🦖", "Ти-рекс", "rare"], ["🐳", "Кит-кругосветка", "rare"],
          ["🚀", "Ракета «Пятёрка»", "legendary"], ["👑", "Корона знаний", "legendary"]];
        return { stickers: all.map(([emoji, name, rarity], i) => ({ id: String(i), emoji, name, rarity, count: [1, 1, 2, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0, 0][i] })),
          titles: ["Звездочёт"], title: "Звездочёт", opened: 9 };
      }
      if (path === "/api/cards") return { due: this.cardsBox, due_total: 4, total: 12, learned: 5, next_due: null, decks: ["Дроби", "Пётр I"], subjects: this.state().subjects.map((s) => ({ id: s.id, title: s.title })) };
      if (path.startsWith("/api/cards/topics")) return { topics: ["Обыкновенные дроби", "Сложение дробей", "Проценты"] };
      if (path === "/api/cards/new") return { added: 8, topic: body.topic };
      if (path === "/api/cards/grade") return { ok: true, reward: "" };
      throw new Error("Нет такого действия");
    },
  };

  // ---------- старт ----------

  if (["home", "train", "games", "progress", "schedule"].includes(START)) S.tab = START;
  try { if (WA && WA.ready) WA.ready(); } catch (e) { /* необязательно */ }
  load();
})();
