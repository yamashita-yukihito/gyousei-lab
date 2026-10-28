// カード動画の再生ページ。動画と目次（manifest.json）は scripts/card_video/build_card_video.py が作り、
// nginx が ../media/card-video/ で配る。このページは読むだけで、回答記録もサーバーへの書き込みもしない。
(() => {
  "use strict";

  const MEDIA = "../media/card-video/";
  const STORE_KEY = "cardVideo:v1";
  const RATES = [1, 1.25, 1.5, 1.75, 2];
  const SPECIAL = "特集"; // 全カードを1回ずつ流す通しの回とは別に置く回。「次の回」では飛ばす
  const $ = (id) => document.getElementById(id);

  // 続きの位置・再生速度・見終わった回だけをブラウザに覚える。読めなくても動く。
  function loadStore() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch { return {}; }
  }
  function saveStore(patch) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify({ ...loadStore(), ...patch })); } catch {}
  }

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value);
    });
    children.flat().forEach((child) => node.append(child instanceof Node ? child : String(child)));
    return node;
  }

  function clock(seconds) {
    const s = Math.max(0, Math.floor(seconds));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }

  function minutes(seconds) {
    return `${Math.round(seconds / 60)}分`;
  }

  function chapterTitle(chapter) {
    return chapter.kind === "card" ? `第${chapter.number}問` : chapter.label;
  }

  // 一覧では科目名の見出しの下に並べるので、題名の頭の科目名を外す
  function shortTitle(episode) {
    const head = `${episode.group} `;
    return episode.title.startsWith(head) ? episode.title.slice(head.length) : episode.title;
  }

  let manifest = null;
  let current = null;

  const series = () => manifest.episodes.filter((e) => e.group !== SPECIAL);

  function neighbor(episode, dir) {
    if (!episode || episode.group === SPECIAL) return null;
    const list = series();
    return list[list.indexOf(episode) + dir] || null;
  }

  function markWatched(episode) {
    const watched = loadStore().watched || {};
    if (watched[episode.id]) return;
    watched[episode.id] = new Date().toISOString().slice(0, 10);
    saveStore({ watched });
    renderEpisodes();
  }

  function renderEpisodes() {
    const store = loadStore();
    const watched = store.watched || {};
    const positions = store.positions || {};
    const groups = [];
    manifest.episodes.forEach((episode) => {
      let group = groups.find((g) => g.name === episode.group);
      if (!group) groups.push(group = { name: episode.group, episodes: [] });
      group.episodes.push(episode);
    });
    const list = series();
    const done = list.filter((e) => watched[e.id]).length;
    const totalCards = list.reduce((sum, e) => sum + e.cardCount, 0);
    const totalSeconds = list.reduce((sum, e) => sum + e.durationSeconds, 0);
    $("progress").textContent =
      `全${list.length}本・${totalCards}枚・合計${Math.floor(totalSeconds / 3600)}時間${Math.round((totalSeconds % 3600) / 60)}分。見終わった回 ${done}／${list.length}本`;

    $("episodes").replaceChildren(...groups.map((group) => el("section", { class: "episode-group" },
      el("h3", {}, group.name || "その他"),
      el("div", { class: "episode-buttons" }, group.episodes.map((episode) => {
        const resume = positions[episode.id];
        let state = "";
        if (watched[episode.id]) state = "✓ 見た";
        else if (resume && resume.builtAt === episode.builtAt && resume.time > 15) state = `途中 ${clock(resume.time)}`;
        return el("button", {
          type: "button",
          class: watched[episode.id] ? "watched" : "",
          "aria-pressed": String(current && current.id === episode.id),
          onclick: () => { openEpisode(episode.id, true); $("main-content").scrollIntoView({ behavior: "smooth" }); },
        }, el("strong", {}, shortTitle(episode)),
        el("small", {}, `${episode.cardCount}枚・${minutes(episode.durationSeconds)}`, state ? el("span", { class: "state" }, state) : ""));
      })))));
  }

  function openEpisode(id, autoplay) {
    const episode = manifest.episodes.find((e) => e.id === id)
      || series().find((e) => !(loadStore().watched || {})[e.id])
      || manifest.episodes[0];
    current = episode;
    saveStore({ episode: episode.id });
    renderEpisodes();

    const store = loadStore();
    const video = el("video", {
      controls: "", preload: "metadata", playsinline: "",
      src: `${MEDIA}${encodeURIComponent(episode.file)}?v=${encodeURIComponent(episode.builtAt || "")}`,
    });
    const rate = RATES.includes(store.rate) ? store.rate : 1;
    const rateButtons = RATES.map((value) => el("button", {
      type: "button", "aria-pressed": String(value === rate),
      onclick: () => {
        video.playbackRate = value;
        saveStore({ rate: value });
        rateButtons.forEach((b, i) => b.setAttribute("aria-pressed", String(RATES[i] === value)));
      },
    }, `${value}倍`));

    // 家事の合間に流しっぱなしにできるよう、既定で次の回へ進む
    const autoNext = el("input", { type: "checkbox", id: "auto-next" });
    autoNext.checked = store.autoNext !== false;
    autoNext.addEventListener("change", () => saveStore({ autoNext: autoNext.checked }));

    const cards = episode.chapters.filter((c) => c.kind === "card");
    const outro = episode.chapters.find((c) => c.kind === "outro");
    const seek = (seconds) => { video.currentTime = seconds + 0.01; video.play().catch(() => {}); };
    const step = (dir) => {
      const t = video.currentTime;
      const target = dir > 0
        ? cards.find((c) => c.start > t + 0.5)
        : [...cards].reverse().find((c) => c.start < t - 3);
      if (target) seek(target.start);
      else if (dir < 0) seek(0);
    };

    const items = episode.chapters.map((chapter) => {
      const body = el("div", { class: "body" }, el("strong", {}, chapterTitle(chapter)));
      if (chapter.kind === "card") {
        body.append(chapter.label, " ");
        body.append(el("a", {
          href: `../?cardId=${encodeURIComponent(chapter.cardId)}#study`,
          onclick: (event) => event.stopPropagation(),
        }, "カードを開く"));
      }
      return el("li", { onclick: () => seek(chapter.start) }, el("span", { class: "time" }, clock(chapter.start)), body);
    });
    const list = el("ol", { class: "chapters", "aria-label": "目次" }, items);

    const solve = el("a", {
      class: "solve-link",
      href: `../?cardIds=${episode.cardIds.map(encodeURIComponent).join(",")}&from=video#study`,
    }, `この回の${episode.cardCount}枚をアプリで解く`);

    const prev = neighbor(episode, -1);
    const next = neighbor(episode, 1);
    const navButton = (target, label) => {
      const button = el("button", { type: "button", onclick: () => target && openEpisode(target.id, true) }, label);
      button.disabled = !target;
      return button;
    };
    const nav = el("div", { class: "now-playing" },
      el("div", {}, el("small", {}, episode.group || ""), el("h2", {}, shortTitle(episode)),
        el("p", {}, episode.description || "", episode.voiceCredit ? `　声：${episode.voiceCredit}` : "")),
      el("div", { class: "group" }, navButton(prev, "← 前の回"), navButton(next, "次の回 →")));

    $("app").replaceChildren(nav, el("div", { class: "player" },
      el("div", { class: "screen" }, video,
        el("div", { class: "controls" },
          el("div", { class: "group" },
            el("button", { type: "button", onclick: () => step(-1) }, "← 前の問題"),
            el("button", { type: "button", onclick: () => step(1) }, "次の問題 →"),
            el("span", { class: "label" }, "速さ"), rateButtons),
          el("div", { class: "group" },
            next ? el("label", { class: "auto-next", for: "auto-next" }, autoNext, "終わったら次の回へ") : "",
            solve))),
      list));

    let lastSaved = 0;
    video.addEventListener("loadedmetadata", () => {
      video.playbackRate = rate;
      const resume = (loadStore().positions || {})[episode.id];
      if (resume && resume.builtAt === episode.builtAt && resume.time < video.duration - 5) video.currentTime = resume.time;
      if (autoplay) video.play().catch(() => {});
    });
    video.addEventListener("timeupdate", () => {
      const t = video.currentTime;
      let index = -1;
      episode.chapters.forEach((c, i) => { if (c.start <= t + 0.05) index = i; });
      items.forEach((item, i) => item.classList.toggle("now", i === index));
      if (outro && t >= outro.start) markWatched(episode);
      if (Math.abs(t - lastSaved) > 5) {
        lastSaved = t;
        const positions = loadStore().positions || {};
        positions[episode.id] = { time: t, builtAt: episode.builtAt };
        saveStore({ positions });
      }
    });
    video.addEventListener("ended", () => {
      markWatched(episode);
      const positions = loadStore().positions || {};
      delete positions[episode.id];
      saveStore({ positions });
      if (next && autoNext.checked) openEpisode(next.id, true);
    });
    video.addEventListener("ratechange", () => {
      rateButtons.forEach((b, i) => b.setAttribute("aria-pressed", String(RATES[i] === video.playbackRate)));
    });
  }

  fetch(`${MEDIA}manifest.json`, { cache: "no-store" })
    .then((response) => {
      if (!response.ok) throw new Error(String(response.status));
      return response.json();
    })
    .then((data) => {
      manifest = data;
      if (!manifest.episodes || !manifest.episodes.length) throw new Error("empty");
      openEpisode(loadStore().episode, false);
    })
    .catch(() => {
      $("app").replaceChildren(el("p", { class: "status" },
        "まだ動画がありません。Macで scripts/card_video/build_card_video.py を実行すると、ここに並びます。"));
    });
})();
