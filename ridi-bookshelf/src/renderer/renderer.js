const STATUS_ALL = "\uC804\uCCB4";
const STATUS_WISHLIST = "\uAD00\uC2EC";
const STATUS_READING = "\uC77D\uB294 \uC911";
const STATUS_DONE = "\uC644\uB3C5";
const STATUS_HOLD = "\uBCF4\uB958";
const STATUSES = [STATUS_ALL, STATUS_WISHLIST, STATUS_READING, STATUS_DONE];
const DEFAULT_PERIODS = [
  { id: "weekly", name: "\uC8FC\uAC04 \uBCA0\uC2A4\uD2B8" },
  { id: "monthly", name: "\uC6D4\uAC04 \uBCA0\uC2A4\uD2B8" },
  { id: "steady", name: "\uC2A4\uD14C\uB514\uC140\uB7EC" }
];
const DEFAULT_CATEGORIES = [
  { id: "4100", name: "BL \uC18C\uC124 e\uBD81 \u00B7 \uC804\uCCB4" },
  { id: "4101", name: "BL \uC18C\uC124 e\uBD81 \u00B7 \uD604\uB300\uBB3C" },
  { id: "4102", name: "BL \uC18C\uC124 e\uBD81 \u00B7 \uD310\uD0C0\uC9C0\uBB3C" },
  { id: "4103", name: "BL \uC18C\uC124 e\uBD81 \u00B7 \uC5ED\uC0AC/\uC2DC\uB300\uBB3C" },
  { id: "4104", name: "BL \uC18C\uC124 e\uBD81 \u00B7 \uD574\uC678 \uC18C\uC124" }
];
const EVENT_CATEGORY_ID = "event:96367:2532";

const state = {
  shelf: { categories: [], periods: DEFAULT_PERIODS, books: {}, watchedAuthors: [], lastSyncedAt: null, lastAddedIds: [], lastAddedCount: 0 },
  activeStatus: STATUS_ALL,
  activeCategoryId: "all",
  activePeriod: "weekly",
  favoritesOnly: false,
  query: "",
  currentBookId: null,
  progressStartedAt: null
};

const elements = {
  syncLabel: document.querySelector("#syncLabel"),
  scrapeButton: document.querySelector("#scrapeButton"),
  authorForm: document.querySelector("#authorForm"),
  authorInput: document.querySelector("#authorInput"),
  authorList: document.querySelector("#authorList"),
  authorScrapeButton: document.querySelector("#authorScrapeButton"),
  eventScrapeButton: document.querySelector("#eventScrapeButton"),
  categoryList: document.querySelector("#categoryList"),
  categoryFilter: document.querySelector("#categoryFilter"),
  periodFilter: document.querySelector("#periodFilter"),
  statusFilter: document.querySelector("#statusFilter"),
  searchInput: document.querySelector("#searchInput"),
  favoritesOnly: document.querySelector("#favoritesOnly"),
  exportButton: document.querySelector("#exportButton"),
  summary: document.querySelector("#summary"),
  message: document.querySelector("#message"),
  bookGrid: document.querySelector("#bookGrid"),
  dialog: document.querySelector("#bookDialog"),
  dialogCover: document.querySelector("#dialogCover"),
  dialogRank: document.querySelector("#dialogRank"),
  dialogCategory: document.querySelector("#dialogCategory"),
  dialogTitle: document.querySelector("#dialogTitle"),
  dialogAuthor: document.querySelector("#dialogAuthor"),
  dialogTags: document.querySelector("#dialogTags"),
  dialogReviews: document.querySelector("#dialogReviews"),
  dialogStatus: document.querySelector("#dialogStatus"),
  dialogNote: document.querySelector("#dialogNote"),
  dialogFavorite: document.querySelector("#dialogFavorite"),
  dialogOpen: document.querySelector("#dialogOpen"),
  dialogSave: document.querySelector("#dialogSave")
};

function optionalNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(String(value).replace(/,/g, ""));
  return Number.isFinite(number) ? number : null;
}

function normalizeCover(cover) {
  if (!cover) return "";
  const value = String(cover);
  const productId = value.match(/img\.ridicdn\.net\/cover\/([^/?#]+)/)?.[1];
  if (productId) return `https://img.ridicdn.net/cover/${productId}/xxlarge?dpi=xhdpi`;
  return value.replace(/\/(small|large|xlarge|xxlarge)(?:[?#].*)?$/, "/xxlarge?dpi=xhdpi");
}

function normalizeTags(tags) {
  return [...new Set((Array.isArray(tags) ? tags : [])
    .map((tag) => String(tag || "").replace(/^#/, "").trim())
    .filter((tag) => tag && !/(\uC778\uC2A4\uD0C0\uADF8\uB7A8|\uBB34\uB8CC\uC774\uC6A9\uAD8C|\uB2E4\uB978 \uD0A4\uC6CC\uB4DC|\uBC1C\uAE09 \uAC00\uB2A5|\uC0AC\uC6A9\uAE30\uD55C|\uB9AC\uBDF0|\uD3C9\uC810|\uBCC4\uC810|\uD560\uC778)/.test(tag)))]
    .slice(0, 12);
}

function normalizeReviews(reviews) {
  return (Array.isArray(reviews) ? reviews : [])
    .map((review) => ({
      text: String(review?.text || review?.content || "").replace(/\s+/g, " ").trim(),
      likes: optionalNumber(review?.likes || review?.likeCount || review?.recommendCount) || 0,
      rating: optionalNumber(review?.rating || review?.score),
      reviewer: String(review?.reviewer || review?.nickname || "").trim()
    }))
    .filter((review) => review.text)
    .slice(0, 3);
}

function extractVolume(title) {
  const match = String(title || "").trim().match(/(?:\(|\s)(\d+\s*\uAD8C)\)?$/);
  return match ? match[1].replace(/\s+/g, "") : "";
}

function normalizeSeriesTitle(title) {
  const clean = String(title || "").replace(/\s+/g, " ").trim();
  const normalized = clean
    .replace(/\s*\(?\d+\s*\uAD8C\)?$/u, "")
    .replace(/\s*\(?\uC81C\s*\d+\s*\uAD8C\)?$/u, "")
    .trim();
  return normalized || clean;
}

function displayGenre(book) {
  return book?.genreTag || categoryShortName({ name: book?.categoryName || "" });
}

function categoryShortName(category) {
  if (String(category?.id || "").startsWith("event:")) return String(category?.name || "\uC774\uBCA4\uD2B8 BL \uC18C\uC124");
  return String(category?.name || "").replace("BL \uC18C\uC124 e\uBD81 \u00B7 ", "");
}

function normalizeAuthors(authors) {
  return [...new Set((Array.isArray(authors) ? authors : [])
    .map((author) => String(author || "").trim())
    .filter(Boolean))];
}

function normalizeCategory(category, index) {
  const fallback = DEFAULT_CATEGORIES[index] || DEFAULT_CATEGORIES.find((item) => item.id === String(category?.id)) || DEFAULT_CATEGORIES[0];
  const id = String(category?.id || fallback.id);
  const fallbackById = DEFAULT_CATEGORIES.find((item) => item.id === id) || fallback;
  const name = category?.name || fallbackById.name;
  return {
    ...fallbackById,
    ...category,
    id,
    name: !name || name.includes("?") || name.startsWith("\uC7A5\uB974 ") ? fallbackById.name : name
  };
}

function ensureActiveFilters() {
  const categoryExists = state.activeCategoryId === "all" || state.shelf.categories.some((category) => category.id === state.activeCategoryId);
  if (!categoryExists) state.activeCategoryId = "all";

  const periodExists = state.shelf.periods.some((period) => period.id === state.activePeriod);
  if (!periodExists) state.activePeriod = state.shelf.periods[0]?.id || "weekly";
}

function periodNameForId(periodId) {
  if (periodId === "event") return "\uC774\uBCA4\uD2B8";
  return (state.shelf.periods || DEFAULT_PERIODS).find((period) => period.id === periodId)?.name || periodId;
}

function normalizeBook(book) {
  const originalTitle = book.originalTitle || book.title || "";
  const category = state.shelf.categories.find((item) => item.id === String(book.categoryId));
  return {
    ...book,
    title: normalizeSeriesTitle(book.title || originalTitle),
    originalTitle,
    volume: book.volume || extractVolume(originalTitle),
    cover: normalizeCover(book.cover),
    genreTag: book.genreTag || "",
    tags: normalizeTags(book.tags),
    topReviews: normalizeReviews(book.topReviews),
    rating: optionalNumber(book.rating),
    reviewCount: optionalNumber(book.reviewCount),
    categoryName: category?.name || book.categoryName || "",
    period: book.period || "monthly",
    periodName: periodNameForId(book.period || "monthly"),
    status: [...STATUSES, STATUS_HOLD].includes(book.status) ? book.status : STATUS_WISHLIST,
    note: book.note || "",
    favorite: Boolean(book.favorite)
  };
}

function normalizeShelf(shelf) {
  shelf = shelf || {};
  shelf.categories = Array.isArray(shelf.categories) && shelf.categories.length
    ? shelf.categories.map(normalizeCategory)
    : DEFAULT_CATEGORIES.map(normalizeCategory);
  shelf.periods = Array.isArray(shelf.periods) && shelf.periods.length ? shelf.periods : DEFAULT_PERIODS;
  shelf.watchedAuthors = normalizeAuthors(shelf.watchedAuthors);
  shelf.watchedAuthorsInitialized = Boolean(shelf.watchedAuthorsInitialized);
  shelf.activeAuthor = typeof shelf.activeAuthor === "string" ? shelf.activeAuthor : "";
  shelf.lastAddedIds = Array.isArray(shelf.lastAddedIds) ? shelf.lastAddedIds : [];
  shelf.lastAddedCount = Number(shelf.lastAddedCount || 0);
  shelf.books = Object.fromEntries(
    Object.entries(shelf.books || {}).map(([id, book]) => [id, normalizeBook(book)])
  );
  ensureActiveFilters();
  return shelf;
}

function booksArray() {
  return Object.values(state.shelf.books || {}).sort((a, b) => {
    if ((a.categoryId || "") === (b.categoryId || "")) return (a.rank || 0) - (b.rank || 0);
    return String(a.categoryId || "").localeCompare(String(b.categoryId || ""));
  });
}

function formatNumber(value) {
  return new Intl.NumberFormat("ko-KR").format(Number(value || 0));
}

function formatRating(book) {
  const rating = optionalNumber(book?.rating);
  if (rating === null) return "";
  const reviewCount = optionalNumber(book?.reviewCount);
  const review = reviewCount !== null ? ` \u00B7 \uB9AC\uBDF0 ${formatNumber(reviewCount)}` : "";
  return `\uD3C9\uC810 ${rating.toFixed(1)}${review}`;
}

function formatDate(value) {
  if (!value) return "\uC544\uC9C1 \uC218\uC9D1 \uC804";
  return new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function setMessage(text, type = "error") {
  elements.message.textContent = text;
  elements.message.classList.toggle("hidden", !text);
  elements.message.dataset.type = type;
}

function bindProgress() {
  if (!window.bookshelf.onProgress) return;
  window.bookshelf.onProgress((progress) => {
    if (!state.progressStartedAt) state.progressStartedAt = Date.now();
    const current = Number(progress?.current || 0);
    const total = Number(progress?.total || 0);
    const ratio = total ? ` ${current}/${total}` : "";
    const title = progress?.title ? ` \u00B7 ${progress.title}` : "";
    const elapsed = Math.max(1, Math.round((Date.now() - state.progressStartedAt) / 1000));
    setMessage(`${progress?.label || "\uC218\uC9D1 \uC911"}${ratio}${title} \u00B7 ${elapsed}\uCD08 \uACBD\uACFC`, "info");
  });
}

function selectedCategories() {
  return [...document.querySelectorAll("[data-category-toggle]")]
    .filter((input) => input.checked)
    .map((input) => state.shelf.categories.find((category) => category.id === input.value))
    .filter(Boolean);
}

function renderPeriods() {
  if (!elements.periodFilter) return;
  elements.periodFilter.innerHTML = (state.shelf.periods || DEFAULT_PERIODS)
    .map((period) => `<button data-period="${period.id}" class="${state.activePeriod === period.id ? "selected" : ""}">${escapeHtml(period.name)}</button>`)
    .join("");
}

function authorBookCount(author) {
  return booksArray().filter((book) => {
    const placements = Array.isArray(book.placements) ? book.placements : [];
    return String(book.author || "").includes(author) || placements.some((placement) => String(placement.categoryId || "") === `author:${author}`);
  }).length;
}

function renderAuthors() {
  if (!elements.authorList || !elements.authorScrapeButton) return;
  const authors = normalizeAuthors(state.shelf.watchedAuthors);
  state.shelf.watchedAuthors = authors;
  if (state.shelf.activeAuthor && !authors.includes(state.shelf.activeAuthor)) state.shelf.activeAuthor = "";
  elements.authorScrapeButton.disabled = !authors.length;
  elements.authorList.innerHTML = authors.length
    ? authors.map((author) => {
      const selected = state.shelf.activeAuthor === author ? " selected" : "";
      const count = authorBookCount(author);
      const countLabel = count ? `${count}\uAD8C` : "\uC544\uC9C1 \uC5C6\uC74C";
      return `
        <button class="author-folder${selected}" type="button" data-author-query="${escapeHtml(author)}">
          <span class="folder-icon">A</span>
          <span class="folder-copy">
            <strong>${escapeHtml(author)}</strong>
            <small>${escapeHtml(countLabel)}</small>
          </span>
          <b title="\uAC10\uC2DC \uD574\uC81C" data-author-remove="${escapeHtml(author)}">x</b>
        </button>
      `;
    }).join("")
    : `<p class="muted-hint">\uAC10\uC2DC\uD560 \uC791\uAC00\uB97C \uCD94\uAC00\uD574\uC8FC\uC138\uC694.</p>`;
}

function renderCategories() {
  if (!elements.categoryList || !elements.categoryFilter) return;
  const normalCategories = state.shelf.categories.filter((category) => !String(category.id || "").startsWith("event:"));
  const eventCategories = state.shelf.categories.filter((category) => String(category.id || "").startsWith("event:"));
  const eventViews = eventCategories.length ? eventCategories : [{ id: EVENT_CATEGORY_ID, name: "\uC774\uBCA4\uD2B8 \u00B7 BL \uC18C\uC124" }];
  elements.categoryList.innerHTML = normalCategories
    .map((category) => `
      <label class="category-row">
        <input type="checkbox" data-category-toggle value="${escapeHtml(category.id)}" checked>
        <span>
          <strong>${escapeHtml(categoryShortName(category))}</strong>
          <small>${escapeHtml(category.id)}</small>
        </span>
      </label>
    `)
    .join("");

  elements.categoryFilter.innerHTML = [
    `<button data-category-view="all" class="${state.activeCategoryId === "all" ? "selected" : ""}">${STATUS_ALL}</button>`,
    ...normalCategories.map((category) => {
      return `<button data-category-view="${escapeHtml(category.id)}" class="${state.activeCategoryId === category.id ? "selected" : ""}">${escapeHtml(categoryShortName(category))}</button>`;
    }),
    ...eventViews.map((category) => {
      return `<button data-category-view="${escapeHtml(category.id)}" class="${state.activeCategoryId === category.id ? "selected" : ""}">${escapeHtml(categoryShortName(category))}</button>`;
    })
  ].join("");
}

function filteredBooks() {
  const query = state.query.trim().toLowerCase();
  return booksArray().filter((book) => {
    const placements = Array.isArray(book.placements) ? book.placements : [];
    const activeAuthor = state.shelf.activeAuthor || "";
    const authorPlacementMatch = activeAuthor && placements.some((placement) => String(placement.categoryId || "") === `author:${activeAuthor}`);
    const activeCategoryPlacementMatch = placements.some((placement) => String(placement.categoryId || "") === state.activeCategoryId);
    const isEventView = String(state.activeCategoryId || "").startsWith("event:");
    const statusMatch = state.activeStatus === STATUS_ALL || book.status === state.activeStatus;
    const categoryMatch = activeAuthor || state.activeCategoryId === "all" || book.categoryId === state.activeCategoryId || activeCategoryPlacementMatch;
    const periodMatch = activeAuthor || isEventView || !book.period || book.period === state.activePeriod || placements.some((placement) => placement.period === state.activePeriod);
    const favoriteMatch = !state.favoritesOnly || book.favorite;
    const authorMatch = !activeAuthor || String(book.author || "").includes(activeAuthor) || authorPlacementMatch;
    const queryHaystack = [book.title, book.originalTitle, book.author, book.note, book.categoryName, ...(book.tags || []), ...(book.topReviews || []).map((review) => review.text)].join(" ").toLowerCase();
    return statusMatch && categoryMatch && periodMatch && favoriteMatch && authorMatch && (!query || queryHaystack.includes(query));
  });
}

function renderSummary(filtered) {
  const allBooks = booksArray();
  const reading = allBooks.filter((book) => book.status === STATUS_READING).length;
  const done = allBooks.filter((book) => book.status === STATUS_DONE).length;
  const added = state.shelf.lastAddedCount > 0 ? `<em>+${state.shelf.lastAddedCount}</em>` : "";
  const activeCategory = state.shelf.categories.find((category) => category.id === state.activeCategoryId);
  const periodLabel = activeCategory && String(activeCategory.id || "").startsWith("event:") ? categoryShortName(activeCategory) : periodNameForId(state.activePeriod);
  elements.summary.innerHTML = `
    <div class="dashboard-card">
      <div class="stat">
        <span>\uC804\uCCB4 \uC218\uC9D1</span>
        <strong>${allBooks.length}${added}</strong>
      </div>
      <div class="stat">
        <span>\uD604\uC7AC \uBCF4\uAE30</span>
        <strong>${filtered.length}</strong>
      </div>
      <div class="stat">
        <span>\uC218\uC9D1 \uAE30\uAC04</span>
        <strong class="blue-stat">${escapeHtml(periodLabel)}</strong>
      </div>
      <div class="stat">
        <span>\uC77D\uB294 \uC911 / \uC644\uB3C5</span>
        <strong>${reading} <small>/ ${done}</small></strong>
      </div>
    </div>
  `;
}

function renderBooks() {
  const books = filteredBooks();
  renderSummary(books);
  elements.syncLabel.textContent = formatDate(state.shelf.lastSyncedAt);
  if (!books.length) {
    elements.bookGrid.innerHTML = `
      <div class="empty-state">
        <strong>\uBCF4\uC5EC\uC904 \uCC45\uC774 \uC5C6\uC2B5\uB2C8\uB2E4.</strong>
        <p>\uAE30\uAC04\uC774\uB098 \uC7A5\uB974\uB97C \uBC14\uAFB8\uAC70\uB098 \uC218\uC9D1\uC744 \uC2E4\uD589\uD574\uBCF4\uC138\uC694.</p>
      </div>
    `;
    return;
  }
  elements.bookGrid.innerHTML = books.map(renderBookCard).join("");
}

function renderBookCard(book) {
  const isNew = state.shelf.lastAddedIds.includes(book.id);
  const rating = formatRating(book);
  const cover = book.cover
    ? `<img class="book-cover" src="${escapeHtml(book.cover)}" alt="${escapeHtml(book.title)}">`
    : `<div class="cover-placeholder">${escapeHtml(book.title)}</div>`;
  const tags = (book.tags || []).filter((tag) => tag !== displayGenre(book)).slice(0, 3);
  const tagMarkup = tags.length
    ? `<div class="tag-row card-tags">${tags.map((tag) => `<span class="tag-chip">${escapeHtml(tag)}</span>`).join("")}</div>`
    : "";
  return `
    <article class="book-card" data-book-id="${escapeHtml(book.id)}">
      <div class="cover-frame">
        ${cover}
        <span class="rank-pill">#${book.rank || ""}</span>
        ${isNew ? `<span class="new-badge">NEW</span>` : ""}
      </div>
      <div class="book-info">
        <p class="book-genre">${escapeHtml(displayGenre(book))}</p>
        <h3 class="book-title">${escapeHtml(book.title)}</h3>
        <p class="book-author">${escapeHtml(book.author || "\uC791\uAC00 \uC815\uBCF4 \uC5C6\uC74C")}</p>
        ${rating ? `<p class="book-rating">${escapeHtml(rating)}</p>` : ""}
        ${tagMarkup}
        <span class="status-chip">${escapeHtml(book.status || STATUS_WISHLIST)}</span>
      </div>
    </article>
  `;
}

function renderDialogBook(book, loading = false) {
  if (!book) return;
  elements.dialogCover.closest(".dialog-cover-wrap")?.querySelector(".dialog-cover-placeholder")?.remove();
  elements.dialogCover.src = book.cover || "";
  elements.dialogCover.alt = book.title;
  elements.dialogCover.style.display = book.cover ? "block" : "none";
  elements.dialogRank.textContent = `#${book.rank || ""}${book.volume ? ` \u00B7 ${book.volume}` : ""}`;
  elements.dialogCategory.textContent = [displayGenre(book), periodNameForId(book.period || state.activePeriod), formatRating(book)]
    .filter(Boolean)
    .join(" \u00B7 ");
  elements.dialogTitle.textContent = book.title;
  elements.dialogAuthor.textContent = book.author || "\uC791\uAC00 \uC815\uBCF4 \uC5C6\uC74C";
  const tags = (book.tags || []).slice(0, 10);
  if (elements.dialogTags) {
    elements.dialogTags.classList.remove("hidden");
    elements.dialogTags.innerHTML = tags.length
      ? tags.map((tag) => `<span class="tag-chip">${escapeHtml(tag)}</span>`).join("")
      : `<span class="tag-empty">${loading ? "\uD0DC\uADF8 \uBD88\uB7EC\uC624\uB294 \uC911" : "\uD0DC\uADF8 \uC5C6\uC74C"}</span>`;
  }
  const reviews = (book.topReviews || []).slice(0, 5);
  if (elements.dialogReviews) {
    elements.dialogReviews.classList.remove("hidden");
    elements.dialogReviews.innerHTML = reviews.length ? reviews.map((review) => {
      const likes = optionalNumber(review.likes) || 0;
      const rating = optionalNumber(review.rating);
      const ratingText = rating !== null ? `\uBCC4\uC810 ${rating.toFixed(1)}` : "";
      return `
        <article class="review-item">
          <div class="review-meta">
            <strong>\uC88B\uC544\uC694 ${formatNumber(likes)}</strong>
            ${ratingText ? `<span>${escapeHtml(ratingText)}</span>` : ""}
          </div>
          <p>${escapeHtml(review.text)}</p>
        </article>
      `;
    }).join("") : `<article class="review-item empty-review"><p>${loading ? "\uC778\uAE30 \uB9AC\uBDF0 \uBD88\uB7EC\uC624\uB294 \uC911" : "\uC778\uAE30 \uB9AC\uBDF0 \uC5C6\uC74C"}</p></article>`;
  }
  elements.dialogStatus.value = book.status || STATUS_WISHLIST;
  elements.dialogNote.value = book.note || "";
  elements.dialogFavorite.classList.toggle("primary", Boolean(book.favorite));
  elements.dialogFavorite.textContent = book.favorite ? "\u2605" : "\u2606";
  elements.dialogFavorite.title = book.favorite ? "\uBCC4\uD45C \uD574\uC81C" : "\uBCC4\uD45C";
  elements.dialogFavorite.setAttribute("aria-label", elements.dialogFavorite.title);
}

function shouldRefreshDetail(book) {
  if (!book?.url) return false;
  const hasDetail = (book.tags || []).length && (book.topReviews || []).length;
  if (!hasDetail) return true;
  const syncedAt = Date.parse(book.detailSyncedAt || "");
  return !syncedAt || Date.now() - syncedAt > 7 * 24 * 60 * 60 * 1000;
}

async function refreshDialogDetail(bookId) {
  const book = state.shelf.books[bookId];
  if (!book || !shouldRefreshDetail(book) || !window.bookshelf.enrichBook) return;
  renderDialogBook(book, true);
  try {
    const result = await window.bookshelf.enrichBook(bookId);
    if (result?.shelf) state.shelf = normalizeShelf(result.shelf);
    const refreshed = state.shelf.books[bookId];
    renderBooks();
    if (state.currentBookId === bookId && refreshed) renderDialogBook(refreshed, false);
  } catch {
    if (state.currentBookId === bookId) renderDialogBook(state.shelf.books[bookId], false);
  }
}

function openDialog(bookId) {
  const book = state.shelf.books[bookId];
  if (!book) return;
  state.currentBookId = bookId;
  renderDialogBook(book, false);
  elements.dialog.showModal();
  refreshDialogDetail(bookId);
}

async function saveCurrentBook() {
  const book = state.shelf.books[state.currentBookId];
  if (!book) return;
  book.status = elements.dialogStatus.value;
  book.note = elements.dialogNote.value.trim();
  await window.bookshelf.save(state.shelf);
  renderBooks();
}

async function scrapeAuthors() {
  const authors = normalizeAuthors(state.shelf.watchedAuthors);
  if (!authors.length) {
    setMessage("\uAC10\uC2DC\uD560 \uC791\uAC00\uB97C \uD558\uB098 \uC774\uC0C1 \uCD94\uAC00\uD574\uC8FC\uC138\uC694.");
    return;
  }
  elements.authorScrapeButton.disabled = true;
  elements.authorScrapeButton.textContent = "\uC218\uC9D1 \uC911";
  state.progressStartedAt = Date.now();
  setMessage(`\uC791\uAC00 \uAC10\uC2DC \uC218\uC9D1 \uC900\uBE44 \uC911 \u00B7 ${authors.length}\uBA85`, "info");
  try {
    const result = await window.bookshelf.scrapeAuthors({ authors });
    state.shelf = normalizeShelf(result.shelf);
    const errorText = result.errors?.length ? ` / ${result.errors.map((error) => `${error.author}: ${error.message}`).join(" / ")}` : "";
    setMessage(`\uC791\uAC00 \uC218\uC9D1 \uC644\uB8CC. \uC0C8\uB85C \uCD94\uAC00\uB41C \uCC45 ${result.addedCount || 0}\uAD8C${errorText}`, result.errors?.length ? "error" : "success");
    renderAuthors();
    renderPeriods();
    renderCategories();
    renderBooks();
  } catch (error) {
    setMessage(error.message || String(error));
  } finally {
    state.progressStartedAt = null;
    elements.authorScrapeButton.disabled = !normalizeAuthors(state.shelf.watchedAuthors).length;
    elements.authorScrapeButton.textContent = "\uC791\uAC00 \uC218\uC9D1";
  }
}

async function scrapeEvent() {
  if (!elements.eventScrapeButton || !window.bookshelf.scrapeEvent) return;
  elements.eventScrapeButton.disabled = true;
  elements.eventScrapeButton.textContent = "\uC218\uC9D1 \uC911";
  state.progressStartedAt = Date.now();
  setMessage("\uC774\uBCA4\uD2B8 BL \uC18C\uC124 \uC218\uC9D1 \uC900\uBE44 \uC911", "info");
  try {
    const result = await window.bookshelf.scrapeEvent();
    state.shelf = normalizeShelf(result.shelf);
    state.activeCategoryId = EVENT_CATEGORY_ID;
    if (result.errors?.length) {
      setMessage(result.errors.map((error) => `${error.event}: ${error.message}`).join(" / "));
    } else {
      setMessage(`\uC774\uBCA4\uD2B8 \uC218\uC9D1 \uC644\uB8CC. \uC0C8\uB85C \uCD94\uAC00\uB41C \uCC45 ${result.addedCount || 0}\uAD8C`, "success");
    }
    renderPeriods();
    renderCategories();
    renderAuthors();
    renderBooks();
  } catch (error) {
    setMessage(error.message || String(error));
  } finally {
    state.progressStartedAt = null;
    elements.eventScrapeButton.disabled = false;
    elements.eventScrapeButton.textContent = "BL \uC18C\uC124 \uC774\uBCA4\uD2B8 \uC218\uC9D1";
  }
}

async function scrape() {
  const categories = selectedCategories();
  if (!categories.length) {
    setMessage("\uC218\uC9D1\uD560 \uC7A5\uB974\uB97C \uD558\uB098 \uC774\uC0C1 \uC120\uD0DD\uD574\uC8FC\uC138\uC694.");
    return;
  }
  elements.scrapeButton.disabled = true;
  elements.scrapeButton.textContent = "\uC218\uC9D1 \uC911";
  state.progressStartedAt = Date.now();
  setMessage(`${periodNameForId(state.activePeriod)} \uC218\uC9D1 \uC911\uC785\uB2C8\uB2E4.`, "info");
  try {
    const result = await window.bookshelf.scrape({ categories, period: state.activePeriod });
    state.shelf = normalizeShelf(result.shelf);
    if (result.errors?.length) {
      setMessage(result.errors.map((error) => `${error.categoryName}: ${error.message}`).join(" / "));
    } else {
      setMessage(`\uC218\uC9D1 \uC644\uB8CC. \uC0C8\uB85C \uCD94\uAC00\uB41C \uCC45 ${result.addedCount || 0}\uAD8C`, "success");
    }
    renderPeriods();
    renderCategories();
    renderBooks();
  } catch (error) {
    setMessage(error.message || String(error));
  } finally {
    state.progressStartedAt = null;
    elements.scrapeButton.disabled = false;
    elements.scrapeButton.textContent = "\uC218\uC9D1";
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function bindEvents() {
  document.addEventListener("error", (event) => {
    const image = event.target;
    if (!(image instanceof HTMLImageElement)) return;
    if (!image.matches(".book-cover, .dialog-cover")) return;
    image.dataset.failed = "1";
    image.removeAttribute("src");
    image.style.display = "none";
    if (image.classList.contains("book-cover")) {
      const frame = image.closest(".cover-frame");
      if (frame && !frame.querySelector(".cover-placeholder")) {
        frame.insertAdjacentHTML("afterbegin", `<div class="cover-placeholder">${escapeHtml(image.alt || "\uD45C\uC9C0 \uC5C6\uC74C")}</div>`);
      }
    } else {
      const wrap = image.closest(".dialog-cover-wrap");
      if (wrap && !wrap.querySelector(".dialog-cover-placeholder")) {
        wrap.insertAdjacentHTML("beforeend", `<div class="cover-placeholder dialog-cover-placeholder">${escapeHtml(image.alt || "\uD45C\uC9C0 \uC5C6\uC74C")}</div>`);
      }
    }
  }, true);

  elements.scrapeButton.addEventListener("click", scrape);
  elements.authorForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const author = elements.authorInput.value.trim();
    if (!author) return;
  state.shelf.watchedAuthors = normalizeAuthors([...(state.shelf.watchedAuthors || []), author]);
    elements.authorInput.value = "";
    state.shelf = normalizeShelf(await window.bookshelf.save(state.shelf));
    renderAuthors();
    renderBooks();
  });
  elements.authorList?.addEventListener("click", async (event) => {
    const remove = event.target.closest("[data-author-remove]");
    if (remove) {
      state.shelf.watchedAuthors = normalizeAuthors(state.shelf.watchedAuthors).filter((author) => author !== remove.dataset.authorRemove);
      if (state.shelf.activeAuthor === remove.dataset.authorRemove) state.shelf.activeAuthor = "";
      state.shelf = normalizeShelf(await window.bookshelf.save(state.shelf));
      renderAuthors();
      renderBooks();
      return;
    }
    const folder = event.target.closest("[data-author-query]");
    if (!folder) return;
    state.shelf.activeAuthor = state.shelf.activeAuthor === folder.dataset.authorQuery ? "" : folder.dataset.authorQuery;
    state.query = "";
    elements.searchInput.value = "";
    state.shelf = normalizeShelf(await window.bookshelf.save(state.shelf));
    renderAuthors();
    renderBooks();
  });
  elements.authorScrapeButton?.addEventListener("click", scrapeAuthors);
  elements.eventScrapeButton?.addEventListener("click", scrapeEvent);
  elements.periodFilter.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-period]");
    if (!button) return;
    state.activePeriod = button.dataset.period;
    state.shelf.activeAuthor = "";
    renderPeriods();
    renderAuthors();
    renderBooks();
  });
  elements.categoryFilter.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-category-view]");
    if (!button) return;
    state.activeCategoryId = button.dataset.categoryView;
    state.shelf.activeAuthor = "";
    renderCategories();
    renderAuthors();
    renderBooks();
  });
  elements.statusFilter.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-status]");
    if (!button) return;
    state.activeStatus = button.dataset.status;
    elements.statusFilter.querySelectorAll("button").forEach((item) => item.classList.toggle("selected", item === button));
    renderBooks();
  });
  elements.searchInput.addEventListener("input", (event) => {
    state.query = event.target.value;
    renderBooks();
  });
  elements.favoritesOnly.addEventListener("click", () => {
    state.favoritesOnly = !state.favoritesOnly;
    elements.favoritesOnly.classList.toggle("selected", state.favoritesOnly);
    renderBooks();
  });
  elements.exportButton.addEventListener("click", () => {
    const payload = encodeURIComponent(JSON.stringify(state.shelf, null, 2));
    const link = document.createElement("a");
    link.href = `data:application/json;charset=utf-8,${payload}`;
    link.download = "ridi-bookshelf.json";
    link.click();
  });
  elements.bookGrid.addEventListener("click", (event) => {
    const card = event.target.closest("[data-book-id]");
    if (card) openDialog(card.dataset.bookId);
  });
  elements.dialogFavorite.addEventListener("click", async () => {
    const book = state.shelf.books[state.currentBookId];
    if (!book) return;
    book.favorite = !book.favorite;
    await saveCurrentBook();
    openDialog(book.id);
  });
  elements.dialogOpen.addEventListener("click", () => {
    const book = state.shelf.books[state.currentBookId];
    if (book?.url) window.bookshelf.openExternal(book.url);
  });
  elements.dialogSave.addEventListener("click", async (event) => {
    event.preventDefault();
    await saveCurrentBook();
    elements.dialog.close();
  });
}

async function boot() {
  bindEvents();
  bindProgress();
  state.shelf = normalizeShelf(await window.bookshelf.load());
  renderPeriods();
  renderCategories();
  renderAuthors();
  renderBooks();
}

boot();









