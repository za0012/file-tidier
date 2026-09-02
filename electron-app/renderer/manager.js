const STORAGE_KEY = "fileTidier.manager.v1";
const SUPPORTED_EXTENSIONS = new Set([".epub", ".txt", ".zip", ".cbz", ".pdf", ".html", ".htm", ".xhtml", ".xml"]);

const state = {
  folder: "",
  items: [],
  works: [],
  metadata: {},
  rejectList: [],
  trackList: [],
  selectedReviewTitle: "",
  cardView: false,
  listFilter: "all",
  shownCount: 0,
  listObserver: null,
  listSort: "title",
  scanStartedAt: null,
  scanTimer: null,
  latestProgress: null,
  saveTimer: null,
};

const els = {
  status: document.querySelector("#managerStatus"),
  viewTitle: document.querySelector("#viewTitle"),
  chooseFolder: document.querySelector("#chooseLibraryFolder"),
  folderText: document.querySelector("#libraryFolderText"),
  scan: document.querySelector("#scanLibraryButton"),
  scanCovers: document.querySelector("#scanCoversButton"),
  scanAllCovers: document.querySelector("#scanAllCoversButton"),
  cancelScan: document.querySelector("#cancelLibraryScanButton"),
  recursive: document.querySelector("#managerRecursive"),
  includeZip: document.querySelector("#managerIncludeZip"),
  librarySearch: document.querySelector("#librarySearch"),
  libraryMeta: document.querySelector("#libraryMeta"),
  libraryList: document.querySelector("#libraryList"),
  toggleCardView: document.querySelector("#toggleCardView"),
  coverNotice: document.querySelector("#coverNotice"),
  librarySort: document.querySelector("#librarySort"),
  dashExtensions: document.querySelector("#dashExtensions"),
  dashTodo: document.querySelector("#dashTodo"),
  dashAuthors: document.querySelector("#dashAuthors"),
  dashBiggest: document.querySelector("#dashBiggest"),
  metricWorksNote: document.querySelector("#metricWorksNote"),
  metricFilesNote: document.querySelector("#metricFilesNote"),
  metricDuplicatesNote: document.querySelector("#metricDuplicatesNote"),
  metricRejectsNote: document.querySelector("#metricRejectsNote"),
  chipGroup: document.querySelector(".chip-group"),
  exportData: document.querySelector("#exportDataButton"),
  importData: document.querySelector("#importDataButton"),
  dataPortResult: document.querySelector("#dataPortResult"),
  coverNoticeButton: document.querySelector("#coverNoticeButton"),
  latestList: document.querySelector("#latestList"),
  buildLatest: document.querySelector("#buildLatestButton"),
  reviewTargetList: document.querySelector("#reviewTargetList"),
  reviewTitle: document.querySelector("#reviewTitle"),
  reviewMemo: document.querySelector("#reviewMemo"),
  openReviewSearch: document.querySelector("#openReviewSearch"),
  saveReviewMemo: document.querySelector("#saveReviewMemo"),
  tasteSummary: document.querySelector("#tasteSummary"),
  recommendCandidates: document.querySelector("#recommendCandidates"),
  recommendPrompt: document.querySelector("#recommendPrompt"),
  buildPrompt: document.querySelector("#buildPromptButton"),
  globalMemo: document.querySelector("#globalMemo"),
  saveGlobalMemo: document.querySelector("#saveGlobalMemo"),
  rejectList: document.querySelector("#rejectList"),
  saveRejectList: document.querySelector("#saveRejectList"),
  rejectMatches: document.querySelector("#rejectMatches"),
  trackList: document.querySelector("#trackList"),
  saveTrackList: document.querySelector("#saveTrackList"),
  trackMatches: document.querySelector("#trackMatches"),
  externalWorkList: document.querySelector("#externalWorkList"),
  compareExternalList: document.querySelector("#compareExternalList"),
  externalCompareResult: document.querySelector("#externalCompareResult"),
  webCoverLimit: document.querySelector("#webCoverLimit"),
  fetchWebCovers: document.querySelector("#fetchWebCovers"),
  webCoverResult: document.querySelector("#webCoverResult"),
  buildFolderReport: document.querySelector("#buildFolderReport"),
  folderReport: document.querySelector("#folderReport"),
  workDetailModal: document.querySelector("#workDetailModal"),
  workDetailTitle: document.querySelector("#workDetailTitle"),
  workDetailBody: document.querySelector("#workDetailBody"),
  metricWorks: document.querySelector("#metricWorks"),
  metricFiles: document.querySelector("#metricFiles"),
  metricDuplicates: document.querySelector("#metricDuplicates"),
  metricRejects: document.querySelector("#metricRejects"),
  backToTidier: document.querySelector("#backToTidier"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function fileUrl(path) {
  if (!path) {
    return "";
  }
  const normalized = String(path).replaceAll("\\", "/");
  const thumbMarker = "/electron-app/renderer/.thumb-cache/";
  const markerIndex = normalized.toLowerCase().indexOf(thumbMarker);
  if (markerIndex >= 0) {
    return encodeURI(`./.thumb-cache/${normalized.slice(markerIndex + thumbMarker.length)}`);
  }
  const prefixed = normalized.startsWith("/") ? `file://${normalized}` : `file:///${normalized}`;
  return encodeURI(prefixed);
}

function setStatus(text, type = "") {
  els.status.textContent = text;
  els.status.className = `status ${type}`.trim();
}

async function loadStore() {
  try {
    const localData = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    const fileData = window.fileTidier.loadManagerStore ? await window.fileTidier.loadManagerStore() : {};
    const data = { ...localData, ...fileData };
    state.metadata = data.metadata || {};
    state.rejectList = data.rejectList || [];
    state.trackList = data.trackList || [];
    state.folder = data.folder || "";
    state.items = Array.isArray(data.items) ? data.items : [];
    state.works = buildWorks(state.items);
    els.globalMemo.value = data.globalMemo || "";
    els.rejectList.value = state.rejectList.join("\n");
    els.trackList.value = state.trackList.join("\n");
    if (state.folder) {
      els.folderText.textContent = state.folder;
      els.folderText.title = state.folder;
    }
  } catch {
    state.metadata = {};
    state.rejectList = [];
    state.trackList = [];
  }
}

function storePayload(includeItems = true) {
  return {
    folder: state.folder,
    metadata: state.metadata,
    rejectList: state.rejectList,
    trackList: state.trackList,
    globalMemo: els.globalMemo.value,
    items: includeItems ? state.items : undefined,
    savedAt: new Date().toISOString(),
  };
}

function saveStore(includeItems = true) {
  const lightPayload = storePayload(false);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(lightPayload));
  if (window.fileTidier.saveManagerStore) {
    clearTimeout(state.saveTimer);
    state.saveTimer = setTimeout(() => {
      window.fileTidier.saveManagerStore(storePayload(includeItems));
    }, 250);
  }
}

function normalizeTitle(value) {
  const file = String(value || "").replaceAll("\\", "/").split("/").pop() || "";
  return file
    .replace(/\.[^/.]+$/, "")
    .replace(/^\s*(?:\[[^\]]+\]|\([^)]+\)|\{[^}]+\})+/g, "")
    .replace(/\s*\(\d+\)\s*$/g, "")
    .replace(/[+_\-.]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function titleKey(title) {
  return normalizeTitle(title).casefold?.() || normalizeTitle(title).toLocaleLowerCase("ko-KR");
}

function itemTitle(item) {
  return normalizeTitle(item.seriesTitle || item.displayTitle || item.title || item.name || item.location);
}

// 화수와 권수 중 아는 것을 보여준다. 둘 다 모르면 아무 말도 하지 않는다 -
// "최신 -화" 는 정보가 아니라 잡음이다.
function progressLabel(work) {
  if (work.latestEpisode) {
    return `최신 ${work.latestEpisode}화`;
  }
  if (work.latestVolume) {
    return `${work.latestVolume}권까지`;
  }
  return "화수 표시 없음";
}

// 이름을 episodeSuffix 로 둔다. progressText 는 이미 스캔 진행률 포맷터가
// 쓰고 있어서, 같은 이름을 만들었더니 행 설명에 "스캔 중 · 0초" 가 찍혔다.
function episodeSuffix(work) {
  if (!work.latestEpisode && !work.latestVolume) {
    return "";
  }
  return ` · ${progressLabel(work)}`;
}

function metadataFor(title) {
  const key = titleKey(title);
  if (!state.metadata[key]) {
    state.metadata[key] = {
      title,
      rating: "",
      tags: "",
      read: false,
      favorite: false,
      reviewMemo: "",
    };
  }
  return state.metadata[key];
}

// 평점·읽음·찜·태그는 같은 행에 조작 칸이 바로 있으므로 배지로 또 보여주지
// 않는다. 없다는 사실을 알리는 `로컬 메모 없음` 배지도 뺐다 - 1,000행에
// 반복되면 정보가 아니라 잡음이고, 한 행에 줄 하나를 통째로 더 먹었다.
function metaBadges(meta) {
  if (!meta.reviewMemo) {
    return "";
  }
  return `<span class="meta-badge">리뷰 메모</span>`;
}

function buildWorks(items) {
  const groups = new Map();
  for (const item of items) {
    const extension = String(item.extension || "").toLowerCase();
    if (extension && !SUPPORTED_EXTENSIONS.has(extension)) {
      continue;
    }
    const title = itemTitle(item);
    if (!title) {
      continue;
    }
    const key = titleKey(title);
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        title,
        author: item.displayAuthor || item.writer || "",
        thumbnail: item.thumbnail || "",
        files: [],
        extensions: new Set(),
        importedTags: new Set(),
        sourceHints: new Set(),
        isComplete: false,
        latestEpisode: 0,
        latestVolume: 0,
        largestSizeText: item.sizeText || "-",
      });
    }
    const work = groups.get(key);
    work.files.push(item);
    if (!work.author && (item.displayAuthor || item.writer)) {
      work.author = item.displayAuthor || item.writer;
    }
    if (!work.thumbnail && item.thumbnail) {
      work.thumbnail = item.thumbnail;
    }
    for (const tag of item.tags || []) {
      work.importedTags.add(tag);
    }
    if (item.sourceHint) {
      work.sourceHints.add(item.sourceHint);
    }
    work.isComplete = work.isComplete || Boolean(item.isComplete);
    work.extensions.add(extension || "-");
    // 파일명을 여기서 다시 뜯지 않는다. 예전에는 화/권 표시가 없어도 아무 숫자나
    // 화수로 읽어서, 복구 때 붙은 레코드 번호나 해시에서 5757화 같은 값이 나왔다.
    // 판정은 백엔드 한 곳에서만 한다.
    work.latestEpisode = Math.max(work.latestEpisode, Number(item.episodeCount || 0));
    work.latestVolume = Math.max(work.latestVolume || 0, Number(item.volumeCount || 0));
  }
  return [...groups.values()]
    .map((work) => {
      const meta = metadataFor(work.title);
      if (!meta.tags && work.importedTags.size) {
        meta.tags = [...work.importedTags].join(", ");
      }
      return {
        ...work,
        importedTags: [...work.importedTags].sort(),
        sourceHints: [...work.sourceHints].sort(),
        extensions: [...work.extensions].sort(),
        metadata: meta,
      };
    })
    .sort((a, b) => a.title.localeCompare(b.title, "ko-KR"));
}

function duplicateCount() {
  return state.works.filter((work) => work.files.length > 1).length;
}

function rejectMatches() {
  const rejects = state.rejectList.map((item) => item.trim()).filter(Boolean);
  if (!rejects.length) {
    return [];
  }
  return state.works.filter((work) =>
    rejects.some((reject) => work.title.toLocaleLowerCase("ko-KR").includes(reject.toLocaleLowerCase("ko-KR"))),
  );
}

function compactText(value) {
  return normalizeTitle(value).replace(/\s+/g, "").toLocaleLowerCase("ko-KR");
}

function bigramSet(value) {
  const text = compactText(value);
  if (!text) {
    return new Set();
  }
  if (text.length === 1) {
    return new Set([text]);
  }
  const grams = new Set();
  for (let index = 0; index < text.length - 1; index += 1) {
    grams.add(text.slice(index, index + 2));
  }
  return grams;
}

function similarityRatio(left, right) {
  const a = compactText(left);
  const b = compactText(right);
  if (!a || !b) {
    return 0;
  }
  if (a.includes(b) || b.includes(a)) {
    return Math.min(1, Math.max(a.length, b.length) / Math.max(1, Math.min(a.length, b.length)) > 2.4 ? 0.72 : 0.98);
  }
  const leftGrams = bigramSet(a);
  const rightGrams = bigramSet(b);
  let overlap = 0;
  for (const gram of leftGrams) {
    if (rightGrams.has(gram)) {
      overlap += 1;
    }
  }
  return (overlap * 2) / Math.max(1, leftGrams.size + rightGrams.size);
}

// 대괄호 안이 늘 작가는 아니다. 출처·상태 표시를 걸러 낸다.
const NOT_AUTHOR = new Set([
  "공금", "갠소", "공금갠소", "교불", "재업금지", "연재", "연재본", "완결", "완",
  "본편", "외전", "특별편", "합본", "단편", "텍본", "무료", "유료", "bl", "gl",
  "br", "판타지", "로맨스", "무협", "현판", "미포", "포함", "수정", "개정판",
]);

function isLikelyAuthor(name) {
  const cleaned = name.trim().toLowerCase();
  if (!cleaned || cleaned.length > 20) {
    return false;
  }
  if (NOT_AUTHOR.has(cleaned)) {
    return false;
  }
  // 쉼표로 여러 표시를 이어 붙인 것(`상큼토끼, 갠소, 공금`)은 작가가 아니다
  if (cleaned.includes(",")) {
    return false;
  }
  // 숫자·기호만 있는 것도 아니다
  return /[가-힣a-z]/.test(cleaned);
}

function bars(rows, total) {
  if (!rows.length) {
    return `<p class="dash-empty">아직 없습니다.</p>`;
  }
  const top = Math.max(...rows.map((r) => r.count), 1);
  return rows
    .map(
      (r) => `
        <div class="bar-row">
          <span class="bar-name" title="${escapeHtml(r.name)}">${escapeHtml(r.name)}</span>
          <span class="bar-track"><i style="width:${Math.round((r.count / top) * 100)}%"></i></span>
          <span class="bar-value">${r.count.toLocaleString()}${total ? ` · ${Math.round((r.count / total) * 100)}%` : ""}</span>
        </div>`,
    )
    .join("");
}

// 대시보드는 앱 설명이 아니라 내 서재 상태를 보여 준다. 앱이 뭘 하는지는
// 가이드와 패치노트에 이미 적혀 있다.
function renderDashboard() {
  const works = state.works;
  const files = state.items.length;
  const grouped = works.filter((w) => w.files.length > 1);
  // 한 뭉치에서 하나만 남긴다면 몇 개가 줄어드는가
  const removable = grouped.reduce((sum, w) => sum + w.files.length - 1, 0);
  const marked = works.filter(hasRecord).length;
  const noCover = works.filter(
    (w) => !w.thumbnail && w.extensions.some((e) => [".epub", ".zip", ".cbz"].includes(e)),
  ).length;

  els.metricWorks.textContent = works.length.toLocaleString();
  els.metricFiles.textContent = files.toLocaleString();
  els.metricDuplicates.textContent = removable.toLocaleString();
  els.metricRejects.textContent = marked.toLocaleString();
  const note = (el, text) => {
    if (el) {
      el.textContent = text;
    }
  };
  note(els.metricWorksNote, works.length ? `파일 ${files.toLocaleString()}개를 묶은 결과` : "폴더를 불러오세요");
  note(els.metricFilesNote, state.folder || "");
  note(els.metricDuplicatesNote, removable ? `${grouped.length.toLocaleString()}개 작품에 여분이 있습니다` : "여분 없음");
  note(els.metricRejectsNote, marked ? "평점·태그·읽음·메모" : "아직 없습니다");

  const extCount = {};
  for (const item of state.items) {
    const ext = String(item.extension || "").toLowerCase() || "(없음)";
    extCount[ext] = (extCount[ext] || 0) + 1;
  }
  els.dashExtensions.innerHTML = bars(
    Object.entries(extCount).map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count).slice(0, 6),
    files,
  );

  const authorCount = {};
  for (const work of works) {
    const name = String(work.author || "").trim();
    // 파일명 대괄호에는 작가만 들어 있지 않다. 공금·갠소·완결 같은 표시가
    // 작가로 잡히면 순위표가 그것들로 채워진다.
    if (!name || !isLikelyAuthor(name)) {
      continue;
    }
    authorCount[name] = (authorCount[name] || 0) + work.files.length;
  }
  els.dashAuthors.innerHTML = bars(
    Object.entries(authorCount).map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count).slice(0, 6),
    0,
  );

  els.dashBiggest.innerHTML = grouped.length
    ? [...grouped]
        .sort((a, b) => b.files.length - a.files.length)
        .slice(0, 6)
        .map(
          (w) => `
            <div class="rank-row">
              <strong title="${escapeHtml(w.title)}">${escapeHtml(w.title)}</strong>
              <span>${escapeHtml(w.extensions.join(", "))}</span>
              <em>${w.files.length}개</em>
            </div>`,
        )
        .join("")
    : `<p class="dash-empty">묶인 작품이 없습니다.</p>`;

  const todo = [
    { label: "중복 후보", count: grouped.length, filter: "duplicate", hint: "같은 작품에 파일이 여러 개" },
    { label: "확장자 섞임", count: works.filter((w) => w.extensions.length > 1).length, filter: "mixed", hint: "epub 과 txt 가 같이 있음" },
    { label: "표지 없음", count: noCover, filter: "", hint: "표지 불러오기로 채울 수 있음" },
  ].filter((t) => t.count);
  els.dashTodo.innerHTML = todo.length
    ? todo
        .map(
          (t) => `
            <button class="todo-row" type="button" data-goto="${escapeHtml(t.filter)}">
              <strong>${t.count.toLocaleString()}</strong>
              <span>${escapeHtml(t.label)}</span>
              <em>${escapeHtml(t.hint)}</em>
            </button>`,
        )
        .join("")
    : `<p class="dash-empty">손볼 것이 없습니다.</p>`;
}

function renderMetrics() {
  renderDashboard();
}

// 작가로도 찾을 수 있어야 한다. 파일명이 `[슘민] 제목` 형태라 제목 검색으로
// 우연히 걸리기도 하지만, 작가가 파일명 끝에 있거나 epub 메타에만 있으면
// 못 찾았다.
function matchesQuery(work, query) {
  if (!query) {
    return true;
  }
  const meta = metadataFor(work.title);
  return (
    work.title.toLocaleLowerCase("ko-KR").includes(query) ||
    String(work.author || "").toLocaleLowerCase("ko-KR").includes(query) ||
    work.extensions.join(" ").includes(query) ||
    String(meta.tags || "").toLocaleLowerCase("ko-KR").includes(query)
  );
}

function hasRecord(work) {
  const meta = metadataFor(work.title);
  return Boolean(
    meta.read || meta.favorite || String(meta.rating || "").trim() ||
    String(meta.tags || "").trim() || String(meta.reviewMemo || "").trim(),
  );
}

// 추리기는 "무엇을 볼지" 만 정한다. 목록에서 뭔가를 바꾸지 않는다.
function matchesFilter(work) {
  switch (state.listFilter) {
    case "duplicate":
      return work.files.length > 1;
    case "mixed":
      return work.extensions.length > 1;
    case "marked":
      return hasRecord(work);
    default:
      return true;
  }
}

function workModified(work) {
  let latest = "";
  for (const file of work.files) {
    const value = String(file.modified || "");
    if (value > latest) {
      latest = value;
    }
  }
  return latest;
}

function workSize(work) {
  return work.files.reduce((sum, file) => sum + Number(file.size || 0), 0);
}

function sortWorks(works) {
  const sorted = [...works];
  switch (state.listSort) {
    case "files":
      sorted.sort((a, b) => b.files.length - a.files.length || a.title.localeCompare(b.title, "ko-KR"));
      break;
    case "modified":
      sorted.sort((a, b) => workModified(b).localeCompare(workModified(a)) || a.title.localeCompare(b.title, "ko-KR"));
      break;
    case "episode":
      sorted.sort((a, b) =>
        (b.latestEpisode || b.latestVolume || 0) - (a.latestEpisode || a.latestVolume || 0) ||
        a.title.localeCompare(b.title, "ko-KR"));
      break;
    case "size":
      sorted.sort((a, b) => workSize(b) - workSize(a) || a.title.localeCompare(b.title, "ko-KR"));
      break;
    default:
      sorted.sort((a, b) => a.title.localeCompare(b.title, "ko-KR"));
  }
  return sorted;
}

function filteredWorks() {
  const query = els.librarySearch.value.trim().toLocaleLowerCase("ko-KR");
  return sortWorks(state.works.filter((work) => matchesFilter(work) && matchesQuery(work, query)));
}

// 눌러도 아무것도 안 나오는 추리기는 눌러 보기 전에 알려 준다.
function updateChipCounts() {
  if (!els.chipGroup) {
    return;
  }
  const counts = {
    all: state.works.length,
    duplicate: state.works.filter((w) => w.files.length > 1).length,
    mixed: state.works.filter((w) => w.extensions.length > 1).length,
    marked: state.works.filter(hasRecord).length,
  };
  for (const chip of els.chipGroup.querySelectorAll(".chip")) {
    const key = chip.dataset.filter;
    const count = counts[key] ?? 0;
    const label = { all: "전체", duplicate: "중복 후보", mixed: "확장자 섞임", marked: "기록 있음" }[key] || key;
    chip.textContent = count ? `${label} ${count.toLocaleString()}` : label;
    chip.classList.toggle("on", state.listFilter === key);
    chip.classList.toggle("empty", !count && key !== "all");
  }
}

// 표지는 `작품 불러오기` 로는 안 온다. 파일을 다시 다 읽어야 해서 따로
// 떼어 둔 것인데, 버튼 이름만 봐서는 알 수 없어 표지가 깨진 줄로 안다.
// 표지가 하나도 없을 때만 목록 위에서 알려 준다.
function updateCoverNotice() {
  if (!els.coverNotice) {
    return;
  }
  // 하나라도 있으면 숨기면 안 된다. 예전에 일부만 읽어 둔 경우가 흔해서
  // (표지 일부 불러오기는 300개까지) 나머지가 계속 빈 채로 남는다.
  // 파일 안에 표지가 있는 형식(epub/zip/cbz)과, 웹 검색으로만 얻을 수 있는
  // 형식(txt 등)을 나눠 센다. 안내 문구가 달라야 한다.
  const inside = state.works.filter(
    (work) => !work.thumbnail && work.extensions.some((ext) => [".epub", ".zip", ".cbz"].includes(ext)),
  ).length;
  const missing = state.works.filter((work) => !work.thumbnail).length;
  if (!missing) {
    els.coverNotice.hidden = true;
    return;
  }
  // "N개는 파일 안에서 꺼낼 수 있다" 고 적었더니 사실이 아니었다. 확장자가
  // epub/zip 이어도 안에 이미지가 없거나 zip 내부 항목이면 못 꺼낸다.
  // 실제로 표지 전체 불러오기를 돌려도 15개밖에 안 늘었다. 될 것처럼 약속하지
  // 않고, 무엇을 눌러 보면 되는지만 말한다.
  els.coverNotice.querySelector("span").textContent =
    `표지 없는 작품이 ${missing}개 있습니다. 파일 안에 표지가 있으면 아래 버튼으로 꺼내고,` +
    ` 없으면 정리 도구의 웹 표지 검색이 리디에서 찾습니다. 개인 연재본은 대부분 찾지 못합니다.`;
  els.coverNotice.hidden = false;
}

// 사용자가 직접 넣은 것만 뺀다. 스캔 결과(items)는 다시 훑으면 나오고,
// 넣어 두면 파일만 커져서 옮기기 불편하다.
function userEnteredOnly(metadata) {
  const fields = ["rating", "tags", "read", "favorite", "reviewMemo", "memo"];
  const out = {};
  for (const [key, value] of Object.entries(metadata || {})) {
    if (!value || typeof value !== "object") {
      continue;
    }
    const kept = fields.some((f) => {
      const v = value[f];
      return v === true || (typeof v === "string" && v.trim() !== "");
    });
    if (kept) {
      out[key] = value;
    }
  }
  return out;
}

async function exportManagerData() {
  const metadata = userEnteredOnly(state.metadata);
  const payload = {
    kind: "file-tidier-manager-data",
    version: 1,
    savedAt: new Date().toISOString(),
    metadata,
    rejectList: state.rejectList || [],
    trackList: state.trackList || [],
    globalMemo: els.globalMemo ? els.globalMemo.value : "",
  };
  const result = await window.fileTidier.exportManagerData(payload);
  if (result?.cancelled) {
    els.dataPortResult.textContent = "내보내기를 취소했습니다.";
    return;
  }
  if (!result?.ok) {
    els.dataPortResult.textContent = `내보내지 못했습니다: ${result?.error || "알 수 없는 오류"}`;
    return;
  }
  els.dataPortResult.textContent =
    `기록 ${Object.keys(metadata).length}건, 보관거부 ${(state.rejectList || []).length}개, ` +
    `추적 ${(state.trackList || []).length}개를 저장했습니다.
${result.path}`;
}

async function importManagerData() {
  const result = await window.fileTidier.importManagerData();
  if (result?.cancelled) {
    els.dataPortResult.textContent = "가져오기를 취소했습니다.";
    return;
  }
  if (!result?.ok) {
    els.dataPortResult.textContent = `읽지 못했습니다: ${result?.error || "알 수 없는 오류"}`;
    return;
  }
  const data = result.data || {};
  if (data.kind && data.kind !== "file-tidier-manager-data") {
    els.dataPortResult.textContent = "이 파일은 작품 관리 기록이 아닙니다.";
    return;
  }
  // 이미 있는 값을 덮지 않는다. 지금 쓰고 있는 기록이 최신이라고 보는 편이
  // 안전하고, 덮어쓰면 되돌릴 방법이 없다.
  const incoming = userEnteredOnly(data.metadata);
  let added = 0;
  let kept = 0;
  for (const [key, value] of Object.entries(incoming)) {
    if (state.metadata[key]) {
      kept += 1;
      continue;
    }
    state.metadata[key] = value;
    added += 1;
  }
  const before = new Set([...(state.rejectList || []), ...(state.trackList || [])]);
  const rejectAdded = (data.rejectList || []).filter((x) => !before.has(x));
  const trackAdded = (data.trackList || []).filter((x) => !before.has(x));
  state.rejectList = [...(state.rejectList || []), ...rejectAdded];
  state.trackList = [...(state.trackList || []), ...trackAdded];
  if (els.rejectList) {
    els.rejectList.value = state.rejectList.join("\n");
  }
  if (els.trackList) {
    els.trackList.value = state.trackList.join("\n");
  }
  // 메모는 입력칸이 원본이다. 이미 적어 둔 게 있으면 덮지 않는다.
  if (els.globalMemo && !els.globalMemo.value.trim() && data.globalMemo) {
    els.globalMemo.value = data.globalMemo;
  }
  saveStore();
  renderAll();
  // 지금 폴더에 없는 작품의 기록도 그대로 둔다. 그 폴더를 다시 훑으면 붙는다.
  const works = new Set(state.works.map((w) => w.title));
  const unmatched = Object.keys(incoming).filter((k) => !works.has(k)).length;
  els.dataPortResult.textContent =
    `새로 들어온 기록 ${added}건, 이미 있어서 그대로 둔 것 ${kept}건. ` +
    `보관거부 +${rejectAdded.length}, 추적 +${trackAdded.length}.` +
    (unmatched ? `
지금 폴더에 없는 작품의 기록 ${unmatched}건은 그대로 보관합니다 - 그 폴더를 훑으면 붙습니다.` : "");
}

// 한 번에 다 그리면 카드뷰에서 1,158장을 화면 밖까지 전부 만든다. 전환에
// 몇 분이 걸려 사실상 못 쓰는 기능이었다. 보이는 만큼만 그리고, 끝에 닿으면
// 이어 붙인다. 이어 붙이는 방식이라 이미 그린 행의 입력값과 포커스가 살아
// 남는다(전부 다시 그리면 태그를 치던 중에 날아간다).
const PAGE = 60;

// 표지가 없으면 같은 색 바탕에 로고만 넣는다. 제목에서 색을 뽑아 보기도
// 했는데, 알록달록한 타일이 진짜 표지보다 눈에 띄어 책장이 산만해졌다.
// 제목은 표지 아래에 어차피 적힌다.
function coverHtml(work) {
  if (work.thumbnail) {
    return `<img src="${escapeHtml(fileUrl(work.thumbnail))}" alt="" loading="lazy" />`;
  }
  return `<div class="cover-made"><span class="cover-logo">FT</span></div>`;
}

function workRowHtml(work) {
  const meta = metadataFor(work.title);
  const count = work.files.length;
  const progress = work.latestEpisode
    ? `${work.latestEpisode}화`
    : work.latestVolume
      ? `${work.latestVolume}권`
      : "";
  const badges = [
    count > 1 ? `<span class="badge dup">${count}</span>` : "",
    meta.favorite ? `<span class="badge fav">찜</span>` : "",
  ].join("");
  const foot = [work.author, work.extensions.join(", "), progress, work.isComplete ? "완결" : ""]
    .filter(Boolean)
    .join(" · ");
  return `
    <article class="shelf-item${meta.read ? " is-read" : ""}" data-title="${escapeHtml(work.title)}">
      <div class="shelf-cover">
        ${coverHtml(work)}
        ${badges}
        <div class="shelf-actions">
          <button class="mini-toggle ${meta.read ? "on" : ""}" data-toggle="read" data-title="${escapeHtml(work.title)}" type="button">${meta.read ? "읽음" : "안 읽음"}</button>
          <button class="mini-toggle ${meta.favorite ? "on" : ""}" data-toggle="favorite" data-title="${escapeHtml(work.title)}" type="button">찜</button>
          <button class="mini-toggle detail-button" data-show-files="${escapeHtml(work.key)}" type="button">파일 ${count}</button>
        </div>
      </div>
      <strong title="${escapeHtml(work.title)}">${escapeHtml(work.title)}</strong>
      <span>${escapeHtml(foot)}</span>
      <div class="shelf-inputs">
        <input class="rating-input" data-meta="rating" data-title="${escapeHtml(work.title)}" value="${escapeHtml(meta.rating)}" placeholder="평점" />
        <input class="tag-input" data-meta="tags" data-title="${escapeHtml(work.title)}" value="${escapeHtml(meta.tags)}" placeholder="태그" />
      </div>
    </article>
  `;
}

function appendWorkRows(works, from, to) {
  const html = works.slice(from, to).map(workRowHtml).join("");
  els.libraryList.insertAdjacentHTML("beforeend", html);
}

function ensureListSentinel(works) {
  let sentinel = els.libraryList.querySelector(".list-sentinel");
  if (state.shownCount >= works.length) {
    if (sentinel) {
      sentinel.remove();
    }
    if (state.listObserver) {
      state.listObserver.disconnect();
    }
    return;
  }
  if (!sentinel) {
    sentinel = document.createElement("div");
    sentinel.className = "list-sentinel";
    els.libraryList.appendChild(sentinel);
  } else {
    els.libraryList.appendChild(sentinel);
  }
  sentinel.textContent = `${(works.length - state.shownCount).toLocaleString()}개 더 있습니다`;
  if (state.listObserver) {
    state.listObserver.disconnect();
  }
  state.listObserver = new IntersectionObserver((entries) => {
    if (!entries.some((entry) => entry.isIntersecting)) {
      return;
    }
    const next = Math.min(state.shownCount + PAGE, works.length);
    appendWorkRows(works, state.shownCount, next);
    state.shownCount = next;
    ensureListSentinel(works);
  }, { rootMargin: "600px" });
  state.listObserver.observe(sentinel);
}

function renderLibrary() {
  const works = filteredWorks();
  els.libraryMeta.textContent = `작품 ${works.length}개 · 파일 ${state.items.length}개 · 중복 후보 ${duplicateCount()}개`;
  updateChipCounts();
  updateCoverNotice();
  if (state.listObserver) {
    state.listObserver.disconnect();
  }
  if (!works.length) {
    els.libraryList.innerHTML = `<div class="recommend-box">표시할 작품이 없습니다.</div>`;
    state.shownCount = 0;
    return;
  }
  els.libraryList.className = `library-list ${state.cardView ? "compact" : "shelf"}`;
  els.libraryList.innerHTML = "";
  state.shownCount = Math.min(PAGE, works.length);
  appendWorkRows(works, 0, state.shownCount);
  ensureListSentinel(works);
}

function renderLatest() {
  // 파일이 하나뿐이고 확장자도 하나면 비교할 상대가 없다. 예전에는 화수만
  // 있어도 목록에 넣어서, 200줄이 전부 "단일 · 1개 파일" 로 찼다.
  const candidates = state.works
    .filter((work) => work.files.length > 1 || work.extensions.length > 1)
    .sort((a, b) => b.latestEpisode - a.latestEpisode || b.files.length - a.files.length || a.title.localeCompare(b.title, "ko-KR"))
    .slice(0, 200);
  if (!candidates.length) {
    els.latestList.innerHTML = `<div class="recommend-box">비교할 후보가 없습니다. 먼저 작품을 불러오세요.</div>`;
    return;
  }
  els.latestList.innerHTML = candidates
    .map((work) => {
      const ranked = [...work.files].sort((a, b) => {
        // 백엔드가 판정한 값만 쓴다. 화수를 모르면 권수로, 그것도 모르면 수정일로.
        const rank = (item) => Number(item.episodeCount || 0) || Number(item.volumeCount || 0);
        const episodeDiff = rank(b) - rank(a);
        if (episodeDiff) {
          return episodeDiff;
        }
        return String(b.modified || "").localeCompare(String(a.modified || ""));
      });
      const best = ranked[0];
      const newestModified = [...work.files].sort((a, b) => String(b.modified || "").localeCompare(String(a.modified || "")))[0];
      const mixedExtensions = work.extensions.length > 1;
      const status = mixedExtensions ? "확장자 섞임" : work.files.length > 1 ? "중복 후보" : "단일";
      return `
        <article class="compare-row">
          <div>
            <strong>${escapeHtml(work.title)}</strong>
            <span>최신 후보: ${escapeHtml(best?.name || "")}</span>
            <span>수정일 최신: ${escapeHtml(newestModified?.name || "")} · ${escapeHtml(newestModified?.modified || "-")}</span>
          </div>
          <span>${work.files.length}개 파일</span>
          <span>${escapeHtml(work.extensions.join(", "))}</span>
          <span>${progressLabel(work)}</span>
          <span class="latest-pill ${mixedExtensions ? "warn" : "good"}">${status}</span>
        </article>
      `;
    })
    .join("");
}

function renderReviews() {
  const works = filteredWorks().slice(0, 200);
  els.reviewTargetList.innerHTML = works.length
    ? works
        .map((work) => {
          const meta = metadataFor(work.title);
          return `
            <article class="target-row" data-review-title="${escapeHtml(work.title)}">
              <div>
                <strong title="${escapeHtml(work.title)}">${escapeHtml(work.title)}</strong>
                ${meta.tags ? `<span>${escapeHtml(meta.tags)}</span>` : ""}
              </div>
              <span>${escapeHtml(meta.rating || "")}</span>
              <button data-review-title="${escapeHtml(work.title)}" type="button">선택</button>
            </article>
          `;
        })
        .join("")
    : `<div class="recommend-box">작품을 먼저 불러오세요.</div>`;
}

function renderRejectMatches() {
  const matches = rejectMatches();
  els.rejectMatches.innerHTML = matches.length
    ? `<p class="caption">보관거부 일치 ${matches.length}개</p>${matches
        .slice(0, 30)
        .map((work) => `<p>${escapeHtml(work.title)}</p>`)
        .join("")}`
    : `<p class="caption">보관거부와 일치한 작품 없음</p>`;
}

function trackMatches() {
  const keywords = state.trackList.map((item) => item.trim()).filter(Boolean);
  if (!keywords.length) {
    return [];
  }
  const rows = [];
  for (const keyword of keywords) {
    const key = keyword.toLocaleLowerCase("ko-KR");
    const matches = state.works
      .map((work) => {
      const haystack = [work.title, work.author, work.extensions.join(" "), ...work.files.map((file) => file.name || file.location)]
        .join(" ")
        .toLocaleLowerCase("ko-KR");
        const exact = haystack.includes(key);
        const score = Math.max(similarityRatio(keyword, work.title), similarityRatio(keyword, work.author));
        return { work, score: exact ? 1 : score };
      })
      .filter((match) => match.score >= 0.82)
      .sort((a, b) => b.score - a.score || a.work.title.localeCompare(b.work.title, "ko-KR"))
      .map((match) => ({ ...match.work, matchScore: match.score }));
    rows.push({ keyword, matches });
  }
  return rows;
}

function renderTrackMatches() {
  const rows = trackMatches();
  if (!rows.length) {
    els.trackMatches.innerHTML = `<div class="recommend-box">추적 키워드를 저장하면 현재 작품 목록과 대조합니다.</div>`;
    return;
  }
  els.trackMatches.innerHTML = rows
    .map(({ keyword, matches }) => {
      const preview = matches
        .slice(0, 8)
        .map((work) => `${work.title}${work.matchScore < 1 ? ` (${Math.round(work.matchScore * 100)}%)` : ""}`)
        .join(", ");
      // 찜·읽음은 따로 칸을 두면 거의 늘 "-" 다. 있을 때만 설명줄에 붙인다.
      const marks = [];
      if (matches.some((work) => metadataFor(work.title).favorite)) {
        marks.push("찜");
      }
      if (matches.some((work) => metadataFor(work.title).read)) {
        marks.push("읽음");
      }
      const note = [preview || "일치 없음", marks.join(" · ")].filter(Boolean).join(" — ");
      return `
        <article class="track-row">
          <div>
            <strong title="${escapeHtml(keyword)}">${escapeHtml(keyword)}</strong>
            <span>${escapeHtml(note)}</span>
          </div>
          <span class="track-count">${matches.length}개</span>
          <span class="latest-pill ${matches.length ? "good" : "warn"}">${matches.length ? "발견" : "없음"}</span>
        </article>
      `;
    })
    .join("");
}

function renderRecommend() {
  const tagged = state.works.filter((work) => metadataFor(work.title).tags || metadataFor(work.title).rating);
  const favorite = state.works.filter((work) => metadataFor(work.title).favorite);
  const read = state.works.filter((work) => metadataFor(work.title).read);
  const tagCounts = new Map();
  for (const work of state.works) {
    const tags = String(metadataFor(work.title).tags || "")
      .split(/[,\s]+/)
      .map((tag) => tag.trim())
      .filter(Boolean);
    for (const tag of tags) {
      tagCounts.set(tag, (tagCounts.get(tag) || 0) + 1);
    }
  }
  const topTags = [...tagCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
  els.tasteSummary.innerHTML = `
    <strong>취향 요약</strong><br />
    보유 작품 ${state.works.length}개, 태그/평점 입력 ${tagged.length}개, 읽음 ${read.length}개, 즐겨찾기 ${favorite.length}개입니다.<br />
    자주 나온 태그: ${topTags.length ? topTags.map(([tag, count]) => `${escapeHtml(tag)}(${count})`).join(", ") : "아직 없음"}
  `;
  // 근거가 있는 것만 후보로 둔다. 예전에는 기록이 하나도 없어도 제목순
  // 앞에서 아홉 개를 잘라 보여 줬고, 깨진 이름까지 후보로 올라왔다.
  // 취향을 모르는 채로 고른 아홉 개는 추천의 근거가 아니다.
  const candidates = state.works
    .filter((work) => {
      const meta = metadataFor(work.title);
      if (!meta.favorite && !String(meta.rating || "").trim() && !String(meta.tags || "").trim()) {
        return false;
      }
      return !/^%u[0-9a-f]{4}/i.test(work.title) && !/�/.test(work.title);
    })
    .sort((a, b) => {
      const am = metadataFor(a.title);
      const bm = metadataFor(b.title);
      return Number(bm.favorite) - Number(am.favorite) || Number(bm.rating || 0) - Number(am.rating || 0);
    })
    .slice(0, 9);
  els.recommendCandidates.innerHTML = candidates.length
    ? candidates
    .map((work) => {
      const meta = metadataFor(work.title);
      const bits = [meta.rating ? `평점 ${meta.rating}` : "", meta.tags || "", meta.favorite ? "찜" : ""].filter(Boolean);
      return `
        <article class="candidate-card">
          <strong>${escapeHtml(work.title)}</strong>
          <span>${escapeHtml(bits.join(" · "))}</span>
        </article>
      `;
    })
    .join("")
    : `<div class="recommend-box">추천의 근거로 쓸 기록이 없습니다. 작품 목록에서 마음에 든 작품에 평점이나 찜을 남기면 그것을 바탕으로 후보를 고릅니다.</div>`;
}

function buildPrompt() {
  const favorites = state.works
    .filter((work) => metadataFor(work.title).favorite)
    .slice(0, 30)
    .map((work) => `- ${work.title} / 태그: ${metadataFor(work.title).tags || "-"} / 평점: ${metadataFor(work.title).rating || "-"}`);
  const rejects = state.rejectList.slice(0, 50).map((item) => `- ${item}`);
  const prompt = [
    "내가 가진 작품 목록과 취향을 바탕으로 다음에 읽을 작품을 추천해줘.",
    "이미 보유한 작품이나 보관거부 목록과 겹치는 추천은 제외해줘.",
    "",
    "[좋아한 작품]",
    favorites.length ? favorites.join("\n") : "- 아직 즐겨찾기/평점이 부족함",
    "",
    "[보관거부/제외]",
    rejects.length ? rejects.join("\n") : "- 없음",
    "",
    "추천 결과는 제목, 추천 이유, 비슷한 보유작, 확인할 검색어 순서로 정리해줘.",
  ].join("\n");
  els.recommendPrompt.value = prompt;
}

function renderExternalCompare() {
  const lines = els.externalWorkList.value
    .split(/\r?\n/)
    .map((line) => normalizeTitle(line))
    .filter(Boolean);
  const owned = [];
  const rejected = [];
  const missing = [];
  for (const line of lines) {
    const key = titleKey(line);
    const isOwned = state.works.some((work) => work.key.includes(key) || key.includes(work.key));
    const isRejected = state.rejectList.some((reject) => titleKey(reject).includes(key) || key.includes(titleKey(reject)));
    if (isOwned) {
      owned.push(line);
    } else if (isRejected) {
      rejected.push(line);
    } else {
      missing.push(line);
    }
  }
  els.externalCompareResult.innerHTML = `
    <strong>보유 ${owned.length}개 · 보관거부 ${rejected.length}개 · 미보유 ${missing.length}개</strong>
    <p>미보유: ${escapeHtml(missing.slice(0, 40).join(", ") || "-")}</p>
  `;
}

function renderFolderReport() {
  if (!state.items.length) {
    els.folderReport.innerHTML = `<p>먼저 작품을 불러오세요.</p>`;
    return;
  }
  const groups = new Map();
  for (const item of state.items) {
    const location = String(item.location || "");
    const folder = location.includes(" :: ")
      ? `${location.split(" :: ")[0]} 내부`
      : location.replaceAll("\\", "/").split("/").slice(0, -1).join("/") || "(루트)";
    if (!groups.has(folder)) {
      groups.set(folder, []);
    }
    groups.get(folder).push(item);
  }
  els.folderReport.innerHTML = [...groups.entries()]
    .sort((a, b) => b[1].length - a[1].length)
    .slice(0, 80)
    .map(([folder, items], index) => {
      const files = items
        .slice(0, 120)
        .map((item) => `<li>${escapeHtml(item.name)} <span class="caption">${escapeHtml(item.sizeText || "")}</span></li>`)
        .join("");
      return `
        <details ${index === 0 ? "open" : ""}>
          <summary>${escapeHtml(folder)} · ${items.length}개</summary>
          <ul>${files}</ul>
        </details>
      `;
    })
    .join("");
}

function showWorkDetail(workKey) {
  const work = state.works.find((item) => item.key === workKey);
  if (!work) {
    return;
  }
  els.workDetailTitle.textContent = work.title;
  const rows = [...work.files]
    .sort((a, b) => String(a.name || "").localeCompare(String(b.name || ""), "ko-KR"))
    .map((file) => {
      const location = file.location || "";
      return `
        <article class="detail-file-row">
          <div>
            <strong>${escapeHtml(file.name || file.title || "-")}</strong>
            <span>${escapeHtml(location)}</span>
          </div>
          <span>${escapeHtml(file.extension || "-")}</span>
          <span>${escapeHtml(file.sizeText || "-")}</span>
          <span>${escapeHtml(file.modified || "-")}</span>
        </article>
      `;
    })
    .join("");
  els.workDetailBody.innerHTML = `
    <div class="detail-summary">
      <span>파일 ${work.files.length}개</span>
      <span>확장자 ${escapeHtml(work.extensions.join(", ") || "-")}</span>
      <span>${progressLabel(work)}</span>
      ${work.author ? `<span>작가 ${escapeHtml(work.author)}</span>` : ""}
    </div>
    <div class="detail-file-list">${rows}</div>
  `;
  els.workDetailModal.hidden = false;
}

function closeWorkDetail() {
  els.workDetailModal.hidden = true;
  els.workDetailBody.innerHTML = "";
}

function renderAll() {
  renderMetrics();
  renderLibrary();
  renderLatest();
  renderReviews();
  renderRejectMatches();
  renderTrackMatches();
  renderRecommend();
}

function formatElapsed(ms) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) {
    return `${seconds}초`;
  }
  return `${Math.floor(seconds / 60)}분 ${seconds % 60}초`;
}

function progressText(progress) {
  const elapsed = state.scanStartedAt ? formatElapsed(Date.now() - state.scanStartedAt) : "0초";
  const total = Number(progress?.total || 0);
  const current = Number(progress?.current || 0);
  const detail = progress?.detail ? ` · ${progress.detail}` : "";
  if (total > 0) {
    return `${progress.step || "스캔 중"} · ${current}/${total}개 · ${elapsed}${detail}`;
  }
  return `${progress?.step || "스캔 중"} · ${elapsed}${detail}`;
}

function updateProgress(progress = state.latestProgress) {
  if (!state.scanStartedAt) {
    return;
  }
  state.latestProgress = progress || state.latestProgress || { step: "작품 불러오는 중" };
  setStatus(progressText(state.latestProgress), "loading");
}

function startProgress() {
  state.scanStartedAt = Date.now();
  state.latestProgress = { step: "작품 불러오는 중" };
  updateProgress();
  if (state.scanTimer) {
    clearInterval(state.scanTimer);
  }
  state.scanTimer = setInterval(() => updateProgress(), 1000);
}

function stopProgress() {
  state.scanStartedAt = null;
  state.latestProgress = null;
  if (state.scanTimer) {
    clearInterval(state.scanTimer);
    state.scanTimer = null;
  }
}

async function scanLibrary({ withThumbnails = false, thumbnailLimit = 0, allCovers = false } = {}) {
  if (!state.folder) {
    setStatus("관리 폴더를 선택하세요.", "error");
    return;
  }
  if (allCovers) {
    const confirmed = window.confirm(
      "표지 전체 불러오기는 폴더 안의 모든 EPUB/ZIP/CBZ 표지 후보를 확인합니다.\n파일 수가 많으면 오래 걸릴 수 있습니다. 진행할까요?",
    );
    if (!confirmed) {
      setStatus("표지 전체 불러오기 취소됨");
      return;
    }
  }
  startProgress();
  els.scan.disabled = true;
  els.scanCovers.disabled = true;
  els.scanAllCovers.disabled = true;
  els.cancelScan.disabled = false;
  let payload;
  try {
    payload = await window.fileTidier.scan({
      mode: "catalog",
      folder: state.folder,
      query: "",
      limit: 100000,
      minSizeKb: 0,
      recursive: els.recursive.checked,
      includeZip: els.includeZip.checked,
      withThumbnails,
      thumbnailLimit,
    });
  } catch (error) {
    payload = { ok: false, error: error?.message || String(error) };
  } finally {
    stopProgress();
    els.scan.disabled = false;
    els.scanCovers.disabled = false;
    els.scanAllCovers.disabled = false;
    els.cancelScan.disabled = true;
  }
  if (!payload?.ok) {
    setStatus(payload?.error || "작품을 불러오지 못했습니다.", payload?.cancelled ? "" : "error");
    return;
  }
  state.items = payload.items || [];
  state.works = buildWorks(state.items);
  saveStore();
  renderAll();
  setStatus(
    withThumbnails
      ? `${allCovers ? "표지 전체 반영" : "표지 일부 반영"} · 표지 대상 ${payload.thumbnailTried || 0}개 · 작품 ${state.works.length}개 · 파일 ${state.items.length}개`
      : `작품 ${state.works.length}개 · 파일 ${state.items.length}개`,
  );
}

async function fetchWebCovers() {
  if (!state.folder) {
    setStatus("관리 폴더를 선택하세요.", "error");
    return;
  }
  const maxItems = Math.max(1, Math.min(100, Number.parseInt(els.webCoverLimit.value, 10) || 20));
  const confirmed = window.confirm(
    `웹 표지 검색은 외부 검색 사이트에 작품명을 전송합니다.\n원본 파일은 바꾸지 않고 썸네일 캐시에만 저장합니다.\n최대 ${maxItems}개를 검색할까요?`,
  );
  if (!confirmed) {
    setStatus("웹 표지 검색 취소됨");
    return;
  }
  startProgress();
  els.fetchWebCovers.disabled = true;
  let payload;
  try {
    payload = await window.fileTidier.fetchWebCovers({
      folder: state.folder,
      query: els.librarySearch.value || "",
      recursive: els.recursive.checked,
      maxItems,
    });
  } catch (error) {
    payload = { ok: false, error: error?.message || String(error) };
  } finally {
    stopProgress();
    els.fetchWebCovers.disabled = false;
  }
  if (!payload?.ok) {
    setStatus(payload?.error || "웹 표지 검색 실패", "error");
    return;
  }
  const byLocation = new Map((payload.items || []).filter((item) => item.thumbnail).map((item) => [item.location, item.thumbnail]));
  state.items = state.items.map((item) => (byLocation.has(item.location) ? { ...item, thumbnail: byLocation.get(item.location) } : item));
  state.works = buildWorks(state.items);
  saveStore();
  renderAll();
  els.webCoverResult.innerHTML = `
    <strong>표지 ${payload.updated || 0}개 반영 · 검색 ${payload.total || 0}개</strong>
    <p>${escapeHtml((payload.items || []).slice(0, 8).map((item) => `${item.ok ? "성공" : "실패"}: ${item.query}`).join(", ") || "-")}</p>
  `;
  setStatus(`웹 표지 검색 완료 · ${payload.updated || 0}/${payload.total || 0}개`);
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.disabled) {
      setStatus("최신내용 프리뷰는 다음 승인 단계에서 넣을게요.");
      return;
    }
    document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item === button));
    document.querySelectorAll(".view").forEach((panel) => panel.classList.toggle("active", panel.dataset.panel === button.dataset.view));
    els.viewTitle.textContent = button.textContent;
  });
});

els.backToTidier.addEventListener("click", async () => {
  await window.fileTidier.openTidier();
});

els.chooseFolder.addEventListener("click", async () => {
  const folder = await window.fileTidier.chooseFolder();
  if (!folder) {
    return;
  }
  state.folder = folder;
  state.items = [];
  state.works = [];
  els.folderText.textContent = folder;
  els.folderText.title = folder;
  saveStore();
  renderAll();
  setStatus("관리 폴더 선택됨");
});

els.scan.addEventListener("click", () => scanLibrary());
if (els.exportData) {
  els.exportData.addEventListener("click", exportManagerData);
}
if (els.importData) {
  els.importData.addEventListener("click", importManagerData);
}
if (els.librarySort) {
  els.librarySort.addEventListener("change", () => {
    state.listSort = els.librarySort.value;
    renderLibrary();
  });
}
if (els.chipGroup) {
  els.chipGroup.addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) {
      return;
    }
    state.listFilter = chip.dataset.filter || "all";
    renderLibrary();
  });
}
if (els.dashTodo) {
  els.dashTodo.addEventListener("click", (event) => {
    const row = event.target.closest(".todo-row");
    if (!row || !row.dataset.goto) {
      return;
    }
    state.listFilter = row.dataset.goto;
    document.querySelector('.nav-item[data-view="library"]').click();
    if (els.librarySort) {
      els.librarySort.value = "files";
      state.listSort = "files";
    }
    renderLibrary();
  });
}
els.scanCovers.addEventListener("click", () => scanLibrary({ withThumbnails: true, thumbnailLimit: 300 }));
if (els.coverNoticeButton) {
  els.coverNoticeButton.addEventListener("click", () => scanLibrary({ withThumbnails: true, thumbnailLimit: 300 }));
}
els.scanAllCovers.addEventListener("click", () => scanLibrary({ withThumbnails: true, thumbnailLimit: 0, allCovers: true }));
els.cancelScan.addEventListener("click", async () => {
  els.cancelScan.disabled = true;
  els.scanCovers.disabled = false;
  els.scanAllCovers.disabled = false;
  await window.fileTidier.cancelScan();
  setStatus("중지 요청됨");
});
els.librarySearch.addEventListener("input", () => {
  renderLibrary();
  renderReviews();
});
els.toggleCardView.addEventListener("click", () => {
  state.cardView = !state.cardView;
  els.toggleCardView.textContent = state.cardView ? "책장으로" : "목록으로";
  renderLibrary();
});
els.buildLatest.addEventListener("click", renderLatest);
els.libraryList.addEventListener("input", (event) => {
  const title = event.target.dataset.title;
  const field = event.target.dataset.meta;
  if (!title || !field) {
    return;
  }
  metadataFor(title)[field] = event.target.value;
  saveStore();
  setStatus("작품 메타데이터 로컬 저장됨");
  renderMetrics();
  renderRecommend();
});
els.libraryList.addEventListener("click", (event) => {
  const detailButton = event.target.closest("[data-show-files]");
  if (detailButton) {
    showWorkDetail(detailButton.dataset.showFiles);
    return;
  }
  const title = event.target.dataset.title;
  const toggle = event.target.dataset.toggle;
  if (!title || !toggle) {
    return;
  }
  const meta = metadataFor(title);
  meta[toggle] = !meta[toggle];
  saveStore();
  setStatus(`${toggle === "read" ? "읽음 상태" : "즐겨찾기"} 로컬 저장됨`);
  renderAll();
});
els.reviewTargetList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-review-title]");
  if (!button) {
    return;
  }
  state.selectedReviewTitle = button.dataset.reviewTitle;
  const meta = metadataFor(state.selectedReviewTitle);
  els.reviewTitle.textContent = state.selectedReviewTitle;
  els.reviewMemo.value = meta.reviewMemo || "";
});
els.openReviewSearch.addEventListener("click", async () => {
  if (!state.selectedReviewTitle) {
    setStatus("리뷰 확인할 작품을 선택하세요.", "error");
    return;
  }
  const query = encodeURIComponent(`${state.selectedReviewTitle} 텍본 리뷰 평점 댓글 수`);
  await window.fileTidier.openExternal(`https://www.google.com/search?q=${query}`);
});
els.saveReviewMemo.addEventListener("click", () => {
  if (!state.selectedReviewTitle) {
    setStatus("리뷰 메모를 저장할 작품을 선택하세요.", "error");
    return;
  }
  metadataFor(state.selectedReviewTitle).reviewMemo = els.reviewMemo.value;
  saveStore();
  renderAll();
  setStatus("리뷰 메모 저장됨");
});
els.buildPrompt.addEventListener("click", () => {
  buildPrompt();
  setStatus("AI 추천 프롬프트 생성됨");
});
els.saveGlobalMemo.addEventListener("click", () => {
  saveStore();
  setStatus("메모 저장됨");
});
els.saveRejectList.addEventListener("click", () => {
  state.rejectList = els.rejectList.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  saveStore();
  renderMetrics();
  renderRejectMatches();
  setStatus("보관거부 목록 저장됨");
});
els.saveTrackList.addEventListener("click", () => {
  state.trackList = els.trackList.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  saveStore();
  renderTrackMatches();
  setStatus("추적 목록 저장됨");
});
els.compareExternalList.addEventListener("click", renderExternalCompare);
els.fetchWebCovers.addEventListener("click", fetchWebCovers);
els.buildFolderReport.addEventListener("click", renderFolderReport);
els.workDetailModal.addEventListener("click", (event) => {
  if (event.target.closest("[data-close-detail]")) {
    closeWorkDetail();
  }
});

async function initialize() {
  if (window.fileTidier.onScanProgress) {
    window.fileTidier.onScanProgress((progress) => updateProgress(progress));
  }
  await loadStore();
  renderAll();
  buildPrompt();
  if (state.items.length) {
    setStatus(`저장된 목록 복원 · 작품 ${state.works.length}개 · 파일 ${state.items.length}개`);
  }
}

initialize();
