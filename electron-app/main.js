const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const crypto = require("node:crypto");
const fs = require("node:fs/promises");
const { spawn } = require("node:child_process");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "..");
const backendPath = path.join(projectRoot, "file_tidier_backend.py");

let mainWindow;
let currentScan = null;

function managerStorePath() {
  return path.join(app.getPath("userData"), "file-tidier-manager-store.json");
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1320,
    height: 820,
    minWidth: 1080,
    minHeight: 680,
    backgroundColor: "#f7f9fc",
    title: "File Tidier",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));
}

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

ipcMain.handle("choose-folder", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ["openDirectory"],
  });
  if (result.canceled || result.filePaths.length === 0) {
    return null;
  }
  return result.filePaths[0];
});

ipcMain.handle("choose-reference-zip", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ["openFile"],
    filters: [{ name: "Zip files", extensions: ["zip", "cbz"] }],
  });
  if (result.canceled || result.filePaths.length === 0) {
    return null;
  }
  return result.filePaths[0];
});

ipcMain.handle("open-manager", async (_event) => {
  const window = BrowserWindow.fromWebContents(_event.sender);
  await window.loadFile(path.join(__dirname, "renderer", "manager.html"));
  window.setTitle("File Tidier - 작품 관리");
  return true;
});

ipcMain.handle("open-tidier", async (_event) => {
  const window = BrowserWindow.fromWebContents(_event.sender);
  await window.loadFile(path.join(__dirname, "renderer", "index.html"));
  window.setTitle("File Tidier");
  return true;
});

ipcMain.handle("open-external", async (_event, url) => {
  if (typeof url !== "string" || !/^https?:\/\//i.test(url)) {
    return false;
  }
  await shell.openExternal(url);
  return true;
});

ipcMain.handle("manager-store-load", async () => {
  try {
    const raw = await fs.readFile(managerStorePath(), "utf8");
    return JSON.parse(raw);
  } catch {
    return {};
  }
});

ipcMain.handle("manager-store-save", async (_event, data) => {
  try {
    await fs.mkdir(path.dirname(managerStorePath()), { recursive: true });
    await fs.writeFile(managerStorePath(), JSON.stringify(data || {}), "utf8");
    return true;
  } catch {
    return false;
  }
});

ipcMain.handle("app-integrity", async () => {
  const files = [
    "file_tidier_core.py",
    "file_tidier_backend.py",
    "electron-app/main.js",
    "electron-app/preload.js",
    "electron-app/renderer/index.html",
    "electron-app/renderer/renderer.js",
    "electron-app/renderer/styles.css",
    "electron-app/renderer/manager.html",
    "electron-app/renderer/manager.js",
    "electron-app/renderer/manager.css",
  ];
  const digest = crypto.createHash("sha256");
  const details = [];
  for (const relativePath of files) {
    const absolutePath = path.join(projectRoot, relativePath);
    try {
      const data = await fs.readFile(absolutePath);
      const fileHash = crypto.createHash("sha256").update(data).digest("hex");
      digest.update(relativePath);
      digest.update(fileHash);
      details.push({ path: relativePath, hash: fileHash, ok: true });
    } catch (error) {
      details.push({ path: relativePath, hash: "", ok: false, error: error.message });
    }
  }
  return {
    ok: true,
    appPath: projectRoot,
    hash: digest.digest("hex"),
    files: details,
    generatedAt: new Date().toISOString(),
  };
});

ipcMain.handle("cancel-scan", async () => {
  if (currentScan) {
    currentScan.kill();
    currentScan = null;
    return true;
  }
  return false;
});

ipcMain.handle("quarantine-files", async (_event, options) => {
  const args = [
    backendPath,
    "quarantine",
    "--folder",
    options.folder,
    "--paths",
    JSON.stringify(options.paths || []),
  ];

  return new Promise((resolve) => {
    const child = spawn("python", args, {
      cwd: projectRoot,
      windowsHide: true,
    });
    let timedOut = false;
    let stdout = "";
    let stderr = "";
    const timeout = setTimeout(() => {
      timedOut = true;
      child.kill();
    }, 20000);
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("close", (code) => {
      clearTimeout(timeout);
      if (timedOut) {
        resolve({
          ok: false,
          error: "분석이 20초를 넘어서 자동 중단했습니다. 큰 zip은 빠른 목록 비교만 쓰거나, 나중에 별도 정밀검사로 빼는 게 좋습니다.",
        });
        return;
      }
      try {
        const payload = JSON.parse(stdout.trim() || "{}");
        if (!payload.ok && stderr) {
          payload.error = `${payload.error || "실패"}\n${stderr}`;
        }
        resolve(payload);
      } catch (error) {
        resolve({
          ok: false,
          error: `격리 결과를 읽지 못했습니다. code=${code}\n${stderr || error.message}`,
        });
      }
    });
  });
});

ipcMain.handle("compare-items", async (_event, options) => {
  const args = [
    backendPath,
    "compare-items",
    "--left",
    options.left,
    "--right",
    options.right,
  ];

  return new Promise((resolve) => {
    const child = spawn("python", args, {
      cwd: projectRoot,
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("close", (code) => {
      try {
        const payload = JSON.parse(stdout.trim() || "{}");
        if (!payload.ok && stderr) {
          payload.error = `${payload.error || "compare failed"}\n${stderr}`;
        }
        resolve(payload);
      } catch (error) {
        resolve({
          ok: false,
          error: `comparison result parse failed: code=${code}\n${stderr || error.message}`,
        });
      }
    });
  });
});

ipcMain.handle("apply-rename", async (_event, options) => {
  const args = [
    backendPath,
    "apply-rename",
    "--folder",
    options.folder,
    "--query",
    options.query || "",
    "--limit",
    String(options.limit || 2000),
    options.recursive ? "--recursive" : "--no-recursive",
    options.includeZip ? "--include-zip" : "--no-include-zip",
    "--min-size-kb",
    String(options.minSizeKb ?? 4),
    "--allowed-extensions",
    options.allowedExtensions || "",
    "--find",
    options.rename?.find || "",
    "--replace",
    options.rename?.replace || "",
    "--position",
    options.rename?.position || "front",
    options.rename?.regex ? "--regex" : "--no-regex",
    "--prefix",
    options.rename?.prefix || "",
    "--suffix",
    options.rename?.suffix || "",
    "--case",
    options.rename?.caseMode || "keep",
    "--start-number",
    String(options.rename?.startNumber ?? -1),
    "--padding",
    String(options.rename?.padding ?? 3),
    "--author",
    options.rename?.author || "",
    "--author-pattern",
    options.rename?.authorPattern || "prefix",
    options.rename?.stripCopySuffix ? "--strip-copy-suffix" : "--no-strip-copy-suffix",
    options.rename?.autoAuthor ? "--auto-author" : "--no-auto-author",
    options.rename?.normalizeTitleFormat ? "--normalize-title-format" : "--no-normalize-title-format",
  ];
  return new Promise((resolve) => {
    const child = spawn("python", args, {
      cwd: projectRoot,
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("close", (code) => {
      try {
        const payload = JSON.parse(stdout.trim() || "{}");
        if (!payload.ok && stderr) {
          payload.error = `${payload.error || "rename failed"}\n${stderr}`;
        }
        resolve(payload);
      } catch (error) {
        resolve({
          ok: false,
          error: `rename result parse failed: code=${code}\n${stderr || error.message}`,
        });
      }
    });
  });
});

ipcMain.handle("web-covers", async (_event, options) => {
  const args = [
    backendPath,
    "web-covers",
    "--folder",
    options.folder,
    "--query",
    options.query || "",
    options.recursive ? "--recursive" : "--no-recursive",
    "--max-items",
    String(options.maxItems || 20),
  ];

  return new Promise((resolve) => {
    const child = spawn("python", args, {
      cwd: projectRoot,
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    let stderrPending = "";
    const handleStderrLine = (line) => {
      const text = line.trim();
      if (!text) {
        return;
      }
      try {
        const payload = JSON.parse(text);
        if (payload.kind === "progress") {
          _event.sender.send("scan-progress", payload);
          return;
        }
      } catch {
        // Keep regular stderr for final error display.
      }
      stderr += `${line}\n`;
    };
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderrPending += chunk.toString("utf8");
      const lines = stderrPending.split(/\r?\n/);
      stderrPending = lines.pop() || "";
      lines.forEach(handleStderrLine);
    });
    child.on("close", (code) => {
      if (stderrPending) {
        handleStderrLine(stderrPending);
      }
      try {
        const payload = JSON.parse(stdout.trim() || "{}");
        if (!payload.ok && stderr) {
          payload.error = `${payload.error || "web cover search failed"}\n${stderr}`;
        }
        resolve(payload);
      } catch (error) {
        resolve({
          ok: false,
          error: `web cover result parse failed: code=${code}\n${stderr || error.message}`,
        });
      }
    });
  });
});

const RESULT_FILE_KIND = "file-tidier-result";
const RESULT_FILE_VERSION = 1;
const RESULT_FILE_FILTERS = [{ name: "File Tidier 결과", extensions: ["json"] }];

function resultStamp() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return (
    `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}` +
    `-${pad(now.getHours())}${pad(now.getMinutes())}`
  );
}

// 스캔 결과를 파일로 남긴다. 백엔드의 --save-result 와 같은 형식이라
// CLI로 저장한 것과 서로 열린다.
ipcMain.handle("save-scan-result", async (_event, options) => {
  const mode = options?.mode || "scan";
  const payload = options?.payload;
  if (!payload) {
    return { ok: false, error: "저장할 결과가 없습니다" };
  }
  const chosen = await dialog.showSaveDialog(mainWindow, {
    title: "스캔 결과 저장",
    defaultPath: `filetidier-${mode}-${resultStamp()}.json`,
    filters: RESULT_FILE_FILTERS,
  });
  if (chosen.canceled || !chosen.filePath) {
    return { ok: false, cancelled: true };
  }
  const document = {
    kind: RESULT_FILE_KIND,
    version: RESULT_FILE_VERSION,
    command: mode,
    savedAt: new Date().toISOString().slice(0, 19).replace("T", " "),
    options: options?.options || {},
    truncated: Number(payload.total || 0) > Number(payload.shown || 0),
    payload,
  };
  try {
    await fs.writeFile(chosen.filePath, JSON.stringify(document), "utf-8");
  } catch (error) {
    return { ok: false, error: String(error?.message || error) };
  }
  return { ok: true, path: chosen.filePath, truncated: document.truncated };
});

ipcMain.handle("load-scan-result", async () => {
  const chosen = await dialog.showOpenDialog(mainWindow, {
    title: "저장한 결과 열기",
    properties: ["openFile"],
    filters: RESULT_FILE_FILTERS,
  });
  if (chosen.canceled || !chosen.filePaths?.length) {
    return { ok: false, cancelled: true };
  }
  const target = chosen.filePaths[0];
  let document;
  try {
    document = JSON.parse(await fs.readFile(target, "utf-8"));
  } catch (error) {
    return { ok: false, error: `읽을 수 없습니다: ${String(error?.message || error)}` };
  }
  if (!document || document.kind !== RESULT_FILE_KIND || !document.payload) {
    return { ok: false, error: "File Tidier 결과 파일이 아닙니다" };
  }
  return {
    ok: true,
    path: target,
    command: document.command || "",
    savedAt: document.savedAt || "",
    options: document.options || {},
    truncated: Boolean(document.truncated),
    payload: document.payload,
  };
});

ipcMain.handle("scan", async (_event, options) => {
  if (currentScan) {
    currentScan.kill();
    currentScan = null;
  }

  const args = [
    backendPath,
    options.mode,
    "--folder",
    options.folder,
    "--query",
    options.query || "",
    "--limit",
    String(options.limit || 2000),
    options.recursive ? "--recursive" : "--no-recursive",
    options.includeZip ? "--include-zip" : "--no-include-zip",
  ];
  if (options.minSizeKb !== undefined) {
    args.push("--min-size-kb", String(options.minSizeKb));
  }
  if (options.allowedExtensions !== undefined) {
    args.push("--allowed-extensions", options.allowedExtensions || "");
  }
  if (options.mode === "catalog") {
    args.push(options.withThumbnails ? "--with-thumbnails" : "--no-with-thumbnails");
    args.push("--thumbnail-limit", String(options.thumbnailLimit ?? 0));
  }
  if (options.referenceZip) {
    args.push("--reference-zip", options.referenceZip);
  }
  if (options.mode === "rename-preview") {
    args.push(
      "--find",
      options.rename?.find || "",
      "--replace",
      options.rename?.replace || "",
      "--position",
      options.rename?.position || "front",
      options.rename?.regex ? "--regex" : "--no-regex",
      "--prefix",
      options.rename?.prefix || "",
      "--suffix",
      options.rename?.suffix || "",
      "--case",
      options.rename?.caseMode || "keep",
      "--start-number",
      String(options.rename?.startNumber ?? -1),
      "--padding",
      String(options.rename?.padding ?? 3),
      "--author",
      options.rename?.author || "",
      "--author-pattern",
      options.rename?.authorPattern || "prefix",
      options.rename?.stripCopySuffix ? "--strip-copy-suffix" : "--no-strip-copy-suffix",
      options.rename?.autoAuthor ? "--auto-author" : "--no-auto-author",
      options.rename?.normalizeTitleFormat ? "--normalize-title-format" : "--no-normalize-title-format",
    );
  }

  return new Promise((resolve) => {
    const child = spawn("python", args, {
      cwd: projectRoot,
      windowsHide: true,
      env: {
        ...process.env,
        FILE_TIDIER_CACHE_DIR: path.join(app.getPath("userData"), "cache"),
      },
    });
    currentScan = child;

    let stdout = "";
    let stderr = "";
    let stderrPending = "";

    const handleStderrLine = (line) => {
      const text = line.trim();
      if (!text) {
        return;
      }
      try {
        const payload = JSON.parse(text);
        if (payload.kind === "progress") {
          _event.sender.send("scan-progress", payload);
          return;
        }
      } catch {
        // Keep regular stderr text for the final error message.
      }
      stderr += `${line}\n`;
    };

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderrPending += chunk.toString("utf8");
      const lines = stderrPending.split(/\r?\n/);
      stderrPending = lines.pop() || "";
      lines.forEach(handleStderrLine);
    });
    child.on("close", (code, signal) => {
      currentScan = null;
      if (stderrPending) {
        handleStderrLine(stderrPending);
        stderrPending = "";
      }
      if (signal) {
        resolve({ ok: false, cancelled: true, error: "작업을 중지했습니다." });
        return;
      }
      try {
        const payload = JSON.parse(stdout.trim() || "{}");
        if (!payload.ok && stderr) {
          payload.error = `${payload.error || "실패"}\n${stderr}`;
        }
        resolve(payload);
      } catch (error) {
        resolve({
          ok: false,
          error: `결과를 읽지 못했습니다. code=${code}\n${stderr || error.message}`,
        });
      }
    });
  });
});
