// カード動画のスライドHTMLを、headless Chrome 1つで順に1920x1080のPNGへ撮る。
// 使い方: node render_slides.mjs jobs.json [profileDir]
//   jobs.json = [{"html": "/abs/slide.html", "png": "/abs/slide.png"}, ...]
//   はみ出したスライドのHTMLパスを jobs.json.overflow.json へ書く
// 外部へは何も取りに行かない（スライドはローカルのHTMLと画像だけ）。
import { spawn } from "node:child_process";
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9341;
const WIDTH = 1920;
const HEIGHT = 1080;

const jobsPath = process.argv[2];
const jobs = JSON.parse(readFileSync(jobsPath, "utf8"));
// Chromeのプロファイルは呼び出し側の作業フォルダへ置く（終わったら作業フォルダごと消える）
const profile = process.argv[3] || mkdtempSync(join(tmpdir(), "card-video-chrome-"));
const chrome = spawn(CHROME, [
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  "--hide-scrollbars", "--allow-file-access-from-files", `--user-data-dir=${profile}`,
  `--remote-debugging-port=${PORT}`, "about:blank",
], { stdio: "ignore" });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

let target;
for (let i = 0; i < 100 && !target; i++) {
  try {
    const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
    target = list.find((t) => t.type === "page");
  } catch {}
  if (!target) await sleep(100);
}
if (!target) {
  chrome.kill();
  throw new Error("headless Chrome に接続できませんでした");
}

const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve) => ws.addEventListener("open", resolve));
let seq = 0;
const pending = new Map();
const waiters = [];
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    if (msg.error) reject(new Error(msg.error.message));
    else resolve(msg.result);
  } else if (msg.method) {
    for (const w of waiters.splice(0)) w(msg);
  }
});
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++seq;
  pending.set(id, { resolve, reject });
  ws.send(JSON.stringify({ id, method, params }));
});
const waitFor = (method) => new Promise((resolve) => {
  const check = (msg) => (msg.method === method ? resolve(msg) : waiters.push(check));
  waiters.push(check);
});

try {
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", {
    width: WIDTH, height: HEIGHT, deviceScaleFactor: 1, mobile: false,
  });
  // 1枚目は撮る前に一度空の画面を描かせておく
  await send("Page.captureScreenshot", { format: "png" });
  const overflow = [];
  for (const job of jobs) {
    const loaded = waitFor("Page.loadEventFired");
    await send("Page.navigate", { url: pathToFileURL(job.html).href });
    await loaded;
    // 画像の展開と、画面サイズを変えた直後の描き直しを待つ。待たないと最初の1枚が
    // 前の大きさのまま敷き詰められた絵になる（2026-10-26に起きた）。
    await send("Runtime.evaluate", {
      expression: "document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))",
      awaitPromise: true,
    });
    // 画面からはみ出した文字は黙って切れるので、はみ出しを数えて呼び出し元へ返す
    const over = await send("Runtime.evaluate", {
      returnByValue: true,
      expression: `[...document.body.querySelectorAll("*")].filter((el) => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return false;
        const clipped = getComputedStyle(el).overflow !== "visible" && el.scrollHeight > el.clientHeight + 2;
        return r.bottom > ${HEIGHT} + 1 || r.right > ${WIDTH} + 1 || clipped;
      }).length`,
    });
    if (over.result.value) overflow.push(job.html);
    const shot = await send("Page.captureScreenshot", {
      format: "png", clip: { x: 0, y: 0, width: WIDTH, height: HEIGHT, scale: 1 },
    });
    writeFileSync(job.png, Buffer.from(shot.data, "base64"));
  }
  writeFileSync(`${jobsPath}.overflow.json`, JSON.stringify(overflow));
} finally {
  ws.close();
  chrome.kill();
}
