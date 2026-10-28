// 声の聞きくらべページ。音声と一覧（samples.json）は scripts/card_video/voice_samples.py が作り、
// nginx が ../media/card-video/voice-samples/ で配る。このページは読むだけで、サーバーへ書き込まない。
(() => {
  "use strict";

  const MEDIA = "../media/card-video/voice-samples/";
  const STORE_KEY = "cardVideoVoices:v1";
  const RATES = [1, 1.25, 1.5];
  const $ = (id) => document.getElementById(id);

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

  let rate = RATES.includes(loadStore().rate) ? loadStore().rate : 1;
  let keptOnly = false;
  const players = [];

  function render(data) {
    const kept = loadStore().kept || {};
    const groups = [];
    data.samples.forEach((sample) => {
      let group = groups.find((g) => g.name === sample.group);
      if (!group) groups.push(group = { name: sample.group, samples: [] });
      group.samples.push(sample);
    });
    players.length = 0;

    const sections = groups.map((group) => {
      const cards = group.samples
        .filter((sample) => !keptOnly || kept[sample.id])
        .map((sample) => {
          const audio = el("audio", {
            controls: "", preload: "none",
            src: `${MEDIA}${encodeURIComponent(sample.file)}?v=${encodeURIComponent(data.generatedAt || "")}`,
          });
          const box = el("article", { class: `voice${kept[sample.id] ? " kept" : ""}` },
            el("div", { class: "head" }, el("strong", {}, sample.label), el("span", { class: "note" }, `${Math.round(sample.seconds)}秒`)),
            el("div", { class: "note" }, sample.note),
            audio,
            el("div", { class: "foot" },
              el("span", {}, sample.credit || ""),
              el("button", {
                type: "button", class: "keep",
                onclick: (event) => {
                  const next = { ...(loadStore().kept || {}) };
                  if (next[sample.id]) delete next[sample.id];
                  else next[sample.id] = true;
                  saveStore({ kept: next });
                  // 描き直すと再生中の音が止まるので、その場で印だけ変える
                  box.classList.toggle("kept", Boolean(next[sample.id]));
                  event.currentTarget.textContent = next[sample.id] ? "✓ 残した" : "候補に残す";
                },
              }, kept[sample.id] ? "✓ 残した" : "候補に残す")));
          // 同時に2つ鳴らない。押した声だけを流す
          audio.addEventListener("play", () => {
            players.forEach((other) => { if (other !== audio) other.pause(); });
            audio.playbackRate = rate;
            box.classList.add("playing");
          });
          audio.addEventListener("pause", () => box.classList.remove("playing"));
          players.push(audio);
          return box;
        });
      if (!cards.length) return "";
      return el("section", { class: "group" }, el("h2", {}, group.name), el("div", { class: "voices" }, cards));
    });

    const script = el("details", { class: "script" }, el("summary", {}, "読んでいる文章を見る"),
      data.text.map((paragraph) => el("p", {}, paragraph)));
    $("app").replaceChildren(script, ...sections);
  }

  function renderBar(data) {
    const buttons = RATES.map((value) => el("button", {
      type: "button", "aria-pressed": String(value === rate),
      onclick: () => {
        rate = value;
        saveStore({ rate });
        players.forEach((audio) => { audio.playbackRate = rate; });
        buttons.forEach((b, i) => b.setAttribute("aria-pressed", String(RATES[i] === rate)));
      },
    }, `${value}倍`));
    $("rates").replaceChildren(...buttons);
    $("kept-only").addEventListener("click", (event) => {
      keptOnly = !keptOnly;
      event.currentTarget.setAttribute("aria-pressed", String(keptOnly));
      render(data);
    });
  }

  fetch(`${MEDIA}samples.json`, { cache: "no-store" })
    .then((response) => {
      if (!response.ok) throw new Error(String(response.status));
      return response.json();
    })
    .then((data) => { renderBar(data); render(data); })
    .catch(() => {
      $("app").replaceChildren(el("p", { class: "status" },
        "まだサンプルがありません。Macで scripts/card_video/voice_samples.py を実行すると、ここに並びます。"));
    });
})();
