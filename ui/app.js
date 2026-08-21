const duplicateRows = [
  {
    title: "aaa",
    type: "제목 중복",
    size: "1.8 MB",
    path: "D:\\Books\\정리전\\aaa.epub",
  },
  {
    title: "aaa",
    type: "zip 내부",
    size: "4 KB",
    path: "D:\\Books\\정리전\\aaa.zip :: [ㅠㅠ]aaa.txt",
  },
  {
    title: "aaa",
    type: "압축 파일",
    size: "2.1 MB",
    path: "D:\\Books\\정리전\\aaa.zip",
  },
  {
    title: "sample book",
    type: "내용 중복",
    size: "6.4 MB",
    path: "D:\\Books\\정리전\\sample book.pdf",
  },
  {
    title: "sample book",
    type: "내용 중복",
    size: "6.4 MB",
    path: "D:\\Books\\backup\\sample book.pdf",
  },
];

const renameFiles = [
  "(아이)aaa.zip",
  "(아이)bbb.zip",
  "(아이)ccc.zip",
  "ddd(아이).zip",
  "mid(아이)dle.zip",
];

let selectedPosition = "front";

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function splitName(filename) {
  const dotIndex = filename.lastIndexOf(".");
  if (dotIndex <= 0) {
    return [filename, ""];
  }
  return [filename.slice(0, dotIndex), filename.slice(dotIndex)];
}

function replaceByPosition(text, findText, replaceText, position) {
  if (!findText) {
    return text;
  }
  if (position === "front") {
    return text.startsWith(findText) ? `${replaceText}${text.slice(findText.length)}` : text;
  }
  if (position === "back") {
    return text.endsWith(findText) ? `${text.slice(0, -findText.length)}${replaceText}` : text;
  }
  if (position === "exact") {
    return text === findText ? replaceText : text;
  }
  return text.replaceAll(findText, replaceText);
}

function renderDuplicates() {
  const table = document.querySelector("#duplicateTable");
  const rows = duplicateRows
    .map(
      (row) => `
        <div class="table-row">
          <div class="cell"><span class="badge warn">${escapeHtml(row.type)}</span></div>
          <div class="cell title-cell">${escapeHtml(row.title)}</div>
          <div class="cell">${escapeHtml(row.size)}</div>
          <div class="cell path-cell" title="${escapeHtml(row.path)}">${escapeHtml(row.path)}</div>
        </div>
      `,
    )
    .join("");

  table.innerHTML = `
    <div class="table-header">
      <div class="cell">종류</div>
      <div class="cell">기준 제목</div>
      <div class="cell">크기</div>
      <div class="cell">위치</div>
    </div>
    ${rows}
  `;
}

function renderRenamePreview() {
  const findText = document.querySelector("#findText").value;
  const replaceText = document.querySelector("#replaceText").value;
  const table = document.querySelector("#renameTable");

  const rows = renameFiles
    .map((filename) => {
      const [stem, extension] = splitName(filename);
      const newStem = replaceByPosition(stem, findText, replaceText, selectedPosition);
      const newName = `${newStem}${extension}`;
      const changed = newName !== filename;
      return `
        <div class="table-row">
          <div class="cell" title="${escapeHtml(filename)}">${escapeHtml(filename)}</div>
          <div class="cell" title="${escapeHtml(newName)}">${escapeHtml(newName)}</div>
          <div class="cell"><span class="badge ${changed ? "good" : "muted"}">${changed ? "ready" : "skip"}</span></div>
        </div>
      `;
    })
    .join("");

  table.innerHTML = `
    <div class="table-header">
      <div class="cell">현재 이름</div>
      <div class="cell">새 이름</div>
      <div class="cell">상태</div>
    </div>
    ${rows}
  `;

  const positionText = {
    front: "맨 앞",
    back: "맨 뒤",
    contains: "어디든 포함된",
    exact: "전체 이름이 같은",
  }[selectedPosition];
  document.querySelector(".preview-callout").innerHTML =
    `현재 예시는 <strong>${escapeHtml(findText || "(빈 값)")}</strong>가 ${positionText} 경우만 바뀝니다.`;
}

function bindControls() {
  document.querySelectorAll("#positionButtons button").forEach((button) => {
    button.addEventListener("click", () => {
      selectedPosition = button.dataset.position;
      document.querySelectorAll("#positionButtons button").forEach((item) => {
        item.classList.toggle("selected", item === button);
      });
      renderRenamePreview();
    });
  });

  document.querySelector("#findText").addEventListener("input", renderRenamePreview);
  document.querySelector("#replaceText").addEventListener("input", renderRenamePreview);
}

renderDuplicates();
renderRenamePreview();
bindControls();
