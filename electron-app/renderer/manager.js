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
  checkIntegrity: document.querySelector("#checkIntegrity"),
  integrityResult: document.querySelector("#integrityResult"),
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

function renderMetrics() {
  els.metricWorks.textContent = state.works.length;
  els.metricFiles.textContent = state.items.length;
  els.metricDuplicates.textContent = duplicateCount();
  els.metricRejects.textContent = rejectMatches().length;
}

function filteredWorks() {
  const query = els.librarySearch.value.trim().toLocaleLowerCase("ko-KR");
  if (!query) {
    return state.works;
  }
  return state.works.filter((work) => {
    const meta = metadataFor(work.title);
    return (
      work.title.toLocaleLowerCase("ko-KR").includes(query) ||
      work.extensions.join(" ").includes(query) ||
      String(meta.tags || "").toLocaleLowerCase("ko-KR").includes(query)
    );
  });
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
  const missing = state.works.filter(
    (work) => !work.thumbnail && work.extensions.some((ext) => [".epub", ".zip", ".cbz"].includes(ext)),
  ).length;
  if (!missing) {
    els.coverNotice.hidden = true;
    return;
  }
  els.coverNotice.querySelector("span").textContent =
    `표지를 아직 안 읽은 작품이 ${missing}개 있습니다. 표지는 파일을 다시 읽어야 해서 '작품 불러오기' 로는 오지 않습니다.`;
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

function renderLibrary() {
  const works = filteredWorks();
  els.libraryMeta.textContent = `작품 ${works.length}개 · 파일 ${state.items.length}개 · 중복 후보 ${duplicateCount()}개`;
  updateCoverNotice();
  if (!works.length) {
    els.libraryList.innerHTML = `<div class="recommend-box">표시할 작품이 없습니다.</div>`;
    return;
  }
  els.libraryList.innerHTML = works
    .map((work) => {
      const meta = metadataFor(work.title);
      const cardClass = state.cardView ? " card-mode" : "";
      const cover = work.thumbnail
        ? `<div class="book-cover"><img src="${escapeHtml(fileUrl(work.thumbnail))}" alt="" loading="lazy" /></div>`
        : `<div class="book-cover placeholder"><span>${escapeHtml((work.extensions[0] || "file").replace(".", ""))}</span></div>`;
      const authorText = work.author ? `작가 ${work.author} · ` : "";
      const completeText = work.isComplete ? " · 완결" : "";
      const sourceHint = work.sourceHints.includes("published")
        ? `<span class="source-pill published">정식 EPUB 추정</span>`
        : work.sourceHints.includes("personal")
          ? `<span class="source-pill personal">개인 변환본 추정</span>`
          : "";
      return `
        <article class="work-row${cardClass}" data-title="${escapeHtml(work.title)}">
          ${cover}
          <div class="work-main">
            <strong title="${escapeHtml(work.title)}">${escapeHtml(work.title)}</strong>
            <span class="work-meta">${escapeHtml(authorText)}${escapeHtml(work.files.length)}개 파일 · ${escapeHtml(work.extensions.join(", "))}${episodeSuffix(work)}${completeText}${sourceHint}${metaBadges(meta)}</span>
          </div>
          <input class="rating-input" data-meta="rating" data-title="${escapeHtml(work.title)}" value="${escapeHtml(meta.rating)}" placeholder="평점" />
          <input class="tag-input" data-meta="tags" data-title="${escapeHtml(work.title)}" value="${escapeHtml(meta.tags)}" placeholder="태그" />
          <button class="mini-toggle ${meta.read ? "on" : ""}" data-toggle="read" data-title="${escapeHtml(work.title)}" type="button">${meta.read ? "읽음" : "안 읽음"}</button>
          <button class="mini-toggle ${meta.favorite ? "on" : ""}" data-toggle="favorite" data-title="${escapeHtml(work.title)}" type="button">찜</button>
          <button class="mini-toggle detail-button" data-show-files="${escapeHtml(work.key)}" type="button">파일 ${escapeHtml(work.files.length)}</button>
        </article>
      `;
    })
    .join("");
}

function renderLatest() {
  const candidates = state.works
    .filter((work) => work.files.length > 1 || work.extensions.length > 1 || work.latestEpisode > 0)
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
                <strong>${escapeHtml(work.title)}</strong>
                <span>${escapeHtml(meta.tags || "태그 없음")}</span>
              </div>
              <span>${escapeHtml(meta.rating || "평점 -")}</span>
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
      .filter((match) => match.score >= 0.62)
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
      return `
        <article class="compare-row">
          <div>
            <strong>${escapeHtml(keyword)}</strong>
            <span>${escapeHtml(preview || "일치 없음")}</span>
          </div>
          <span>${matches.length}개</span>
          <span>${matches.some((work) => metadataFor(work.title).favorite) ? "찜 있음" : "-"}</span>
          <span>${matches.some((work) => metadataFor(work.title).read) ? "읽음 있음" : "-"}</span>
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
  const candidates = [...state.works]
    .sort((a, b) => {
      const am = metadataFor(a.title);
      const bm = metadataFor(b.title);
      return Number(bm.favorite) - Number(am.favorite) || Number(bm.rating || 0) - Number(am.rating || 0);
    })
    .slice(0, 9);
  els.recommendCandidates.innerHTML = candidates
    .map((work) => {
      const meta = metadataFor(work.title);
      return `
        <article class="candidate-card">
          <strong>${escapeHtml(work.title)}</strong>
          <span>${escapeHtml(meta.tags || "태그 없음")} · ${escapeHtml(meta.rating || "평점 -")}</span>
        </article>
      `;
    })
    .join("");
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

async function checkIntegrity() {
  if (!window.fileTidier.getAppIntegrity) {
    els.integrityResult.innerHTML = `<p>무결성 계산 기능을 찾지 못했습니다.</p>`;
    return;
  }
  els.checkIntegrity.disabled = true;
  els.integrityResult.innerHTML = `<p>주요 앱 파일 해시 계산 중...</p>`;
  try {
    const payload = await window.fileTidier.getAppIntegrity();
    const badFiles = (payload.files || []).filter((file) => !file.ok);
    els.integrityResult.innerHTML = `
      <strong>앱 해시 ${escapeHtml((payload.hash || "").slice(0, 16))}...</strong>
      <p>${escapeHtml(payload.appPath || "")}</p>
      <p>${badFiles.length ? `읽지 못한 파일 ${badFiles.length}개` : `주요 파일 ${payload.files?.length || 0}개 확인`}</p>
      <details>
        <summary>파일별 해시 보기</summary>
        <ul class="hash-list">
          ${(payload.files || [])
            .map((file) => `<li><span>${escapeHtml(file.path)}</span><code>${escapeHtml(file.ok ? file.hash.slice(0, 16) : file.error)}</code></li>`)
            .join("")}
        </ul>
      </details>
    `;
    setStatus("무결성 계산 완료");
  } catch (error) {
    els.integrityResult.innerHTML = `<p>${escapeHtml(error?.message || String(error))}</p>`;
    setStatus("무결성 계산 실패", "error");
  } finally {
    els.checkIntegrity.disabled = false;
  }
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
  els.toggleCardView.textContent = state.cardView ? "리스트뷰" : "카드뷰";
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
els.checkIntegrity.addEventListener("click", checkIntegrity);
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
