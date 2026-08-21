const state = {
  folder: "",
  referenceZip: "",
  mode: "catalog",
  skipped: [],
  titleRows: [],
  titleRowsMode: "titles",
  scanStartedAt: null,
  latestProgress: null,
  scanTimer: null,
  progressStep: "",
  progressStepStartedAt: null,
  collapsedComprehensiveGroups: new Set(),
  confidentOnly: false,
  analysisPayload: null,
};

const els = {
  chooseFolder: document.querySelector("#chooseFolder"),
  chooseReferenceZip: document.querySelector("#chooseReferenceZip"),
  folderText: document.querySelector("#folderText"),
  referenceZipText: document.querySelector("#referenceZipText"),
  query: document.querySelector("#queryInput"),
  limit: document.querySelector("#limitInput"),
  minSize: document.querySelector("#minSizeInput"),
  extensions: document.querySelector("#extensionsInput"),
  mode: document.querySelector("#modeSelect"),
  recursive: document.querySelector("#recursiveInput"),
  includeZip: document.querySelector("#zipInput"),
  scan: document.querySelector("#scanButton"),
  cancel: document.querySelector("#cancelButton"),
  previewInput: document.querySelector("#previewInput"),
  previewOutput: document.querySelector("#previewOutput"),
  renameAuthor: document.querySelector("#renameAuthor"),
  renameAuthorPattern: document.querySelector("#renameAuthorPattern"),
  renameFind: document.querySelector("#renameFind"),
  renameReplace: document.querySelector("#renameReplace"),
  renamePosition: document.querySelector("#renamePosition"),
  renameCase: document.querySelector("#renameCase"),
  renamePrefix: document.querySelector("#renamePrefix"),
  renameSuffix: document.querySelector("#renameSuffix"),
  renameStart: document.querySelector("#renameStart"),
  renamePadding: document.querySelector("#renamePadding"),
  renameRegex: document.querySelector("#renameRegex"),
  renameStripCopy: document.querySelector("#renameStripCopy"),
  renameAutoAuthor: document.querySelector("#renameAutoAuthor"),
  renameNormalizeTitle: document.querySelector("#renameNormalizeTitle"),
  status: document.querySelector("#statusBadge"),
  tableTitle: document.querySelector("#tableTitle"),
  tableMeta: document.querySelector("#tableMeta"),
  resultHead: document.querySelector("#resultHead"),
  resultBody: document.querySelector("#resultBody"),
  resultTable: document.querySelector("#resultsPanel table"),
  skippedBody: document.querySelector("#skippedBody"),
  resultsPanel: document.querySelector("#resultsPanel"),
  skippedPanel: document.querySelector("#skippedPanel"),
  resultActions: document.querySelector("#resultActions"),
  confidentOnly: document.querySelector("#confidentOnlyButton"),
  selectDuplicateCandidates: document.querySelector("#selectDuplicateCandidatesButton"),
  clearTitleSelection: document.querySelector("#clearTitleSelectionButton"),
  quarantineSelected: document.querySelector("#quarantineSelectedButton"),
  applyRename: document.querySelector("#applyRenameButton"),
  openManager: document.querySelector("#openManagerButton"),
  analysisModal: document.querySelector("#analysisModal"),
  analysisTitle: document.querySelector("#analysisTitle"),
  analysisClose: document.querySelector("#analysisCloseButton"),
  analysisSummary: document.querySelector("#analysisSummary"),
  analysisCompare: document.querySelector("#analysisCompare"),
  analysisMetrics: document.querySelector("#analysisMetrics"),
  analysisSections: document.querySelector("#analysisSections"),
  analysisRaw: document.querySelector("#analysisRaw"),
  analysisDecision: document.querySelector("#analysisDecision"),
  advancedOptions: document.querySelector("#advancedOptions"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function normalizeTitle(name) {
  const filename = String(name).replaceAll("\\", "/").split("/").pop() || "";
  const withoutExt = filename.replace(/\.[^/.]+$/, "");
  return withoutExt
    .replace(/^(?:\s*(?:\[[^\]]+\]|\([^)]+\)|\{[^}]+\}|【[^】]+】|〔[^〕]+〕|（[^）]+）)\s*)+/g, "")
    .replace(/[_\-.]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function fileTypeToken(extension) {
  const value = String(extension || "").replace(/^\./, "").toUpperCase();
  if (value === "EPUB") return "E";
  if (value === "TXT") return "T";
  if (value === "ZIP" || value === "CBZ") return "Z";
  return value.slice(0, 1) || "F";
}

function bookPartToken(name) {
  const base = String(name || "").replace(/\.[^/.]+$/, "").replace(/\s*\(\d+\)\s*$/, "");
  const extraMatch = base.match(/(?:외전|번외|사이드\s*스토리)\s*[-_ ]*(\d+)?/i);
  if (extraMatch) {
    return { label: `E${extraMatch[1] || "1"}`, className: "extra" };
  }
  const volumeRangeMatch = base.match(/(?:^|[^0-9])(\d{1,3})\s*[~-]\s*(\d{1,3})\s*권/i);
  if (volumeRangeMatch) {
    return { label: `M${volumeRangeMatch[1]}-${volumeRangeMatch[2]}`, className: "main" };
  }
  const volumeMatch = base.match(/(?:^|[^0-9])(\d+)\s*권(?:\s*완결)?/i);
  if (volumeMatch) {
    return { label: `M${volumeMatch[1]}`, className: "main" };
  }
  const trailingVolumeMatch = base.match(/(?:^|\s)(\d{1,3})\s*(?:완결|완)?$/i);
  if (trailingVolumeMatch) {
    return { label: `M${trailingVolumeMatch[1]}`, className: "main" };
  }
  return null;
}

function resultToneClass(item, index = 0) {
  if (item?.healthBroken || item?.confidence === "검토") return "tone-review";
  if (item?.keep) return "tone-keep";
  if (item?.autoSelect || item?.confidence === "확실") return "tone-match";
  return `tone-${(index % 4) + 1}`;
}

function catalogExtensionRank(extension) {
  const order = { ".epub": 0, ".txt": 1, ".zip": 2, ".cbz": 3 };
  return order[String(extension || "").toLowerCase()] ?? 9;
}

function catalogPartRank(item) {
  const part = bookPartToken(item.name);
  if (!part) return [2, Number.MAX_SAFE_INTEGER];
  const number = Number.parseInt(part.label.match(/\d+/)?.[0] || "0", 10);
  return [part.className === "main" ? 0 : 1, number];
}

function catalogSeriesKey(item) {
  return String(item.seriesTitle || item.displayTitle || item.title || item.name || "").trim();
}

function groupToken(group) {
  const value = String(group ?? "-").replace(/^[A-Za-z]+/, "");
  return `H${value || "-"}`;
}

function groupColorClass(group) {
  const number = Number.parseInt(String(group ?? "").match(/\d+/)?.[0] || "0", 10);
  return `group-color-${number % 4}`;
}

function setStatus(text, type = "") {
  els.status.textContent = text;
  els.status.className = `status ${type}`.trim();
}

function formatElapsed(ms) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) {
    return `${seconds}초`;
  }
  return `${Math.floor(seconds / 60)}분 ${seconds % 60}초`;
}

function modeDisplayTitle(mode) {
  const titles = {
    catalog: "파일 목록",
    "duplicates-size": "크기 중복",
    "duplicates-content": "내용 중복",
    "duplicates-comprehensive": "종합 중복 정리",
    "zip-internal-hashes": "ZIP 종합 중복 정리",
    "text-duplicates": "문장 중복",
    "reference-sentences": "참조 문장 확인",
    titles: "제목 중복 확인",
    "rename-preview": "이름 변경 미리보기",
  };
  return titles[mode] || "파일 목록";
}

function progressStatusText(progress) {
  const elapsed = state.scanStartedAt ? formatElapsed(Date.now() - state.scanStartedAt) : "0초";
  const step = progress?.step || "스캔 중";
  const total = Number(progress?.total || 0);
  const current = Number(progress?.current || 0);
  const detail = progress?.detail ? ` · ${progress.detail}` : "";
  if (total > 0) {
    const phaseSeconds = state.progressStepStartedAt
      ? Math.max(0.001, (Date.now() - state.progressStepStartedAt) / 1000)
      : 0;
    const rate = current > 0 && phaseSeconds > 0 ? current / phaseSeconds : 0;
    const remainingSeconds = rate > 0 && current < total ? (total - current) / rate : 0;
    const rateText = rate > 0 ? ` · 초당 ${rate < 10 ? rate.toFixed(1) : Math.round(rate)}개` : "";
    const etaText = remainingSeconds > 0 ? ` · 약 ${formatElapsed(remainingSeconds * 1000)} 남음` : "";
    return `${step} · ${current}/${total}개 · ${elapsed}${rateText}${etaText}${detail}`;
  }
  return `${step} · ${elapsed}${detail}`;
}

function prepareScanProgress() {
  hideResultActions();
  els.resultTable.className = "scan-progress-table";
  els.tableTitle.textContent = modeDisplayTitle(state.mode);
  els.tableMeta.textContent = "";
  els.resultHead.innerHTML = "";
  els.resultBody.innerHTML = `
    <tr class="scan-progress-row">
      <td colspan="13">
        <div class="scan-progress-content">
          <strong>분석 준비 중</strong>
          <span>검사할 파일을 확인하고 있습니다.</span>
          <div class="scan-progress-track"><i></i></div>
        </div>
      </td>
    </tr>
  `;
}

function updateScanProgressPanel(progress, statusText) {
  const content = els.resultBody.querySelector(".scan-progress-content");
  if (!content) return;
  const total = Number(progress?.total || 0);
  const current = Number(progress?.current || 0);
  const percent = total > 0 ? Math.min(100, Math.max(0, (current / total) * 100)) : 0;
  content.querySelector("strong").textContent = progress?.step || "분석 중";
  content.querySelector("span").textContent = statusText;
  const bar = content.querySelector("i");
  bar.style.width = total > 0 ? `${percent}%` : "24%";
  bar.classList.toggle("indeterminate", total <= 0);
}

function updateProgressStatus(progress = state.latestProgress) {
  if (!state.scanStartedAt) {
    return;
  }
  const nextProgress = progress || state.latestProgress || { step: "스캔 중" };
  if (nextProgress.step && nextProgress.step !== state.progressStep) {
    state.progressStep = nextProgress.step;
    state.progressStepStartedAt = Date.now();
  }
  state.latestProgress = nextProgress;
  const statusText = progressStatusText(state.latestProgress);
  setStatus(statusText, "loading");
  updateScanProgressPanel(state.latestProgress, statusText);
}

function startProgressTimer() {
  state.scanStartedAt = Date.now();
  state.progressStep = "";
  state.progressStepStartedAt = Date.now();
  state.latestProgress = { step: "스캔 준비 중" };
  prepareScanProgress();
  updateProgressStatus();
  if (state.scanTimer) {
    clearInterval(state.scanTimer);
  }
  state.scanTimer = setInterval(() => updateProgressStatus(), 1000);
}

function stopProgressTimer() {
  state.scanStartedAt = null;
  state.latestProgress = null;
  state.progressStep = "";
  state.progressStepStartedAt = null;
  if (state.scanTimer) {
    clearInterval(state.scanTimer);
    state.scanTimer = null;
  }
}

function setReferenceZip(zipPath) {
  state.referenceZip = zipPath;
  els.referenceZipText.textContent = zipPath;
  els.referenceZipText.title = zipPath;
  setStatus("참조 zip 선택됨");
}

function setActiveTab(tabName) {
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tabName);
  });
  els.resultsPanel.classList.toggle("hidden", tabName !== "results");
  els.skippedPanel.classList.toggle("hidden", tabName !== "skipped");
}

function renderPreview() {
  els.previewOutput.textContent = `→ ${normalizeTitle(els.previewInput.value) || "(제목 없음)"}`;
}

function renderSkipped(skipped) {
  state.skipped = skipped || [];
  if (state.skipped.length === 0) {
    els.skippedBody.innerHTML = `<tr><td colspan="2" class="path">스킵된 압축파일이 없습니다.</td></tr>`;
    return;
  }
  els.skippedBody.innerHTML = state.skipped
    .map(
      (item) => `
        <tr>
          <td title="${escapeHtml(item.path)}">${escapeHtml(item.path)}</td>
          <td title="${escapeHtml(item.reason)}">${escapeHtml(item.reason)}</td>
        </tr>
      `,
    )
    .join("");
}

function resetResultScroll() {
  const tableWrap = els.resultTable?.closest(".table-wrap");
  if (!tableWrap) return;
  requestAnimationFrame(() => {
    tableWrap.scrollTop = 0;
    tableWrap.scrollLeft = 0;
  });
}

function renderCatalog(payload) {
  hideResultActions();
  els.resultTable.className = "catalog-table";
  els.tableTitle.textContent = "파일 목록";
  els.tableMeta.textContent = `FILES (${payload.shown ?? 0}) · 전체 ${payload.total ?? 0}개`;
  els.resultHead.innerHTML = `
    <tr>
      <th>파일</th>
      <th>뽑은 제목</th>
      <th>확장자</th>
      <th>종류</th>
      <th>크기</th>
      <th>수정일</th>
      <th>위치</th>
    </tr>
  `;
  const items = [...(payload.items || [])].sort((left, right) => {
    const seriesCompare = catalogSeriesKey(left).localeCompare(catalogSeriesKey(right), "ko", { numeric: true });
    if (seriesCompare) return seriesCompare;
    const extensionCompare = catalogExtensionRank(left.extension) - catalogExtensionRank(right.extension);
    if (extensionCompare) return extensionCompare;
    const leftPart = catalogPartRank(left);
    const rightPart = catalogPartRank(right);
    return leftPart[0] - rightPart[0]
      || leftPart[1] - rightPart[1]
      || String(left.name || "").localeCompare(String(right.name || ""), "ko", { numeric: true });
  });
  els.resultBody.innerHTML = items
    .map((item, index) => {
      const part = bookPartToken(item.name);
      const seriesKey = catalogSeriesKey(item);
      const previousSeriesKey = index > 0 ? catalogSeriesKey(items[index - 1]) : "";
      const seriesChanged = index === 0 || previousSeriesKey !== seriesKey;
      const extensionChanged = seriesChanged || items[index - 1]?.extension !== item.extension;
      return `
        <tr class="result-row ${resultToneClass(item, index)} ${seriesChanged ? "series-start" : "series-child"} ${extensionChanged ? "extension-start" : "extension-child"}">
          <td title="${escapeHtml(`${item.name}\n${item.location || ""}`)}">
            <div class="file-cell">
              <span class="file-token ${extensionChanged ? "" : "ghost"}" title="${escapeHtml(item.extension || "-")}">${extensionChanged ? escapeHtml(fileTypeToken(item.extension)) : ""}</span>
              ${part ? `<span class="part-token ${part.className}">${escapeHtml(part.label)}</span>` : ""}
              <span class="file-name">${escapeHtml(item.name)}</span>
            </div>
          </td>
          <td title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</td>
          <td class="ext">${escapeHtml(item.extension || "-")}</td>
          <td><span class="badge">${escapeHtml(item.kind)}</span></td>
          <td>${escapeHtml(item.sizeText)}</td>
          <td>${escapeHtml(item.modified || "-")}</td>
          <td class="path" title="${escapeHtml(item.location)}">${escapeHtml(item.location)}</td>
        </tr>
      `;
    })
    .join("");
  resetResultScroll();
}

function renderTitles(payload) {
  els.selectDuplicateCandidates.textContent = "중복 후보 선택";
  els.resultTable.className = "title-table";
  els.tableTitle.textContent = "제목 중복 확인";
  const groupCount = payload.groups ?? new Set((payload.items || []).map((item) => item.group)).size;
  const totalCount = payload.total ?? (payload.items || []).length;
  const shownCount = payload.shown ?? (payload.items || []).length;
  els.tableMeta.textContent = `같은 제목 + 같은 확장자 ${groupCount}그룹, 전체 ${totalCount}개 중 ${shownCount}개 표시`;
  state.titleRows = payload.items || [];
  state.titleRowsMode = "titles";
  renderTitleRows();
  resetResultScroll();
}

function syncModeControls() {
  document.querySelectorAll("[data-mode-value]").forEach((button) => {
    button.classList.toggle("active", button.dataset.modeValue === els.mode.value);
  });
  document.body.dataset.mode = els.mode.value;
  if (els.mode.value === "rename-preview" && els.advancedOptions) {
    els.advancedOptions.open = true;
  }
}

function renderComprehensiveDuplicates(payload) {
  els.selectDuplicateCandidates.textContent = "중복 후보 선택";
  els.resultTable.className = "title-table comprehensive-table";
  els.tableTitle.textContent = "종합 중복 정리";
  const groupCount = payload.groups ?? new Set((payload.items || []).map((item) => item.group)).size;
  const totalCount = payload.total ?? (payload.items || []).length;
  const shownCount = payload.shown ?? (payload.items || []).length;
  const stats = payload.analysisStats || {};
  const zipCacheText = (stats.zipCacheHits || stats.zipCacheMisses)
    ? ` · ZIP 캐시 ${stats.zipCacheHits || 0}개 재사용/${stats.zipCacheMisses || 0}개 새 검사`
    : "";
  const statsText = stats.scannedFiles
    ? ` · 스캔 ${stats.scannedFiles}개 → 표시 ${totalCount}개 · 해시일치 ${stats.hashMatchedRows || 0}개 · 검토 ${stats.reviewRows || 0}개 · 선택가능 ${stats.selectableRows || 0}개${zipCacheText}`
    : "";
  els.tableMeta.textContent = `제목/해시/본문 종합 ${groupCount}그룹, 전체 ${totalCount}개 중 ${shownCount}개 표시${statsText} · 기본 기준은 가장 오래된 파일 · 격리는 같은 확장자 + 해시 일치 파일만 가능`;
  state.titleRows = payload.items || [];
  state.titleRowsMode = "comprehensive";
  state.confidentOnly = false;
  syncConfidentOnlyButton();
  const groupCounts = new Map();
  state.titleRows.forEach((item) => {
    const group = String(item.zipDuplicateGroup || item.group);
    groupCounts.set(group, (groupCounts.get(group) || 0) + 1);
  });
  state.collapsedComprehensiveGroups = new Set(
    [...groupCounts.entries()].filter(([, count]) => count > 1).map(([group]) => group),
  );
  renderComprehensiveRows();
  resetResultScroll();
}

function renderTitleRows() {
  const keepByGroup = new Map();
  state.titleRows.forEach((item) => {
    if (item.keep) {
      keepByGroup.set(item.group, item);
    }
  });
  showTitleActions();
  els.resultHead.innerHTML = `
    <tr>
      <th>선택 · 그룹</th>
      <th>남길 후보</th>
      <th>뽑은 제목</th>
      <th>원래 이름</th>
      <th>확장자</th>
      <th>종류</th>
      <th>크기</th>
      <th>분석</th>
      <th>수정일</th>
      <th>위치</th>
    </tr>
  `;
  els.resultBody.innerHTML = state.titleRows
    .map((item, index) => {
      const keep = keepByGroup.get(item.group);
      const groupLead = index === 0 || String(state.titleRows[index - 1]?.group) !== String(item.group);
      return `
        <tr class="result-row ${resultToneClass(item, index)} ${groupLead ? "group-lead" : "group-child"}">
          <td class="selection-group-cell">
            <div class="selection-group-inner">
              <input
                class="row-check title-quarantine-check"
                type="checkbox"
                data-location="${escapeHtml(item.location)}"
                data-duplicate="${!item.keep && item.kind === "file" ? "true" : "false"}"
                ${item.kind === "file" ? "" : "disabled"}
                title="${item.kind === "file" ? "격리 대상" : "zip 내부 항목은 격리 제외"}"
              />
              <span class="group-visual">
                <span class="group-line-dot" aria-hidden="true"></span>
                <span class="group-token ${groupColorClass(item.group)}">${escapeHtml(groupToken(item.group))}</span>
              </span>
            </div>
          </td>
          <td><span class="badge ${item.keep ? "keep" : "dup"}">${escapeHtml(item.keepText)}</span></td>
          <td title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</td>
          <td title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</td>
          <td class="ext">${escapeHtml(item.extension || "-")}</td>
          <td><span class="badge">${escapeHtml(item.kind)}</span></td>
          <td>${escapeHtml(item.sizeText)}</td>
          <td>${titleAnalysisControl(item, keep)}</td>
          <td>${escapeHtml(item.modified || "-")}</td>
          <td class="path" title="${escapeHtml(item.location)}">${escapeHtml(item.location)}</td>
        </tr>
      `;
    })
    .join("");
}

function confidenceClass(confidence) {
  if (confidence === "확실") return "keep";
  if (confidence === "높음") return "warn";
  return "dup";
}

function hashEvidenceCell(item) {
  if (item.hash && item.matchesKeepHash === false) {
    return `<span class="hash-pill muted" title="이 파일은 다른 위치의 파일과는 SHA-256이 일치하지만, 현재 남김 기준 파일과는 해시가 다릅니다.">기준과 해시 다름</span>`;
  }
  if (item.sourceType === "zip archive" && item.hash) {
    return `<span class="hash-pill ok" title="zip 내부 일치 SHA-256: ${escapeHtml(item.hash)}">내부 해시 일치</span>`;
  }
  if (item.sourceType === "zip item" && item.hash) {
    return `<span class="hash-pill ok" title="zip 내부 파일 SHA-256: ${escapeHtml(item.hash)}">zip 내부 해시</span>`;
  }
  if (item.sourceType === "file" && item.hash && String(item.evidence || "").includes("zip 내부")) {
    return `<span class="hash-pill ok" title="압축 밖 파일이 zip 내부 파일과 SHA-256 완전 일치: ${escapeHtml(item.hash)}">zip 내부와 일치</span>`;
  }
  if (item.hash) {
    return `<span class="hash-pill ok" title="SHA-256 완전 일치: ${escapeHtml(item.hash)}">해시 일치</span>`;
  }
  return `<span class="hash-pill muted" title="해시로 완전 일치가 확인되지 않았습니다. 제목/본문 근거는 참고용이며 격리 선택이 막힙니다.">해시 미확인</span>`;
}

function keepChoiceControl(item) {
  if (item.sourceType === "zip archive") {
    return zipKeepChoiceControl(item);
  }
  if (item.keep) {
    return `<span class="badge keep" title="현재 남길 파일 기준입니다.">${escapeHtml(item.keepText || "남김")}</span>`;
  }
  return `
    <button
      class="mini-keep"
      type="button"
      data-set-keep="true"
      data-group="${escapeHtml(item.group)}"
      data-location="${escapeHtml(item.location)}"
      title="이 파일명/형식을 남길 기준으로 선택합니다. 해시가 같은 파일만 격리 후보가 됩니다."
    >남김 선택</button>
  `;
}

function renderComprehensiveRows(checkedLocations = null) {
  const keepByGroup = new Map();
  const countByGroup = new Map();
  state.titleRows.forEach((item) => {
    if (item.keep) {
      keepByGroup.set(item.group, item);
    }
  });
  const filteredRows = state.confidentOnly
    ? state.titleRows.filter((item) => item.confidence === "확실")
    : state.titleRows;
  const groupOrder = new Map();
  const sourceOrder = new Map();
  filteredRows.forEach((item, index) => {
    const rowGroup = String(item.zipDuplicateGroup || item.group);
    if (!groupOrder.has(rowGroup)) {
      groupOrder.set(rowGroup, groupOrder.size);
    }
    sourceOrder.set(item.location, index);
  });
  const visibleRows = [...filteredRows].sort((left, right) => {
    const leftGroup = String(left.zipDuplicateGroup || left.group);
    const rightGroup = String(right.zipDuplicateGroup || right.group);
    const groupCompare = (groupOrder.get(leftGroup) || 0) - (groupOrder.get(rightGroup) || 0);
    if (groupCompare) return groupCompare;
    const keepCompare = Number(Boolean(right.keep)) - Number(Boolean(left.keep));
    if (keepCompare) return keepCompare;
    return (sourceOrder.get(left.location) || 0) - (sourceOrder.get(right.location) || 0);
  });
  visibleRows.forEach((item) => {
    const rowGroup = String(item.zipDuplicateGroup || item.group);
    countByGroup.set(rowGroup, (countByGroup.get(rowGroup) || 0) + 1);
  });
  showTitleActions();
  els.resultHead.innerHTML = `
    <tr>
      <th>선택 · 그룹</th>
      <th>원래 이름</th>
      <th>크기</th>
      <th>수정일</th>
      <th>판정</th>
      <th>처리</th>
      <th>근거</th>
      <th>뽑은 제목</th>
      <th>확장자</th>
      <th>해시 확인</th>
      <th>분석</th>
      <th>위치</th>
      <th>묶음</th>
    </tr>
  `;
  els.resultBody.innerHTML = visibleRows
    .map((item, index) => {
      const keep = keepByGroup.get(item.group);
      const rowGroup = item.zipDuplicateGroup || item.group;
      const previousGroup = visibleRows[index - 1]?.zipDuplicateGroup || visibleRows[index - 1]?.group;
      const groupLead = index === 0 || String(previousGroup) !== String(rowGroup);
      const groupKey = String(rowGroup);
      const groupCount = countByGroup.get(groupKey) || 1;
      const collapsed = state.collapsedComprehensiveGroups.has(groupKey);
      const isZipArchive = item.sourceType === "zip archive";
      const canMove = isZipArchive
        ? Boolean(item.autoSelect && !item.keep)
        : item.kind === "file" && Boolean(item.autoSelect && item.hash) && !item.keep;
      const moveTitle = isZipArchive
        ? canMove
          ? "zip 내부 확인 대상이 전부 같은 해시로 다른 위치에 있어 zip 파일 전체를 격리할 수 있습니다."
          : "zip 내부가 전부 일치하지 않거나 남김 후보라 격리 선택이 막힙니다."
        : item.kind !== "file"
        ? "zip 내부 항목은 격리 제외"
        : item.autoSelect && item.hash
          ? "현재 남김 기준과 확장자 및 SHA-256이 완전히 같은 실제 파일만 격리 대상"
          : item.hash
            ? "다른 위치와의 해시 일치는 확인됐지만 현재 남김 기준과 해시가 달라 격리 선택 불가"
            : "해시 일치가 확인되지 않아 격리 선택 불가";
      return `
        <tr class="result-row ${resultToneClass(item, index)} ${groupLead ? "group-lead" : "group-child"} ${!groupLead && collapsed ? "group-hidden" : ""}" data-row-group="${escapeHtml(groupKey)}">
          <td class="selection-group-cell">
            <div class="selection-group-inner">
              <input
                class="row-check title-quarantine-check"
                type="checkbox"
                data-location="${escapeHtml(item.location)}"
                data-duplicate="${item.autoSelect && !item.keep ? "true" : "false"}"
                ${canMove ? "" : "disabled"}
                title="${escapeHtml(moveTitle)}"
              />
              <span class="group-visual">
                <span class="group-line-dot" aria-hidden="true"></span>
                <span class="group-token ${groupColorClass(rowGroup)}">${escapeHtml(groupToken(rowGroup))}</span>
              </span>
            </div>
          </td>
          <td class="primary-file-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</td>
          <td class="numeric-cell">${escapeHtml(item.sizeText)}</td>
          <td class="date-cell">${escapeHtml(item.modified || "-")}</td>
          <td><span class="badge ${confidenceClass(item.confidence)}">${escapeHtml(item.confidence || "검토")}</span></td>
          <td>${keepChoiceControl(item)}</td>
          <td class="evidence-cell" title="${escapeHtml(item.evidence)}">${escapeHtml(item.evidence || "-")}</td>
          <td title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</td>
          <td class="ext">${escapeHtml(item.extension || "-")}</td>
          <td>${hashEvidenceCell(item)}</td>
          <td>${titleAnalysisControl(item, keep)}</td>
          <td class="path" title="${escapeHtml(item.location)}">${escapeHtml(item.location)}</td>
          <td class="group-count-cell">
            ${groupLead && groupCount > 1 ? `<button class="group-count-toggle ${collapsed ? "collapsed" : ""}" type="button" data-toggle-comprehensive-group="${escapeHtml(groupKey)}" aria-expanded="${collapsed ? "false" : "true"}">+${groupCount - 1}<span aria-hidden="true">⌄</span></button>` : ""}
          </td>
        </tr>
      `;
    })
    .join("");
  if (checkedLocations) {
    document.querySelectorAll(".title-quarantine-check").forEach((input) => {
      input.checked = !input.disabled && checkedLocations.has(input.dataset.location);
    });
  }
}

function syncConfidentOnlyButton() {
  if (!els.confidentOnly) return;
  els.confidentOnly.textContent = state.confidentOnly
    ? "전체 보기"
    : state.titleRowsMode === "zip-internal-hashes"
      ? "격리 가능 묶음만"
      : "확실만 보기";
  els.confidentOnly.classList.toggle("active", state.confidentOnly);
  els.confidentOnly.setAttribute("aria-pressed", state.confidentOnly ? "true" : "false");
}

function toggleConfidentOnly() {
  const checkedLocations = new Set(
    [...document.querySelectorAll(".title-quarantine-check:checked")]
      .map((input) => input.dataset.location)
      .filter(Boolean),
  );
  state.confidentOnly = !state.confidentOnly;
  syncConfidentOnlyButton();
  if (state.titleRowsMode === "zip-internal-hashes") {
    renderZipInternalHashes({
      items: state.titleRows,
      total: state.titleRows.length,
      shown: state.titleRows.length,
      stats: zipStatsFromRows(state.titleRows),
    });
    document.querySelectorAll(".title-quarantine-check").forEach((input) => {
      input.checked = !input.disabled && checkedLocations.has(input.dataset.location);
    });
  } else {
    renderComprehensiveRows(checkedLocations);
  }
  resetResultScroll();
}

function setComprehensiveKeep(group, location) {
  const checkedBefore = new Set(
    [...document.querySelectorAll(".title-quarantine-check:checked")]
      .map((input) => input.dataset.location)
      .filter(Boolean),
  );
  const selected = state.titleRows.find((item) => String(item.group) === String(group) && item.location === location);
  if (!selected) {
    setStatus("남길 후보를 찾지 못했습니다.", "error");
    return;
  }
  state.titleRows = state.titleRows.map((item) => {
    if (String(item.group) !== String(group)) {
      return item;
    }
    const keep = item.location === selected.location;
    const hashMatchedToSelected =
      !keep &&
      item.kind === "file" &&
      selected.kind === "file" &&
      item.hash &&
      selected.hash &&
      item.hash === selected.hash &&
      item.extension === selected.extension;
    return {
      ...item,
      keep,
      keepText: keep ? "남김" : hashMatchedToSelected ? "중복" : "검토",
      confidence: keep ? (selected.hash ? "확실" : "검토") : hashMatchedToSelected ? "확실" : "검토",
      matchesKeepHash: item.hash ? Boolean(keep || hashMatchedToSelected) : null,
      autoSelect: Boolean(hashMatchedToSelected),
    };
  });
  const selectable = state.titleRows.filter((item) => item.autoSelect).length;
  const review = state.titleRows.filter((item) => !item.hash || item.keepText === "검토").length;
  const groups = new Set(state.titleRows.map((item) => item.group));
  els.tableMeta.textContent = `제목/해시/본문 종합 ${groups.size}그룹, 현재 목록 ${state.titleRows.length}개 표시 · 해시일치 선택가능 ${selectable}개 · 검토 ${review}개`;
  renderComprehensiveRows();
  document.querySelectorAll(".title-quarantine-check").forEach((input) => {
    if (input.disabled) {
      return;
    }
    const row = state.titleRows.find((item) => item.location === input.dataset.location);
    if (row && String(row.group) === String(group)) {
      input.checked = input.dataset.duplicate === "true";
      return;
    }
    input.checked = checkedBefore.has(input.dataset.location);
  });
  const checkedCount = document.querySelectorAll(".title-quarantine-check:checked").length;
  setStatus(`그룹 ${group}의 남길 후보를 바꿨습니다. 현재 선택 ${checkedCount}개`);
}

function zipStatsFromRows(rows) {
  const zipRows = rows.filter((item) => item.sourceType === "zip archive");
  return {
    scannedZipArchives: zipRows.length,
    zipArchives: zipRows.length,
    zipAllMatched: zipRows.filter((item) => ["all-matched", "subset"].includes(item.zipStatus)).length,
    zipExternallyCovered: zipRows.filter((item) => item.memberCount > 0 && item.externalMatchedCount === item.memberCount).length,
    zipSelectable: zipRows.filter((item) => item.autoSelect).length,
  };
}

function setZipArchiveKeep(group, location) {
  const checkedBefore = new Set(
    [...document.querySelectorAll(".title-quarantine-check:checked")]
      .map((input) => input.dataset.location)
      .filter(Boolean),
  );
  const selected = state.titleRows.find(
    (item) => item.sourceType === "zip archive" && item.zipDuplicateGroup === group && item.location === location,
  );
  if (!selected) {
    setStatus("남길 zip 후보를 찾지 못했습니다.", "error");
    return;
  }
  state.titleRows = state.titleRows.map((item) => {
    if (item.sourceType !== "zip archive" || item.zipDuplicateGroup !== group) {
      return item;
    }
    const keep = item.location === selected.location;
    const allMatched = ["all-matched", "subset", "keep"].includes(item.zipStatus);
    return {
      ...item,
      keep,
      keepText: keep ? "남김" : allMatched ? "중복" : "검토",
      zipStatus: keep ? "keep" : item.zipStatus === "keep" ? "all-matched" : item.zipStatus,
      zipStatusText: keep ? "남김" : item.zipStatusText === "남김" ? "전부 일치" : item.zipStatusText,
      autoSelect: Boolean(!keep && allMatched),
    };
  });
  if (state.titleRowsMode === "comprehensive") {
    const selectable = state.titleRows.filter((item) => item.autoSelect).length;
    const review = state.titleRows.filter((item) => !item.hash || item.keepText === "검토").length;
    const groups = new Set(state.titleRows.map((item) => item.group));
    els.tableMeta.textContent = `제목/해시/본문/zip 내부 종합 ${groups.size}그룹, 현재 목록 ${state.titleRows.length}개 표시 · 격리 가능 ${selectable}개 · 검토 ${review}개`;
    renderComprehensiveRows();
  } else {
    renderZipInternalHashes({
      items: state.titleRows,
      total: state.titleRows.length,
      shown: state.titleRows.length,
      stats: zipStatsFromRows(state.titleRows),
    });
  }
  document.querySelectorAll(".title-quarantine-check").forEach((input) => {
    if (input.disabled) {
      return;
    }
    const row = state.titleRows.find((item) => item.location === input.dataset.location);
    if (row && row.sourceType === "zip archive" && row.zipDuplicateGroup === group) {
      input.checked = input.dataset.duplicate === "true";
      return;
    }
    input.checked = checkedBefore.has(input.dataset.location);
  });
  setStatus(`${group} 묶음의 남김 zip을 바꿨습니다.`);
}

function titleAnalysisControl(item, keep) {
  if (item.sourceType === "zip archive") {
    return zipAnalysisControl(item);
  }
  if (!keep) {
    return `<span class="diff-badge same" title="비교 기준이 없습니다.">-</span>`;
  }
  if (item.location === keep.location) {
    return `<span class="diff-badge keep" title="남길 파일 후보입니다.">기준</span>`;
  }
  const disabled = item.kind !== "file" || keep.kind !== "file";
  const title = disabled
    ? "zip 내부 항목은 아직 행별 분석에서 제외했습니다. zip 파일 자체를 비교해 주세요."
    : `기준 파일과 실제 내용을 비교합니다.\n기준: ${keep.name}\n후보: ${item.name}`;
  return `
    <button
      class="mini-analysis"
      type="button"
      data-compare-title="true"
      data-left="${escapeHtml(item.location)}"
      data-right="${escapeHtml(keep.location)}"
      ${disabled ? "disabled" : ""}
      title="${escapeHtml(title)}"
    >분석</button>
  `;
}

function removeQuarantinedTitleRows(moved) {
  const movedPaths = new Set((moved || []).map((item) => item.from));
  if (movedPaths.size === 0) {
    return;
  }
  if (state.titleRowsMode === "zip-internal-hashes") {
    state.titleRows = state.titleRows.filter((item) => !movedPaths.has(item.location) && !movedPaths.has(item.archive));
    renderZipInternalHashes({
      items: state.titleRows,
      total: state.titleRows.length,
      shown: state.titleRows.length,
      stats: {
        zipArchives: state.titleRows.filter((item) => item.sourceType === "zip archive").length,
        zipAllMatched: state.titleRows.filter((item) => ["all-matched", "subset"].includes(item.zipStatus)).length,
        zipSelectable: state.titleRows.filter((item) => item.autoSelect).length,
      },
    });
    return;
  }
  state.titleRows = state.titleRows.filter((item) => !movedPaths.has(item.location));
  const counts = new Map();
  state.titleRows.forEach((item) => counts.set(item.group, (counts.get(item.group) || 0) + 1));
  state.titleRows = state.titleRows.filter((item) => (counts.get(item.group) || 0) > 1);
  const groups = new Set(state.titleRows.map((item) => item.group));
  if (state.titleRowsMode === "comprehensive") {
    const selectable = state.titleRows.filter((item) => item.autoSelect).length;
    const review = state.titleRows.filter((item) => !item.hash).length;
    els.tableMeta.textContent = `제목/해시/본문 종합 ${groups.size}그룹, 현재 목록 ${state.titleRows.length}개 표시 · 해시일치 선택가능 ${selectable}개 · 검토 ${review}개`;
    renderComprehensiveRows();
  } else {
    els.tableMeta.textContent = `같은 제목 + 같은 확장자 ${groups.size}그룹, 현재 목록 ${state.titleRows.length}개 표시`;
    renderTitleRows();
  }
}

function hideResultActions() {
  els.resultActions.classList.add("hidden");
  els.confidentOnly?.classList.add("hidden");
  els.selectDuplicateCandidates.classList.add("hidden");
  els.clearTitleSelection.classList.add("hidden");
  els.quarantineSelected.classList.add("hidden");
  els.applyRename.classList.add("hidden");
}

function showTitleActions() {
  els.resultActions.classList.remove("hidden");
  els.confidentOnly?.classList.toggle(
    "hidden",
    !["comprehensive", "zip-internal-hashes"].includes(state.titleRowsMode),
  );
  els.selectDuplicateCandidates.classList.remove("hidden");
  els.clearTitleSelection.classList.remove("hidden");
  els.quarantineSelected.classList.remove("hidden");
  els.applyRename.classList.add("hidden");
}

function showRenameActions() {
  els.resultActions.classList.remove("hidden");
  els.confidentOnly?.classList.add("hidden");
  els.selectDuplicateCandidates.classList.add("hidden");
  els.clearTitleSelection.classList.add("hidden");
  els.quarantineSelected.classList.add("hidden");
  els.applyRename.classList.remove("hidden");
}

function formatByteDelta(delta) {
  if (delta === 0) {
    return "같음";
  }
  const abs = Math.abs(delta);
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = abs;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const number = unitIndex === 0 ? String(Math.round(value)) : value.toFixed(1);
  return `${delta > 0 ? "+" : "-"}${number} ${units[unitIndex]}`;
}

function titleCompareInfo(item, keep) {
  if (!keep) {
    return {
      text: "-",
      className: "same",
      tooltip: "비교 기준 없음",
    };
  }
  if (item.location === keep.location) {
    return {
      text: "기준",
      className: "keep",
      tooltip: `남길 후보\n파일: ${item.name}\n크기: ${item.sizeText}\n수정일: ${item.modified || "-"}`,
    };
  }
  const delta = Number(item.size || 0) - Number(keep.size || 0);
  return {
    text: formatByteDelta(delta),
    className: delta === 0 ? "same" : delta > 0 ? "larger" : "smaller",
    tooltip: [
      `남길 후보: ${keep.name}`,
      `기준 크기: ${keep.sizeText}`,
      `이 파일: ${item.name}`,
      `이 파일 크기: ${item.sizeText}`,
      `차이: ${formatByteDelta(delta)}`,
      `기준 수정일: ${keep.modified || "-"}`,
      `이 파일 수정일: ${item.modified || "-"}`,
    ].join("\n"),
  };
}

function renderDuplicates(payload, contentMode) {
  hideResultActions();
  els.resultTable.className = "";
  els.tableTitle.textContent = contentMode ? "\ub0b4\uc6a9 \uc911\ubcf5" : "\ud06c\uae30 \uc911\ubcf5";
  els.tableMeta.textContent = `\uc911\ubcf5 ${payload.groups ?? 0}\uadf8\ub8f9, \uc804\uccb4 ${payload.total ?? 0}\uac1c \uc911 ${payload.shown ?? 0}\uac1c \ud45c\uc2dc`;
  els.resultHead.innerHTML = `
    <tr>
      <th>\uadf8\ub8f9</th>
      <th>\ud30c\uc77c\uba85</th>
      <th>\ud655\uc7a5\uc790</th>
      <th>\ud06c\uae30</th>
      <th>\uc0c1\ud0dc</th>
      <th>${contentMode ? "\ud574\uc2dc" : "\uc218\uc815\uc77c"}</th>
      <th>\uc704\uce58</th>
    </tr>
  `;
  els.resultBody.innerHTML = (payload.items || [])
    .map(
      (item) => `
        <tr>
          <td>${escapeHtml(item.group)}</td>
          <td title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</td>
          <td class="ext">${escapeHtml(item.extension || "-")}</td>
          <td>${escapeHtml(item.sizeText)}</td>
          <td>
            <span class="health-badge ${item.healthBroken ? "broken" : ""}" title="${escapeHtml(item.healthDetail || "")}">
              ${escapeHtml(item.healthStatus || "\ud655\uc778 \uc548 \ub428")}
            </span>
          </td>
          <td>${escapeHtml(contentMode ? item.hash : new Date(item.modified * 1000).toLocaleString())}</td>
          <td class="path" title="${escapeHtml(item.location)}">${escapeHtml(item.location)}</td>
        </tr>
      `,
    )
    .join("");
}

function zipKeepChoiceControl(item) {
  if (item.sourceType !== "zip archive") {
    return `<span class="diff-badge same">-</span>`;
  }
  if (item.zipStatus === "superset") {
    return `<span class="badge keep" title="다른 ZIP의 전체 내용을 포함하는 보관 기준 ZIP입니다.">남김</span>`;
  }
  if (!item.canChooseKeep || !item.zipDuplicateGroup) {
    return item.autoSelect
      ? `<span class="badge dup" title="압축 안 된 파일로도 전부 확인되어 격리 후보입니다.">격리 후보</span>`
      : `<span class="badge warn">검토</span>`;
  }
  if (item.keep) {
    return `<span class="badge keep" title="이 zip을 같은 내부 구성 묶음에서 남깁니다.">남김</span>`;
  }
  return `
    <button
      class="mini-keep"
      type="button"
      data-set-zip-keep="true"
      data-group="${escapeHtml(item.zipDuplicateGroup)}"
      data-location="${escapeHtml(item.location)}"
      title="같은 내부 구성의 zip 묶음에서 이 파일을 남길 후보로 선택합니다."
    >남김 선택</button>
  `;
}

function zipAnalysisControl(item) {
  if (item.sourceType !== "zip archive") {
    return `<span class="diff-badge same">-</span>`;
  }
  return `
    <button
      class="mini-analysis"
      type="button"
      data-zip-analysis="true"
      data-location="${escapeHtml(item.location)}"
      title="zip 내부 파일이 무엇과 일치했고 무엇이 남았는지 봅니다."
    >분석</button>
  `;
}

function renderZipInternalHashes(payload) {
  const freshPayload = payload.items !== state.titleRows;
  state.titleRows = payload.items || [];
  state.titleRowsMode = "zip-internal-hashes";
  els.selectDuplicateCandidates.textContent = "격리 후보 선택";
  els.resultTable.className = "title-table comprehensive-table zip-comprehensive-table";
  els.tableTitle.textContent = "ZIP 종합 중복 정리";
  const stats = payload.stats || {};
  const cacheText = stats.zipCacheEnabled === false
    ? " · 캐시 사용 불가"
    : ` · 캐시 재사용 ${stats.zipCacheHits || 0}개 · 새로 검사 ${stats.zipCacheMisses || 0}개`;
  els.tableMeta.textContent = `전체 ZIP ${stats.scannedZipArchives || stats.zipArchives || 0}개 · ZIP 비교 묶음 ${payload.groups || 0}개 · 비교 대상 ZIP ${stats.zipArchives || 0}개 · 내부 동일/전체 포함 ${stats.zipAllMatched || 0}개 · 격리 가능 ${stats.zipSelectable || 0}개${cacheText}`;

  const zipGroupKey = (item) => item.sourceType === "zip archive"
    ? String(item.zipComparisonGroup || item.zipDuplicateGroup || item.group)
    : `HASH${item.group}`;
  const zipConfidence = (item) => item.sourceType === "zip archive"
    ? (["all-matched", "subset", "superset", "keep"].includes(item.zipStatus) ? "확실" : "검토")
    : "확실";
  const allRows = [...state.titleRows].sort((left, right) => {
    const groupCompare = zipGroupKey(left).localeCompare(zipGroupKey(right), "ko", { numeric: true });
    if (groupCompare) return groupCompare;
    const decisionRank = (item) => {
      if (["keep", "superset"].includes(item.zipStatus)) return 0;
      if (item.zipStatus === "partial") return 1;
      if (["subset", "all-matched"].includes(item.zipStatus)) return 2;
      return 3;
    };
    return decisionRank(left) - decisionRank(right)
      || String(left.name || "").localeCompare(String(right.name || ""), "ko", { numeric: true });
  });
  const selectableGroups = new Set(
    allRows.filter((item) => item.autoSelect).map((item) => zipGroupKey(item)),
  );
  const visibleRows = state.confidentOnly
    ? allRows.filter((item) => selectableGroups.has(zipGroupKey(item)))
    : allRows;
  const countByGroup = new Map();
  allRows.forEach((item) => {
    const group = zipGroupKey(item);
    countByGroup.set(group, (countByGroup.get(group) || 0) + 1);
  });
  if (freshPayload) {
    state.collapsedComprehensiveGroups = new Set(
      [...countByGroup.entries()].filter(([, count]) => count > 1).map(([group]) => group),
    );
  }
  showTitleActions();
  els.resultHead.innerHTML = `
    <tr>
      <th>선택 · 그룹</th>
      <th>원래 이름</th>
      <th>크기</th>
      <th>수정일</th>
      <th>판정</th>
      <th>처리</th>
      <th>일치 근거</th>
      <th>뽑은 제목</th>
      <th>확장자</th>
      <th>해시 확인</th>
      <th>분석</th>
      <th>위치</th>
      <th>묶음</th>
    </tr>
  `;
  els.resultBody.innerHTML = visibleRows
    .map((item, index) => {
      const groupKey = zipGroupKey(item);
      const previousGroup = index > 0 ? zipGroupKey(visibleRows[index - 1]) : "";
      const groupLead = index === 0 || previousGroup !== groupKey;
      const groupCount = countByGroup.get(groupKey) || 1;
      const collapsed = state.collapsedComprehensiveGroups.has(groupKey);
      const locationText = item.sourceType === "zip item" ? item.innerName : item.location;
      const isArchive = item.sourceType === "zip archive";
      const canMove = isArchive && item.autoSelect;
      const detail = isArchive
        ? [
            `내부 문서 ${item.memberCount || 0}개 중 ${item.matchedCount || 0}개가 같은 해시로 발견됨`,
            `압축 안 된 파일과 일치 ${item.externalMatchedCount || 0}개`,
            `다른 zip 내부 파일과 일치 ${item.matchedZipCount ?? Math.max(0, (item.matchedCount || 0) - (item.externalMatchedCount || 0))}개`,
            item.matchTargetDetail ? `\n일치 대상:\n${item.matchTargetDetail}` : "",
            item.errorCount ? `오류 ${item.errorCount}개` : "",
            item.comparisonTargetName ? `비교 ZIP: ${item.comparisonTargetName}` : "",
            item.onlyHereExamples?.length ? `이 ZIP에만 있음: ${item.onlyHereExamples.join(", ")}` : "",
            item.onlyTargetExamples?.length ? `비교 ZIP에만 있음: ${item.onlyTargetExamples.join(", ")}` : "",
            item.changedExamples?.length ? `같은 이름, 다른 내용: ${item.changedExamples.join(", ")}` : "",
          ].filter(Boolean).join("\n")
        : `${item.sourceType === "file" ? "압축 안 된 파일" : "zip 내부 파일"} · SHA-256: ${item.hash}`;
      const badgeClass = !isArchive
        ? ""
        : item.zipStatus === "all-matched"
          ? "keep"
        : item.zipStatus === "subset"
            ? "keep"
          : item.zipStatus === "superset"
            ? "keep"
          : item.zipStatus === "keep"
            ? "keep"
            : item.zipStatus === "partial"
              ? "warn"
              : item.zipStatus === "broken"
                ? "broken"
                : "dup";
      const statusText = isArchive ? item.zipStatusText || "-" : "해시 일치";
      const matchText = isArchive
        ? item.matchTargetText || `${item.matchedCount || 0}/${item.memberCount || 0} · 외부 ${item.externalMatchedCount || 0}`
        : item.sourceType === "zip item" ? "ZIP 내부 파일과 동일" : "압축 안 된 파일과 동일";
      const hashText = isArchive
        ? ["all-matched", "subset", "keep"].includes(item.zipStatus) ? "내부 전체 일치" : item.zipStatus === "superset" ? "보관 기준" : item.zipStatus === "partial" ? "일부 일치" : "미확인"
        : item.hashShort || "해시 일치";
      return `
        <tr class="result-row ${resultToneClass({ ...item, confidence: zipConfidence(item) }, index)} ${groupLead ? "group-lead" : "group-child"} ${!groupLead && collapsed ? "group-hidden" : ""}" data-row-group="${escapeHtml(groupKey)}">
          <td class="selection-group-cell">
            <div class="selection-group-inner">
              <input
                class="row-check title-quarantine-check"
                type="checkbox"
                data-location="${escapeHtml(item.location)}"
                data-duplicate="${item.autoSelect ? "true" : "false"}"
                ${canMove ? "" : "disabled"}
                title="${escapeHtml(canMove ? "ZIP 내부 문서가 모두 다른 위치의 동일 확장자 파일과 SHA-256까지 일치하여 ZIP 전체를 격리할 수 있습니다." : isArchive ? "내부 파일 전체 일치가 아니므로 격리할 수 없습니다." : "해시 근거를 보여주는 하위 행이며 직접 격리하지 않습니다.")}"
              />
              <span class="group-visual">
                <span class="group-line-dot" aria-hidden="true"></span>
                <span class="group-token ${groupColorClass(groupKey)}">${escapeHtml(groupKey.replace(/^HASH/, "H"))}</span>
              </span>
            </div>
          </td>
          <td class="primary-file-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</td>
          <td class="numeric-cell">${escapeHtml(item.sizeText || "-")}</td>
          <td class="date-cell">${escapeHtml(item.modified || "-")}</td>
          <td><span class="badge ${badgeClass}" title="${escapeHtml(detail)}">${escapeHtml(statusText)}</span></td>
          <td>${zipKeepChoiceControl(item)}</td>
          <td class="evidence-cell" title="${escapeHtml(detail)}">${escapeHtml(matchText)}</td>
          <td title="${escapeHtml(item.title)}">${escapeHtml(item.title || "-")}</td>
          <td class="ext">${escapeHtml(item.extension || "-")}</td>
          <td><span class="hash-pill ${hashText === "미확인" ? "muted" : "ok"}" title="${escapeHtml(item.hash ? `SHA-256: ${item.hash}` : detail)}">${escapeHtml(hashText)}</span></td>
          <td>${zipAnalysisControl(item)}</td>
          <td class="path" title="${escapeHtml(item.location)}">${escapeHtml(locationText)}</td>
          <td class="group-count-cell">
            ${groupLead && groupCount > 1 ? `<button class="group-count-toggle ${collapsed ? "collapsed" : ""}" type="button" data-toggle-comprehensive-group="${escapeHtml(groupKey)}" aria-expanded="${collapsed ? "false" : "true"}">+${groupCount - 1}<span aria-hidden="true">⌄</span></button>` : ""}
          </td>
        </tr>
      `;
    })
    .join("");
  if (visibleRows.length === 0) {
    const scannedZipCount = stats.scannedZipArchives || stats.zipArchives || 0;
    const emptyTitle = scannedZipCount === 0
      ? "확인할 ZIP 파일을 찾지 못했습니다."
      : "서로 비교할 ZIP 조합을 찾지 못했습니다.";
    const emptyDetail = scannedZipCount === 0
      ? "ZIP이 하위 폴더에 있다면 ‘하위 폴더’를 켠 뒤 다시 스캔하세요."
      : `${scannedZipCount}개 ZIP을 확인했지만 ZIP끼리 겹치는 내부 파일이 없습니다.`;
    els.resultBody.innerHTML = `
      <tr class="empty-result-row">
        <td colspan="13">
          <strong>${escapeHtml(emptyTitle)}</strong>
          <span>${escapeHtml(emptyDetail)}</span>
        </td>
      </tr>
    `;
  }
  syncConfidentOnlyButton();
}

function renderRename(payload) {
  showRenameActions();
  els.resultTable.className = "";
  els.tableTitle.textContent = "이름 변경 미리보기";
  els.tableMeta.textContent = `바뀔 항목 ${payload.shown ?? 0}개 표시 · 적용 가능 ${payload.ready ?? 0}개 · 변경 없음 ${payload.unchanged ?? 0}개 숨김`;
  els.resultHead.innerHTML = `
    <tr>
      <th>현재 이름</th>
      <th>새 이름</th>
      <th>확장자</th>
      <th>상태</th>
      <th>폴더</th>
    </tr>
  `;
  const items = payload.items || [];
  if (!items.length) {
    els.resultBody.innerHTML = `<tr><td colspan="5" class="path">바꿀 이름이 없습니다.</td></tr>`;
    return;
  }
  const statusLabel = (status) => {
    if (status === "ready") return "적용 가능";
    if (status === "target exists") return "같은 이름 있음";
    if (status === "target duplicated") return "대상 중복";
    return status;
  };
  els.resultBody.innerHTML = items
    .map(
      (item) => `
        <tr>
          <td title="${escapeHtml(item.old)}">${escapeHtml(item.old)}</td>
          <td title="${escapeHtml(item.new)}">${escapeHtml(item.new)}</td>
          <td class="ext">${escapeHtml(item.extension || "-")}</td>
          <td><span class="badge ${item.status === "ready" ? "keep" : "dup"}">${escapeHtml(statusLabel(item.status))}</span></td>
          <td class="path" title="${escapeHtml(item.location)}">${escapeHtml(item.location)}</td>
        </tr>
      `,
    )
    .join("");
}

function renderTextDuplicates(payload) {
  hideResultActions();
  els.resultTable.className = "";
  els.tableTitle.textContent = "문장 중복";
  els.tableMeta.textContent = `본문 동일 ${payload.groups ?? 0}그룹, 전체 ${payload.total ?? 0}개 중 ${payload.shown ?? 0}개 표시`;
  els.resultHead.innerHTML = `
    <tr>
      <th>그룹</th>
      <th>남길 후보</th>
      <th>뽑은 제목</th>
      <th>원래 이름</th>
      <th>확장자</th>
      <th>문장 수</th>
      <th>크기</th>
      <th>본문 프리뷰</th>
      <th>위치</th>
    </tr>
  `;
  els.resultBody.innerHTML = (payload.items || [])
    .map(
      (item) => `
        <tr>
          <td>${escapeHtml(item.group)}</td>
          <td><span class="badge ${item.keep ? "keep" : "dup"}">${escapeHtml(item.keepText)}</span></td>
          <td title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</td>
          <td title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</td>
          <td class="ext">${escapeHtml(item.extension || "-")}</td>
          <td>${escapeHtml(item.sentenceCount || "-")}</td>
          <td>${escapeHtml(item.sizeText)}</td>
          <td class="path" title="${escapeHtml(item.preview)}">${escapeHtml(item.preview)}</td>
          <td class="path" title="${escapeHtml(item.location)}">${escapeHtml(item.location)}</td>
        </tr>
      `,
    )
    .join("");
}

function renderReferenceSentences(payload) {
  hideResultActions();
  els.resultTable.className = "";
  els.tableTitle.textContent = "참조 문장 확인";
  els.tableMeta.textContent = `참조 문장 ${payload.referenceSentences ?? 0}개 · 발견 파일 ${payload.total ?? 0}개 중 ${payload.shown ?? 0}개 표시`;
  els.resultHead.innerHTML = `
    <tr>
      <th>일치 문장</th>
      <th>일치율</th>
      <th>뽑은 제목</th>
      <th>원래 이름</th>
      <th>확장자</th>
      <th>참조 파일</th>
      <th>일치 문장 프리뷰</th>
      <th>위치</th>
    </tr>
  `;
  els.resultBody.innerHTML = (payload.items || [])
    .map(
      (item) => `
        <tr>
          <td><span class="badge keep">${escapeHtml(item.matchCount)} / ${escapeHtml(item.sentenceCount)}</span></td>
          <td>${escapeHtml(item.matchRatio)}%</td>
          <td title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</td>
          <td title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</td>
          <td class="ext">${escapeHtml(item.extension || "-")}</td>
          <td class="path" title="${escapeHtml(item.referenceFiles)}">${escapeHtml(item.referenceFiles)}</td>
          <td class="path" title="${escapeHtml(item.preview)}">${escapeHtml(item.preview)}</td>
          <td class="path" title="${escapeHtml(item.location)}">${escapeHtml(item.location)}</td>
        </tr>
      `,
    )
    .join("");
}

function collectRenameOptions() {
  return {
    find: els.renameFind.value,
    replace: els.renameReplace.value,
    position: els.renamePosition.value,
    regex: els.renameRegex.checked,
    prefix: els.renamePrefix.value,
    suffix: els.renameSuffix.value,
    caseMode: els.renameCase.value,
    startNumber: Number.parseInt(els.renameStart.value, 10) || -1,
    padding: Number.parseInt(els.renamePadding.value, 10) || 0,
    author: els.renameAuthor.value,
    authorPattern: els.renameAuthorPattern.value,
    stripCopySuffix: els.renameStripCopy.checked,
    autoAuthor: els.renameAutoAuthor.checked,
    normalizeTitleFormat: els.renameNormalizeTitle.checked,
  };
}

async function runScan() {
  const mode = els.mode.value;
  if (!state.folder) {
    setStatus("폴더를 선택하세요", "error");
    return;
  }
  if (mode === "reference-sentences" && !state.referenceZip) {
    setStatus("참조 zip을 선택하세요", "error");
    return;
  }
  state.mode = mode;
  state.confidentOnly = false;
  syncConfidentOnlyButton();
  setActiveTab("results");
  startProgressTimer();
  els.scan.disabled = true;

  const payload = await window.fileTidier.scan({
    mode,
    folder: state.folder,
    query: els.query.value,
    limit: Number.parseInt(els.limit.value, 10) || 2000,
    minSizeKb: Number.parseFloat(els.minSize.value) || 0,
    recursive: els.recursive.checked,
    includeZip: mode === "zip-internal-hashes" ? true : els.includeZip.checked,
    allowedExtensions: els.extensions.value,
    referenceZip: state.referenceZip,
    rename: collectRenameOptions(),
  });

  stopProgressTimer();
  els.scan.disabled = false;
  if (!payload.ok) {
    const errorText = payload.error || "실패했습니다";
    setStatus(errorText, payload.cancelled ? "" : "error");
    const progressContent = els.resultBody.querySelector(".scan-progress-content");
    if (progressContent) {
      progressContent.querySelector("strong").textContent = payload.cancelled ? "스캔 중지됨" : "스캔 실패";
      progressContent.querySelector("span").textContent = errorText;
      const bar = progressContent.querySelector("i");
      bar.classList.remove("indeterminate");
      bar.style.width = "0";
    }
    return;
  }
  if (mode === "titles") {
    renderTitles(payload);
  } else if (mode === "duplicates-comprehensive") {
    renderComprehensiveDuplicates(payload);
  } else if (mode === "zip-internal-hashes") {
    renderZipInternalHashes(payload);
  } else if (mode === "text-duplicates") {
    renderTextDuplicates(payload);
  } else if (mode === "reference-sentences") {
    renderReferenceSentences(payload);
  } else if (mode === "duplicates-size") {
    renderDuplicates(payload, false);
  } else if (mode === "duplicates-content") {
    renderDuplicates(payload, true);
  } else if (mode === "rename-preview") {
    renderRename(payload);
  } else {
    renderCatalog(payload);
  }
  renderSkipped(payload.skipped);
  setStatus(`완료 · 스킵 ${payload.skipped?.length || 0}개`);
}

async function quarantineSelectedTitleDuplicates() {
  if (!state.folder) {
    setStatus("폴더를 선택하세요", "error");
    return;
  }
  const selectedPaths = [...document.querySelectorAll(".title-quarantine-check:checked")]
    .map((input) => input.dataset.location)
    .filter(Boolean);
  if (selectedPaths.length === 0) {
    setStatus("격리할 파일을 선택하세요", "error");
    return;
  }
  const modeNote =
    state.titleRowsMode === "zip-internal-hashes"
      ? "ZIP 내부 문서가 전부 다른 위치와 SHA-256 해시 일치로 확인된 ZIP 파일만 선택됩니다. 내부 항목을 직접 삭제하지 않고 ZIP 파일 전체를 격리 폴더로 이동합니다."
      : state.titleRowsMode === "comprehensive"
      ? "종합 중복 정리에서는 같은 확장자 + SHA-256 해시 완전 일치 파일만 선택할 수 있습니다."
      : "zip 내부 항목은 이동하지 않습니다.";
  const confirmed = window.confirm(
    `${selectedPaths.length}개 파일을 선택한 폴더 안의 _FileTidier_Quarantine 폴더로 이동할까요?\n\n${modeNote}`,
  );
  if (!confirmed) {
    return;
  }
  els.quarantineSelected.disabled = true;
  setStatus("격리 이동 중...", "loading");
  const payload = await window.fileTidier.quarantineFiles({
    folder: state.folder,
    paths: selectedPaths,
  });
  els.quarantineSelected.disabled = false;
  if (!payload.ok) {
    setStatus(payload.error || "격리 이동 실패", "error");
    return;
  }
  setStatus(`격리 완료 · 이동 ${payload.movedCount || 0}개 · 제외 ${payload.skippedCount || 0}개`);
  removeQuarantinedTitleRows(payload.moved);
}

async function applyRenamePreview() {
  if (!state.folder) {
    setStatus("폴더를 선택하세요.", "error");
    return;
  }
  if (state.mode !== "rename-preview") {
    setStatus("이름 변경 미리보기 모드에서 먼저 스캔하세요.", "error");
    return;
  }
  const confirmed = window.confirm(
    "미리보기에서 ready 상태인 파일명을 실제로 변경할까요?\n\n작업 전 결과 표를 한 번 더 확인하세요.",
  );
  if (!confirmed) {
    return;
  }
  els.applyRename.disabled = true;
  setStatus("이름 변경 적용 중...", "loading");
  const payload = await window.fileTidier.applyRename({
    mode: "rename-preview",
    folder: state.folder,
    query: els.query.value,
    limit: Number.parseInt(els.limit.value, 10) || 2000,
    minSizeKb: Number.parseFloat(els.minSize.value) || 0,
    allowedExtensions: els.extensions.value,
    recursive: els.recursive.checked,
    includeZip: els.includeZip.checked,
    rename: collectRenameOptions(),
  });
  els.applyRename.disabled = false;
  if (!payload.ok) {
    setStatus(payload.error || "이름 변경 중 일부 실패", "error");
    return;
  }
  setStatus(`이름 변경 완료 · ${payload.applied || 0}개`);
  runScan();
}

function selectDuplicateCandidates() {
  document.querySelectorAll(".title-quarantine-check").forEach((input) => {
    input.checked = !input.disabled && input.dataset.duplicate === "true";
  });
  const count = document.querySelectorAll(".title-quarantine-check:checked").length;
  setStatus(
    state.titleRowsMode === "zip-internal-hashes"
      ? `격리 가능한 중복 ZIP ${count}개 선택됨`
      : state.titleRowsMode === "comprehensive"
        ? `해시 일치 후보 ${count}개 선택됨`
        : `중복 후보 ${count}개 선택됨`,
  );
}

function clearTitleSelection() {
  document.querySelectorAll(".title-quarantine-check").forEach((input) => {
    input.checked = false;
  });
  setStatus("선택 해제됨");
}

function formatAnalysisReport(payload) {
  const details = Array.isArray(payload.details) ? payload.details : [];
  return [payload.summary || "\ubd84\uc11d \uacb0\uacfc\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.", "", ...details].join("\n");
}

function renderAnalysisList(items, emptyText = "\ucc28\uc774 \uc5c6\uc74c") {
  const list = Array.isArray(items) ? items.filter(Boolean) : [];
  if (list.length === 0) {
    return `<p class="analysis-empty">${escapeHtml(emptyText)}</p>`;
  }
  return `<div class="analysis-list">${list.map((item) => `<div class="analysis-row">${escapeHtml(item)}</div>`).join("")}</div>`;
}

function renderAnalysisActions(actions) {
  const list = Array.isArray(actions) ? actions : [];
  if (list.length === 0) return "";
  return `
    <div class="analysis-action-list">
      ${list.map((action) => `
        <button
          class="analysis-compare-action"
          type="button"
          data-compare-title="true"
          data-left="${escapeHtml(action.left)}"
          data-right="${escapeHtml(action.right)}"
          title="두 ZIP 내부 파일의 본문과 EPUB 구성을 자세히 비교합니다."
        >
          <span><b>A</b>${escapeHtml(action.leftName)}</span>
          <span><b>B</b>${escapeHtml(action.rightName)}</span>
          <strong>본문 비교</strong>
        </button>
      `).join("")}
    </div>
  `;
}

function analysisSectionClass(title) {
  const text = String(title || "");
  if (text.includes("EPUB \ub0b4\ubd80 \uc694\uc57d")) return "wide summary-card";
  if (text.includes("\ud6c4\ubcf4\uc5d0\ub9cc") || text.includes("\uae30\uc900\uc5d0\ub9cc")) return "diff-card";
  if (text.includes("\ub3d9\uc77c \ub0b4\ubd80") || text.includes("\ub2e4\ub978 \ub0b4\ubd80") || text.includes("\ud55c\ucabd\uc5d0\ub9cc")) {
    return "epub-card";
  }
  return "";
}

function analysisMetricClass(label) {
  const text = String(label || "");
  if (text === "SHA256" || text.includes("bytes") || text.includes("\ub0b4\ubd80 \uc218\uc815\uc77c")) return "wide";
  if (text.includes("\ucc28\uc774") || text.includes("\ubcc0\uacbd")) return "attention";
  return "";
}

function findAnalysisSection(sections, titlePart) {
  return sections.find((section) => String(section.title || "").includes(titlePart));
}

function analysisMetricValue(metrics, label) {
  const metric = (Array.isArray(metrics) ? metrics : []).find((item) => String(item.label || "") === label);
  return metric ? String(metric.value ?? "") : "";
}

function highlightChangedWords(text, otherText) {
  const value = String(text || "");
  const otherWords = new Set(String(otherText || "").match(/[0-9A-Za-z가-힣]+/g) || []);
  return (value.match(/[0-9A-Za-z가-힣]+|[^0-9A-Za-z가-힣]+/g) || [value])
    .map((token) => {
      const escaped = escapeHtml(token);
      return /^[0-9A-Za-z가-힣]+$/.test(token) && !otherWords.has(token)
        ? `<mark class="analysis-word-change">${escaped}</mark>`
        : escaped;
    })
    .join("");
}

function renderCompareCells(leftItems, rightItems, limit = 4) {
  const left = Array.isArray(leftItems) ? leftItems.filter(Boolean) : [];
  const right = Array.isArray(rightItems) ? rightItems.filter(Boolean) : [];
  const count = Math.max(Math.min(Math.max(left.length, right.length), limit), 1);
  return Array.from({ length: count }, (_unused, index) => {
    const leftText = left[index] || "";
    const rightText = right[index] || "";
    return `
      <div class="analysis-compare-row">
        <div class="analysis-compare-cell ${leftText ? "" : "muted"}">${leftText ? highlightChangedWords(leftText, rightText) : "—"}</div>
        <div class="analysis-compare-cell ${rightText ? "" : "muted"}">${rightText ? highlightChangedWords(rightText, leftText) : "—"}</div>
      </div>
    `;
  }).join("");
}

function renderMediaCell(item) {
  if (!item) {
    return `<div class="analysis-media-cell muted">표지 이미지 없음</div>`;
  }
  const image = item.src
    ? `<img src="${escapeHtml(item.src)}" alt="${escapeHtml(item.name || "표지 이미지")}" />`
    : `<div class="analysis-media-placeholder">이미지 없음</div>`;
  return `
    <div class="analysis-media-cell">
      ${image}
      <div>
        <strong>${escapeHtml(item.name || "표지 이미지")}</strong>
        <span>${escapeHtml(item.sizeText || "-")}</span>
      </div>
    </div>
  `;
}

function renderMediaColumn(items) {
  const mediaItems = Array.isArray(items) ? items.slice(0, 8) : [];
  if (!mediaItems.length) {
    return `<div class="analysis-media-column"><div class="analysis-media-cell muted">이미지 없음</div></div>`;
  }
  return `<div class="analysis-media-column">${mediaItems.map((item) => renderMediaCell(item)).join("")}</div>`;
}

function renderMediaCompare(media) {
  const leftItems = Array.isArray(media?.left) ? media.left : [];
  const rightItems = Array.isArray(media?.right) ? media.right : [];
  if (!leftItems.length && !rightItems.length) {
    return "";
  }
  return `
    <section class="analysis-compare-block analysis-media-block">
      <h4>이미지 <span>A ${leftItems.length} · B ${rightItems.length}</span></h4>
      <div class="analysis-media-row">
        ${renderMediaColumn(leftItems)}
        ${renderMediaColumn(rightItems)}
      </div>
    </section>
  `;
}

function renderRangeHighlightedText(text, ranges) {
  const value = String(text || "");
  const normalized = (Array.isArray(ranges) ? ranges : [])
    .map((range) => [Math.max(0, Number(range?.[0]) || 0), Math.min(value.length, Number(range?.[1]) || 0)])
    .filter(([start, end]) => end > start)
    .sort((a, b) => a[0] - b[0]);
  if (!normalized.length) {
    return escapeHtml(value || "-");
  }
  const merged = [];
  for (const range of normalized) {
    const previous = merged.at(-1);
    if (previous && range[0] <= previous[1]) {
      previous[1] = Math.max(previous[1], range[1]);
    } else {
      merged.push([...range]);
    }
  }
  let cursor = 0;
  const parts = [];
  for (const [start, end] of merged) {
    parts.push(escapeHtml(value.slice(cursor, start)));
    parts.push(`<mark class="analysis-word-change">${escapeHtml(value.slice(start, end))}</mark>`);
    cursor = end;
  }
  parts.push(escapeHtml(value.slice(cursor)));
  return parts.join("");
}

function renderSentencePairs(pairs) {
  const rows = Array.isArray(pairs) ? pairs : [];
  if (!rows.length) {
    return "";
  }
  return rows.map((pair) => {
    const similarity = Number(pair.similarity || 0);
    const similarityText = pair.left && pair.right ? `${Math.round(similarity)}% 유사` : "한쪽에만 있음";
    return `
      <div class="analysis-sentence-pair">
        <div class="analysis-compare-cell ${pair.left ? "" : "muted"}">
          ${renderRangeHighlightedText(pair.left, pair.leftRanges)}
        </div>
        <div class="analysis-compare-cell ${pair.right ? "" : "muted"}">
          ${renderRangeHighlightedText(pair.right, pair.rightRanges)}
        </div>
        <span class="analysis-sentence-score">${escapeHtml(similarityText)}</span>
      </div>
    `;
  }).join("");
}

function renderDetailedMediaCell(item) {
  if (!item) {
    return `<div class="analysis-media-cell muted"><span>해당 이미지 없음</span></div>`;
  }
  const image = item.src
    ? `<img src="${escapeHtml(item.src)}" alt="${escapeHtml(item.name || "EPUB 이미지")}" />`
    : `<div class="analysis-media-placeholder">미리보기 없음</div>`;
  const details = [
    item.sizeText || "-",
    item.dimensions || "크기 미확인",
    item.compressedSizeText ? `압축 ${item.compressedSizeText}` : "",
    item.sha256 ? `SHA-256 ${item.sha256.slice(0, 12)}` : "",
  ].filter(Boolean).join(" · ");
  return `
    <div class="analysis-media-cell" title="${escapeHtml(item.path || item.name || "")}">
      ${image}
      <div>
        <strong>${escapeHtml(item.name || item.path || "EPUB 이미지")}</strong>
        ${item.path && item.path !== item.name ? `<small>${escapeHtml(item.path)}</small>` : ""}
        <span>${escapeHtml(details)}</span>
      </div>
    </div>
  `;
}

function renderAlignedMediaCompare(media) {
  const leftItems = Array.isArray(media?.left) ? media.left : [];
  const rightItems = Array.isArray(media?.right) ? media.right : [];
  const rows = Array.isArray(media?.rows) && media.rows.length
    ? media.rows
    : Array.from({ length: Math.max(leftItems.length, rightItems.length) }, (_unused, index) => ({
        left: leftItems[index] || null,
        right: rightItems[index] || null,
        status: leftItems[index] && rightItems[index] ? "비교" : "한쪽에만 있음",
      }));
  if (!rows.length) {
    return "";
  }
  return `
    <section class="analysis-compare-block analysis-media-block">
      <h4>이미지 <span>A ${leftItems.length} · B ${rightItems.length}</span></h4>
      <div class="analysis-media-pairs">
        ${rows.map((row) => `
          <div class="analysis-media-pair">
            ${renderDetailedMediaCell(row.left)}
            ${renderDetailedMediaCell(row.right)}
            <span class="analysis-media-status">${escapeHtml(row.status || "비교")}</span>
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

function renderAnalysisCompare(payload, sections, metrics = []) {
  if (!payload.leftName || !payload.rightName) {
    return "";
  }
  const leftOnly = findAnalysisSection(sections, "\ud6c4\ubcf4\uc5d0\ub9cc");
  const rightOnly = findAnalysisSection(sections, "\uae30\uc900\uc5d0\ub9cc");
  const epubSummary = findAnalysisSection(sections, "EPUB \ub0b4\ubd80 \uc694\uc57d");
  const blocks = [];
  const leftSentenceItems = Array.isArray(leftOnly?.items) ? leftOnly.items : [];
  const rightSentenceItems = Array.isArray(rightOnly?.items) ? rightOnly.items : [];
  if (Array.isArray(payload.sentencePairs) && payload.sentencePairs.length) {
    const leftCount = payload.sentencePairs.filter((item) => item.left).length;
    const rightCount = payload.sentencePairs.filter((item) => item.right).length;
    blocks.push(`
      <section class="analysis-compare-block">
        <h4>문장 <span>A ${leftCount} · B ${rightCount}</span></h4>
        ${renderSentencePairs(payload.sentencePairs)}
      </section>
    `);
  } else if (leftSentenceItems.length || rightSentenceItems.length) {
    blocks.push(`
      <section class="analysis-compare-block">
        <h4>\ubb38\uc7a5 <span>A ${leftSentenceItems.length} · B ${rightSentenceItems.length}</span></h4>
        ${renderCompareCells(leftSentenceItems, rightSentenceItems, 12)}
      </section>
    `);
  }
  const mediaBlock = renderAlignedMediaCompare(payload.media);
  if (mediaBlock) {
    blocks.push(mediaBlock);
  }
  if (Array.isArray(epubSummary?.items) && epubSummary.items.length) {
    const leftSummary = epubSummary.items[0] || "";
    const rightSummary = epubSummary.items[1] || "";
    const extraSummary = epubSummary.items.slice(2, 5).filter(Boolean);
    blocks.push(`
      <section class="analysis-compare-block analysis-epub-block">
        <h4>EPUB 내부 <span>A · B</span></h4>
        ${renderCompareCells([leftSummary], [rightSummary], 1)}
        ${extraSummary.length ? `<div class="analysis-compare-note">${extraSummary.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}</div>` : ""}
      </section>
    `);
  }
  const leftSize = analysisMetricValue(metrics, "후보 크기")
    || analysisMetricValue(metrics, "후보 압축 크기")
    || analysisMetricValue(metrics, "파일 크기");
  const rightSize = analysisMetricValue(metrics, "기준 크기")
    || analysisMetricValue(metrics, "기준 압축 크기");
  return `
    <div class="analysis-compare-files">
      <div title="${escapeHtml(payload.leftPath || payload.leftName)}"><b>A</b><span>${escapeHtml(payload.leftName)}</span>${leftSize ? `<em>${escapeHtml(leftSize)}</em>` : ""}</div>
      <div title="${escapeHtml(payload.rightPath || payload.rightName)}"><b>B</b><span>${escapeHtml(payload.rightName)}</span>${rightSize ? `<em>${escapeHtml(rightSize)}</em>` : ""}</div>
    </div>
    ${blocks.join("")}
  `;
}

function isPromotedAnalysisSection(section) {
  const title = String(section.title || "");
  return title.includes("\ud6c4\ubcf4\uc5d0\ub9cc") || title.includes("\uae30\uc900\uc5d0\ub9cc") || title.includes("EPUB \ub0b4\ubd80 \uc694\uc57d");
}

function compactAnalysisMetrics(metrics) {
  const byLabel = new Map(metrics.map((metric) => [String(metric.label || ""), String(metric.value ?? "")]));
  if (!byLabel.has("후보 크기") || !byLabel.has("기준 크기")) {
    return metrics.map((metric) => ({ label: metric.label, value: metric.value, note: "" }));
  }
  const used = new Set();
  const result = [];
  const pushPair = (label, leftLabel, rightLabel) => {
    if (!byLabel.has(leftLabel) || !byLabel.has(rightLabel)) {
      return;
    }
    used.add(leftLabel);
    used.add(rightLabel);
    result.push({
      label,
      value: `${byLabel.get(leftLabel)} vs ${byLabel.get(rightLabel)}`,
      note: "",
    });
  };
  const pushExisting = (label, valueLabel, deltaLabel = "") => {
    if (!byLabel.has(valueLabel)) {
      return;
    }
    used.add(valueLabel);
    if (deltaLabel) {
      used.add(deltaLabel);
    }
    result.push({
      label,
      value: byLabel.get(valueLabel),
      note: deltaLabel ? byLabel.get(deltaLabel) || "" : "",
    });
  };
  pushPair("파일 크기", "후보 크기", "기준 크기");
  pushExisting("본문 길이", "본문 길이", "본문 길이 차이");
  pushExisting("문장 수", "문장 수", "문장 수 차이");
  for (const metric of metrics) {
    const label = String(metric.label || "");
    if (used.has(label)) {
      continue;
    }
    result.push({ label, value: metric.value, note: "" });
  }
  return result;
}

function canQuarantineAnalysisPath(value) {
  const path = String(value || "").trim();
  return Boolean(path) && !path.includes("::");
}

function renderAnalysisDecision(payload) {
  if (!els.analysisDecision) {
    return;
  }
  const choices = [
    { side: "left", label: "A", path: payload.leftPath, name: payload.leftName },
    { side: "right", label: "B", path: payload.rightPath, name: payload.rightName },
  ].filter((choice) => canQuarantineAnalysisPath(choice.path));
  if (!choices.length) {
    els.analysisDecision.innerHTML = "";
    els.analysisDecision.classList.add("hidden");
    return;
  }
  els.analysisDecision.innerHTML = `
    <div class="analysis-decision-copy">
      <strong>분석 후 격리</strong>
      <span>검토 판정 파일도 여기에서 직접 선택할 수 있습니다. 영구 삭제하지 않고 격리 폴더로 이동합니다.</span>
    </div>
    <div class="analysis-decision-options">
      ${choices.map((choice) => `
        <label class="analysis-decision-option" title="${escapeHtml(choice.path)}">
          <input type="radio" name="analysisQuarantineSide" value="${choice.side}" />
          <b>${choice.label}</b>
          <span>${escapeHtml(choice.name || choice.path)}</span>
        </label>
      `).join("")}
    </div>
    <button class="analysis-quarantine-button" type="button" data-analysis-quarantine="true" disabled>
      선택 파일 격리
    </button>
  `;
  els.analysisDecision.classList.remove("hidden");
}

async function quarantineAnalysisSelection() {
  const payload = state.analysisPayload;
  const selected = els.analysisDecision?.querySelector('input[name="analysisQuarantineSide"]:checked');
  if (!payload || !selected) {
    return;
  }
  const side = selected.value;
  const path = side === "left" ? payload.leftPath : payload.rightPath;
  const name = side === "left" ? payload.leftName : payload.rightName;
  if (!canQuarantineAnalysisPath(path)) {
    setStatus("ZIP 내부 항목은 개별 격리할 수 없습니다.", "error");
    return;
  }
  const confirmed = window.confirm(
    `${selected.value === "left" ? "A" : "B"} 파일을 격리하시겠습니까?\n\n${name || path}\n${path}\n\n검토 판정일 수 있으므로 분석 내용을 확인한 뒤 진행하세요.`,
  );
  if (!confirmed) {
    return;
  }
  const button = els.analysisDecision.querySelector("[data-analysis-quarantine]");
  if (button) {
    button.disabled = true;
    button.textContent = "격리 중";
  }
  setStatus("선택 파일을 격리 폴더로 이동 중...", "loading");
  try {
    const result = await window.fileTidier.quarantineFiles({ folder: state.folder, paths: [path] });
    if (!result?.ok || !result.movedCount) {
      setStatus(result?.error || "파일을 격리하지 못했습니다. 선택 폴더 안의 파일인지 확인하세요.", "error");
      return;
    }
    removeQuarantinedTitleRows(result.moved);
    setStatus(`격리 완료 · ${name || path}`);
    hideAnalysisModal();
  } finally {
    if (button?.isConnected) {
      button.disabled = false;
      button.textContent = "선택 파일 격리";
    }
  }
}

function showAnalysisModal(payload) {
  state.analysisPayload = payload;
  const rawMetrics = Array.isArray(payload.metrics) ? payload.metrics : [];
  const metrics = compactAnalysisMetrics(rawMetrics);
  const sections = Array.isArray(payload.sections) ? payload.sections : [];
  const compareHtml = renderAnalysisCompare(payload, sections, rawMetrics);
  const visibleSections = compareHtml ? sections.filter((section) => !isPromotedAnalysisSection(section)) : sections;
  if (els.analysisTitle) {
    els.analysisTitle.textContent = payload.leftName && payload.rightName ? "Comparison" : "분석 결과";
  }
  els.analysisSummary.textContent = payload.summary || "\ubd84\uc11d \uacb0\uacfc\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.";
  if (els.analysisCompare) {
    els.analysisCompare.innerHTML = compareHtml;
    els.analysisCompare.classList.toggle("hidden", !compareHtml);
  }
  els.analysisMetrics.innerHTML = metrics
    .map(
      (metric) => {
        const value = String(metric.value ?? "");
        const note = String(metric.note || "");
        const deltaValue = note || value;
        const deltaClass = deltaValue.startsWith("+") ? " delta-plus" : deltaValue.startsWith("-") ? " delta-minus" : "";
        const metricClass = analysisMetricClass(metric.label);
        return `
        <div class="analysis-metric${deltaClass}${metricClass ? ` ${metricClass}` : ""}">
          <div class="analysis-metric-head">
            <span>${escapeHtml(metric.label)}</span>
            ${note ? `<em>${escapeHtml(note)}</em>` : ""}
          </div>
          <strong>${escapeHtml(value)}</strong>
        </div>
      `;
      },
    )
    .join("");
  els.analysisSections.innerHTML = visibleSections
    .filter((section) => Array.isArray(section.items))
    .map(
      (section) => `
        <section class="analysis-section ${analysisSectionClass(section.title)}">
          <h4>${escapeHtml(section.title)}</h4>
          ${renderAnalysisList(section.items)}
          ${renderAnalysisActions(section.actions)}
        </section>
      `,
    )
    .join("");
  els.analysisRaw.textContent = formatAnalysisReport(payload);
  renderAnalysisDecision(payload);
  els.analysisModal.classList.remove("hidden");
  els.analysisModal.setAttribute("aria-hidden", "false");
}

function hideAnalysisModal() {
  state.analysisPayload = null;
  els.analysisModal.classList.add("hidden");
  els.analysisModal.setAttribute("aria-hidden", "true");
}

function showZipHashAnalysis(location) {
  const item = state.titleRows.find((row) => row.sourceType === "zip archive" && row.location === location);
  if (!item) {
    setStatus("분석할 zip 요약행을 찾지 못했습니다.", "error");
    return;
  }
  const matchedItems = String(item.matchTargetDetail || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && line !== "-");
  const unmatchedItems = Array.isArray(item.unmatchedExamples) ? item.unmatchedExamples : [];
  const onlyHereItems = Array.isArray(item.onlyHereExamples) ? item.onlyHereExamples : unmatchedItems;
  const onlyTargetItems = Array.isArray(item.onlyTargetExamples) ? item.onlyTargetExamples : [];
  const changedItems = Array.isArray(item.changedExamples) ? item.changedExamples : [];
  const contentComparePairs = Array.isArray(item.contentComparePairs) ? item.contentComparePairs : [];
  const hashes = String(item.hash || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  showAnalysisModal({
    summary:
      item.zipStatus === "partial"
        ? "두 ZIP이 일부만 겹칩니다. 한쪽에만 있거나 내용이 다른 파일이 있으므로 격리할 수 없습니다."
        : item.zipStatus === "subset"
          ? `이 ZIP의 내부 파일 전체가 ${item.comparisonTargetName || "다른 ZIP"}에 들어 있습니다. 비교 대상에 추가 파일이 있으므로 현재 ZIP만 격리할 수 있습니다.`
        : item.zipStatus === "superset"
          ? `이 ZIP은 ${item.comparisonTargetName || "다른 ZIP"}의 내부 파일 전체와 추가 파일을 함께 가지고 있어 보관 기준으로 추천됩니다.`
        : item.zipStatus === "all-matched"
          ? `두 ZIP의 내부 파일 구성이 SHA-256까지 완전히 같습니다. 남김 ZIP을 제외한 중복 ZIP만 격리할 수 있습니다.`
          : item.zipStatus === "keep"
            ? "같은 내부 구성 묶음에서 남길 ZIP으로 선택된 파일입니다."
            : "ZIP 종합 중복 정리 분석 결과입니다.",
    metrics: [
      { label: "판정", value: item.zipStatusText || "-" },
      { label: "내부 일치", value: `${item.matchedCount || 0}/${item.memberCount || 0}` },
      { label: "비교 ZIP", value: item.comparisonTargetName || "-" },
      { label: "이 ZIP에만", value: `${onlyHereItems.length}개` },
      { label: "비교 ZIP에만", value: `${onlyTargetItems.length}개` },
      { label: "내용 다름", value: `${changedItems.length}개` },
      { label: "일치 해시", value: hashes.length ? `${hashes.length}개` : "-" },
    ],
    sections: [
      ...(contentComparePairs.length
        ? [{ title: "ZIP 내부 파일 본문 비교", items: [], actions: contentComparePairs }]
        : []),
      { title: "일치한 항목", items: matchedItems.length ? matchedItems : ["일치한 항목 없음"] },
      { title: "이 ZIP에만 있는 파일", items: onlyHereItems.length ? onlyHereItems : ["없음"] },
      { title: "비교 ZIP에만 있는 파일", items: onlyTargetItems.length ? onlyTargetItems : ["없음"] },
      { title: "같은 이름이지만 내용이 다른 파일", items: changedItems.length ? changedItems : ["없음"] },
      { title: "해시", items: hashes.length ? hashes : ["-"] },
    ],
    details: [
      `zip: ${item.name}`,
      `위치: ${item.location}`,
      `판정: ${item.zipStatusText || "-"}`,
      `비교 ZIP: ${item.comparisonTargetName || "-"}`,
      `비교 ZIP 위치: ${item.comparisonTargetPath || "-"}`,
      `일치: ${item.matchedCount || 0}/${item.memberCount || 0}`,
      `압축 안 된 파일과 일치: ${item.externalMatchedCount || 0}`,
      `다른 zip 내부와 일치: ${item.matchedZipCount || 0}`,
      "",
      "일치한 항목:",
      ...(matchedItems.length ? matchedItems : ["-"]),
      "",
      "이 ZIP에만 있는 파일:",
      ...(onlyHereItems.length ? onlyHereItems : ["-"]),
      "",
      "비교 ZIP에만 있는 파일:",
      ...(onlyTargetItems.length ? onlyTargetItems : ["-"]),
      "",
      "같은 이름이지만 내용이 다른 파일:",
      ...(changedItems.length ? changedItems : ["-"]),
    ],
  });
}

async function compareTitleDifference(button) {
  const left = button.dataset.left;
  const right = button.dataset.right;
  if (!left || !right) {
    setStatus("\ube44\uad50\ud560 \ud30c\uc77c \uacbd\ub85c\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.", "error");
    return;
  }

  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "\ud655\uc778\uc911";
  setStatus("\ucc28\uc774 \uc6d0\uc778 \ubd84\uc11d \uc911... \uc624\ub798 \uac78\ub9ac\uba74 20\ucd08 \uc548\uc5d0 \uc790\ub3d9 \uc911\ub2e8\ub429\ub2c8\ub2e4.", "loading");

  try {
    const payload = await window.fileTidier.compareItems({ left, right });
    if (!payload.ok) {
      setStatus(payload.error || "\ubd84\uc11d \uc2e4\ud328", "error");
      showAnalysisModal({
        summary: payload.error || "\ubd84\uc11d \uc2e4\ud328",
        details: [payload.error || "\uc54c \uc218 \uc5c6\ub294 \uc624\ub958"],
        metrics: [],
        sections: [],
      });
      return;
    }
    setStatus("\ubd84\uc11d \uc644\ub8cc");
    showAnalysisModal(payload);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

els.chooseFolder.addEventListener("click", async () => {
  const folder = await window.fileTidier.chooseFolder();
  if (!folder) {
    return;
  }
  state.folder = folder;
  els.folderText.textContent = folder;
  els.folderText.title = folder;
  setStatus("폴더 선택됨");
});

els.chooseReferenceZip.addEventListener("click", async () => {
  const zipPath = await window.fileTidier.chooseReferenceZip();
  if (!zipPath) {
    return;
  }
  setReferenceZip(zipPath);
});

document.querySelectorAll("[data-reference]").forEach((button) => {
  button.addEventListener("click", () => {
    setReferenceZip(button.dataset.reference);
  });
});

els.openManager?.addEventListener("click", async () => {
  await window.fileTidier.openManager();
});

if (window.fileTidier.onScanProgress) {
  window.fileTidier.onScanProgress((progress) => {
    updateProgressStatus(progress);
  });
}

els.scan.addEventListener("click", runScan);
els.analysisClose.addEventListener("click", hideAnalysisModal);
els.analysisModal.addEventListener("click", (event) => {
  if (event.target.dataset.closeAnalysis) {
    hideAnalysisModal();
  }
});
els.analysisDecision?.addEventListener("change", () => {
  const button = els.analysisDecision.querySelector("[data-analysis-quarantine]");
  if (button) {
    button.disabled = !els.analysisDecision.querySelector('input[name="analysisQuarantineSide"]:checked');
  }
});
els.analysisDecision?.addEventListener("click", (event) => {
  if (event.target.closest("[data-analysis-quarantine]")) {
    quarantineAnalysisSelection();
  }
});
els.analysisSections.addEventListener("click", (event) => {
  const compareButton = event.target.closest("[data-compare-title]");
  if (compareButton) {
    compareTitleDifference(compareButton);
  }
});

function toggleComprehensiveGroup(group) {
  const collapse = !state.collapsedComprehensiveGroups.has(group);
  if (collapse) {
    state.collapsedComprehensiveGroups.add(group);
  } else {
    state.collapsedComprehensiveGroups.delete(group);
  }
  els.resultBody.querySelectorAll("[data-toggle-comprehensive-group]").forEach((button) => {
    if (button.dataset.toggleComprehensiveGroup === group) {
      button.classList.toggle("collapsed", collapse);
      button.setAttribute("aria-expanded", collapse ? "false" : "true");
    }
  });
  els.resultBody.querySelectorAll("tr[data-row-group]").forEach((row) => {
    if (row.dataset.rowGroup === group && row.classList.contains("group-child")) {
      row.classList.toggle("group-hidden", collapse);
    }
  });
}

els.resultBody.addEventListener("click", (event) => {
  const groupToggle = event.target.closest("[data-toggle-comprehensive-group]");
  if (groupToggle) {
    const group = String(groupToggle.dataset.toggleComprehensiveGroup || "");
    toggleComprehensiveGroup(group);
    return;
  }
  const keepButton = event.target.closest("[data-set-keep]");
  if (keepButton) {
    setComprehensiveKeep(keepButton.dataset.group, keepButton.dataset.location);
    return;
  }
  const zipKeepButton = event.target.closest("[data-set-zip-keep]");
  if (zipKeepButton) {
    setZipArchiveKeep(zipKeepButton.dataset.group, zipKeepButton.dataset.location);
    return;
  }
  const zipAnalysisButton = event.target.closest("[data-zip-analysis]");
  if (zipAnalysisButton) {
    showZipHashAnalysis(zipAnalysisButton.dataset.location);
    return;
  }
  const button = event.target.closest("[data-compare-title]");
  if (button) {
    compareTitleDifference(button);
    return;
  }
  const row = event.target.closest("tr[data-row-group]");
  if (
    ["comprehensive", "zip-internal-hashes"].includes(state.titleRowsMode) &&
    row &&
    !event.target.closest("input, button, a, select, label")
  ) {
    toggleComprehensiveGroup(String(row.dataset.rowGroup || ""));
  }
});
els.confidentOnly?.addEventListener("click", toggleConfidentOnly);
els.selectDuplicateCandidates.addEventListener("click", selectDuplicateCandidates);
els.clearTitleSelection.addEventListener("click", clearTitleSelection);
els.quarantineSelected.addEventListener("click", quarantineSelectedTitleDuplicates);
els.applyRename.addEventListener("click", applyRenamePreview);
els.cancel.addEventListener("click", async () => {
  await window.fileTidier.cancelScan();
  setStatus("중지 요청됨");
});
els.previewInput.addEventListener("input", renderPreview);
els.mode.addEventListener("change", () => {
  if (els.mode.value === "zip-internal-hashes") {
    els.includeZip.checked = true;
  }
  els.tableTitle.textContent = modeDisplayTitle(els.mode.value);
  syncModeControls();
});
document.querySelectorAll("[data-mode-value]").forEach((button) => {
  button.addEventListener("click", () => {
    els.mode.value = button.dataset.modeValue;
    els.mode.dispatchEvent(new Event("change"));
  });
});
document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => setActiveTab(button.dataset.tab));
});

renderPreview();
renderSkipped([]);
syncModeControls();
