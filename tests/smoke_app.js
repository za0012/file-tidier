// 앱을 실제로 띄워서 스캔을 돌려 보는 연기 시험.
//
//   node tests/smoke_app.js "E:/내 소설들/정리X"
//
// 단위 시험(tests/test_scan.py)은 가짜 레코드로 함수만 확인한다. 그것만으로는
// 놓치는 것이 있었다 - 백엔드 옵션을 만들어 놓고 UI 에 연결하지 않은 것,
// 복구된 파일의 깨진 수정시각에 스캔이 통째로 죽던 것 둘 다 사용자가 먼저
// 발견했다. 여기서는 사람이 쓰는 경로 그대로(렌더러 -> main -> 백엔드) 돌린다.
const { spawn } = require("child_process");
const path = require("path");

const FOLDER = process.argv[2] || "E:/내 소설들/정리X";
const ROOT = path.resolve(__dirname, "..", "electron-app");
const ELECTRON = require(path.join(ROOT, "node_modules", "electron"));
const PORT = 9223;

let pass = 0, fail = 0;
const check = (label, ok, extra = "") => {
  if (ok) { pass++; console.log("  PASS " + label); }
  else { fail++; console.log("  FAIL " + label + " " + extra); }
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function targets() {
  const res = await fetch(`http://localhost:${PORT}/json/list`);
  return (await res.json()).filter((t) => t.type === "page");
}

async function evaluate(target, expression) {
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let id = 0; const pending = new Map();
  const send = (method, params = {}) =>
    new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); }
  });
  await new Promise((res, rej) => { ws.addEventListener("open", res); ws.addEventListener("error", rej); });
  const out = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  ws.close();
  if (out && out.exceptionDetails) throw new Error(JSON.stringify(out.exceptionDetails).slice(0, 200));
  return out && out.result && out.result.value;
}

(async () => {
  const app = spawn(ELECTRON, [ROOT, `--remote-debugging-port=${PORT}`], { stdio: "ignore" });
  try {
    for (let i = 0; i < 60; i++) {
      try { await targets(); break; } catch { await sleep(1000); }
    }
    const [main] = await targets();
    check("앱이 뜬다", !!main);
    if (!main) return;

    // 포트가 열린 것과 화면이 그려진 것은 다르다. 기다리지 않으면 요소가
    // 아직 없어서 "칸이 없다" 고 잘못 보고한다.
    for (let i = 0; i < 60; i++) {
      const ready = await evaluate(main, "document.readyState === 'complete' && !!document.getElementById('limitInput')");
      if (ready) break;
      await sleep(500);
    }

    check("읽을 최대 칸이 있다", await evaluate(main, "!!document.getElementById('maxFilesInput')"));
    check("표시 최대 칸이 있다", await evaluate(main, "!!document.getElementById('limitInput')"));

    // 사람이 쓰는 경로 그대로: 화면 값 -> IPC -> 백엔드
    const raw = await evaluate(main, `
      (async () => {
        document.getElementById('maxFilesInput').value = '100';
        const p = await window.fileTidier.scan({
          mode: 'duplicates-comprehensive',
          folder: ${JSON.stringify(FOLDER)},
          query: '', limit: 5,
          maxFiles: Math.max(0, Number.parseInt(document.getElementById('maxFilesInput').value, 10) || 0),
          minSizeKb: 4, recursive: false, includeZip: false,
          allowedExtensions: 'txt, epub, zip, cbz', excludeFolders: ''
        });
        return JSON.stringify({ ok: p.ok, error: p.error || null,
          scanned: (p.analysisStats || {}).scannedFiles || 0,
          read: (p.cache || {}).misses || 0, partial: p.partial || null });
      })()
    `);
    const r = JSON.parse(raw);
    check("스캔이 실패하지 않는다", r.ok === true, String(r.error || "").slice(0, 90));
    check("파일을 실제로 훑었다", r.scanned > 0, `(${r.scanned}개)`);
    check("읽을 최대가 지켜진다", r.read <= 100, `(${r.read}개 읽음)`);
    check("제한에 걸리면 부분으로 보고한다", !!r.partial, JSON.stringify(r.partial));
    if (r.partial) {
      check("남은 개수를 알려준다", r.partial.remaining > 0, JSON.stringify(r.partial));
    }
  } finally {
    app.kill();
  }
  console.log(`\nPASS ${pass}  FAIL ${fail}`);
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.log("  오류:", e.message); process.exit(1); });
