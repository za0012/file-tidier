const { app, BrowserWindow, ipcMain, session, shell } = require("electron");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const fs = require("node:fs/promises");
const { scrapeCategory, enrichBooksWithDetails } = require("./scraper");

const STATUS_ALL = "\uC804\uCCB4";
const STATUS_WISHLIST = "\uAD00\uC2EC";
const STATUS_READING = "\uC77D\uB294 \uC911";
const STATUS_DONE = "\uC644\uB3C5";
const STATUS_HOLD = "\uBCF4\uB958";
const APP_NAME = "RIDI \uCC45\uC7A5";
const DATA_DIR_NAME = "ridi-bookshelf";
const LEGACY_DATA_DIR_NAMES = ["\uCC45\uC7A5", APP_NAME];

const PERIODS = [
  { id: "weekly", name: "\uC8FC\uAC04 \uBCA0\uC2A4\uD2B8" },
  { id: "monthly", name: "\uC6D4\uAC04 \uBCA0\uC2A4\uD2B8" },
  { id: "steady", name: "\uC2A4\uD14C\uB514\uC140\uB7EC" }
];


const DEFAULT_CATEGORIES = [
  { id: "4100", name: "BL \uC18C\uC124 e\uBD81 \u00B7 \uC804\uCCB4", url: "https://ridibooks.com/category/4100" },
  { id: "4101", name: "BL \uC18C\uC124 e\uBD81 \u00B7 \uD604\uB300\uBB3C", url: "https://ridibooks.com/category/4101" },
  { id: "4102", name: "BL \uC18C\uC124 e\uBD81 \u00B7 \uD310\uD0C0\uC9C0\uBB3C", url: "https://ridibooks.com/category/4102" },
  { id: "4103", name: "BL \uC18C\uC124 e\uBD81 \u00B7 \uC5ED\uC0AC/\uC2DC\uB300\uBB3C", url: "https://ridibooks.com/category/4103" },
  { id: "4104", name: "BL \uC18C\uC124 e\uBD81 \u00B7 \uD574\uC678 \uC18C\uC124", url: "https://ridibooks.com/category/4104" }
];

const EVENT_SOURCE = {
  id: "event:96367:2532",
  name: "\uC774\uBCA4\uD2B8 \u00B7 BL \uC18C\uC124",
  url: "https://ridibooks.com/event/96367?tab=2532&fresh=1784564269922",
  period: "event",
  periodName: "\uC774\uBCA4\uD2B8"
};

const STATUS_ALIASES = new Map([
  [STATUS_ALL, STATUS_ALL],
  [STATUS_WISHLIST, STATUS_WISHLIST],
  [STATUS_READING, STATUS_READING],
  [STATUS_DONE, STATUS_DONE],
  [STATUS_HOLD, STATUS_HOLD]
]);

function getDataPath() {
  return path.join(app.getPath("userData"), "bookshelf.json");
}

function getLegacyDataPaths() {
  return LEGACY_DATA_DIR_NAMES.map((name) => path.join(app.getPath("appData"), name, "bookshelf.json"));
}

function periodNameForId(periodId) {
  if (String(periodId) === EVENT_SOURCE.period) return EVENT_SOURCE.periodName;
  return PERIODS.find((period) => period.id === String(periodId))?.name || String(periodId || PERIODS[0].name);
}

function categoryNameForId(categoryId) {
  if (String(categoryId) === EVENT_SOURCE.id) return EVENT_SOURCE.name;
  return DEFAULT_CATEGORIES.find((category) => category.id === String(categoryId))?.name || "";
}

function normalizeAuthors(authors) {
  return [...new Set((Array.isArray(authors) ? authors : [])
    .map((author) => String(author || "").trim())
    .filter(Boolean))];
}

function buildAuthorSearchUrl(author) {
  return `https://ridibooks.com/search?q=${encodeURIComponent(author)}`;
}

function buildCategoryUrl(categoryId, period = "weekly") {
  const id = String(categoryId);
  if (period === "weekly") return `https://ridibooks.com/category/${id}`;
  return `https://ridibooks.com/category/${id}?tab=bestsellers&category=${id}&page=1&period=${period}`;
}

function optionalNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(String(value).replace(/,/g, ""));
  return Number.isFinite(number) ? number : null;
}

function normalizeCategory(category, index) {
  const id = String(category?.id || "");
  if (id === EVENT_SOURCE.id) {
    return {
      ...EVENT_SOURCE,
      ...category,
      id: EVENT_SOURCE.id,
      name: category?.name || EVENT_SOURCE.name,
      url: category?.url || EVENT_SOURCE.url
    };
  }
  const matched = DEFAULT_CATEGORIES.find((item) => item.id === id);
  const fallback = matched || DEFAULT_CATEGORIES[index] || DEFAULT_CATEGORIES[0];
  const name = category?.name || "";
  const useFallbackName = !name || name.includes("?") || name.startsWith("\uC7A5\uB974 ");
  return {
    id: id || fallback.id,
    name: useFallbackName ? fallback.name : name,
    url: category?.url || buildCategoryUrl(id || fallback.id, "weekly")
  };
}

function bookProductId(url) {
  return String(url || "").match(/\/books\/([^/?#]+)/)?.[1] || "";
}

function coverMatchesBookUrl(cover, url) {
  const productId = bookProductId(url);
  if (!cover || !productId) return Boolean(cover);
  return !String(cover).includes("img.ridicdn.net/cover/") || String(cover).includes(`/cover/${productId}/`);
}

function coverFromBookUrl(url) {
  const productId = bookProductId(url);
  return productId ? `https://img.ridicdn.net/cover/${productId}/xxlarge?dpi=xhdpi` : "";
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

function normalizePlacement(placement, fallback = {}) {
  const categoryId = String(placement?.categoryId || fallback.categoryId || "");
  const period = String(placement?.period || fallback.period || "weekly");
  return {
    categoryId,
    categoryName: categoryNameForId(categoryId) || placement?.categoryName || fallback.categoryName || "",
    period,
    periodName: periodNameForId(period),
    rank: Number(placement?.rank || fallback.rank || 0),
    scrapedAt: placement?.scrapedAt || fallback.scrapedAt || null
  };
}

function normalizeBook(book) {
  const originalTitle = book.originalTitle || book.title || "";
  const status = STATUS_ALIASES.get(book.status) || STATUS_WISHLIST;
  const fallbackPlacement = {
    categoryId: book.categoryId,
    categoryName: book.categoryName,
    period: book.period || "monthly",
    rank: book.rank,
    scrapedAt: book.scrapedAt
  };
  const placements = Array.isArray(book.placements) && book.placements.length
    ? book.placements.map((placement) => normalizePlacement(placement, fallbackPlacement))
    : [normalizePlacement(fallbackPlacement)];
  const primaryPlacement = placements[0] || normalizePlacement(fallbackPlacement);
  const isAuthorBook = primaryPlacement.period === "author" || placements.some((placement) => placement.period === "author");
  const normalizedCover = normalizeCover(book.cover);
  const fallbackCover = normalizedCover || coverFromBookUrl(book.url);
  const safeCover = isAuthorBook && !book.coverTrusted && !coverMatchesBookUrl(fallbackCover, book.url) ? coverFromBookUrl(book.url) : fallbackCover;

  return {
    ...book,
    title: normalizeSeriesTitle(book.title || originalTitle),
    originalTitle,
    volume: book.volume || extractVolume(originalTitle),
    cover: safeCover,
    coverTrusted: Boolean(book.coverTrusted),
    genreTag: book.genreTag || "",
    tags: normalizeTags(book.tags),
    topReviews: normalizeReviews(book.topReviews),
    rating: optionalNumber(book.rating),
    reviewCount: optionalNumber(book.reviewCount),
    categoryId: primaryPlacement.categoryId,
    categoryName: primaryPlacement.categoryName,
    period: primaryPlacement.period,
    periodName: primaryPlacement.periodName,
    rank: primaryPlacement.rank || book.rank || 0,
    placements,
    favorite: Boolean(book.favorite),
    status,
    note: book.note || ""
  };
}

function normalizeShelf(data) {
  const categoryMap = new Map(DEFAULT_CATEGORIES.map((category, index) => [category.id, normalizeCategory(category, index)]));
  if (Array.isArray(data?.categories)) {
    data.categories.forEach((category, index) => {
      const normalized = normalizeCategory(category, index);
      categoryMap.set(normalized.id, normalized);
    });
  }

  const books = Object.fromEntries(
    Object.entries(data?.books || {}).map(([id, book]) => [id, normalizeBook(book)])
  );

  const derivedAuthors = Object.values(books)
    .flatMap((book) => Array.isArray(book.placements) ? book.placements : [])
    .map((placement) => String(placement.categoryId || "").match(/^author:(.+)$/)?.[1] || "")
    .filter(Boolean);
  const explicitWatchedAuthors = Array.isArray(data?.watchedAuthors) ? data.watchedAuthors : [];
  const shouldDeriveWatchedAuthors = !data?.watchedAuthorsInitialized && explicitWatchedAuthors.length === 0;

  return {
    categories: [...categoryMap.values()],
    periods: PERIODS,
    books,
    watchedAuthors: normalizeAuthors([...explicitWatchedAuthors, ...(shouldDeriveWatchedAuthors ? derivedAuthors : [])]),
    watchedAuthorsInitialized: true,
    activeAuthor: typeof data?.activeAuthor === "string" ? data.activeAuthor : "",
    lastSyncedAt: data?.lastSyncedAt || null,
    lastAuthorSyncedAt: data?.lastAuthorSyncedAt || null,
    lastAddedCount: Number(data?.lastAddedCount || 0),
    lastAddedIds: Array.isArray(data?.lastAddedIds) ? data.lastAddedIds : []
  };
}

function mergeCategoryList(categories, category) {
  const map = new Map((Array.isArray(categories) ? categories : []).map((item, index) => {
    const normalized = normalizeCategory(item, index);
    return [normalized.id, normalized];
  }));
  const normalizedCategory = normalizeCategory(category);
  map.set(normalizedCategory.id, normalizedCategory);
  return [...map.values()];
}

async function readShelf() {
  try {
    const raw = await fs.readFile(getDataPath(), "utf8");
    return normalizeShelf(JSON.parse(raw));
  } catch (error) {
    if (error.code !== "ENOENT") {
      console.warn("Could not read shelf data:", error);
    }

    return normalizeShelf({ categories: DEFAULT_CATEGORIES, periods: PERIODS, books: {}, lastSyncedAt: null });
  }
}

async function writeShelf(data) {
  const normalized = normalizeShelf(data);
  await fs.mkdir(path.dirname(getDataPath()), { recursive: true });
  await fs.writeFile(getDataPath(), JSON.stringify(normalized, null, 2), "utf8");
  return normalized;
}

async function migrateLegacyShelf() {
  const canonicalPath = getDataPath();
  let canonicalRaw = "";
  try {
    canonicalRaw = await fs.readFile(canonicalPath, "utf8");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }

  let merged = canonicalRaw ? normalizeShelf(JSON.parse(canonicalRaw)) : normalizeShelf({});
  for (const legacyPath of getLegacyDataPaths()) {
    if (canonicalPath === legacyPath) continue;
    try {
      const legacy = normalizeShelf(JSON.parse(await fs.readFile(legacyPath, "utf8")));
      merged = normalizeShelf({
        ...merged,
        ...legacy,
        categories: merged.categories?.length ? merged.categories : legacy.categories,
        periods: merged.periods?.length ? merged.periods : legacy.periods,
        books: { ...(merged.books || {}), ...(legacy.books || {}) },
        watchedAuthors: normalizeAuthors([...(merged.watchedAuthors || []), ...(legacy.watchedAuthors || [])]),
        lastAddedIds: Array.from(new Set([...(merged.lastAddedIds || []), ...(legacy.lastAddedIds || [])])),
        lastAddedCount: Math.max(Number(merged.lastAddedCount || 0), Number(legacy.lastAddedCount || 0)),
        lastSyncedAt: merged.lastSyncedAt || legacy.lastSyncedAt,
        lastAuthorSyncedAt: merged.lastAuthorSyncedAt || legacy.lastAuthorSyncedAt
      });
    } catch (error) {
      if (error.code !== "ENOENT") console.warn("Could not migrate legacy shelf data:", error);
    }
  }
  await fs.mkdir(path.dirname(canonicalPath), { recursive: true });
  await fs.writeFile(canonicalPath, JSON.stringify(merged, null, 2), "utf8");
}

function placementKey(placement) {
  return `${placement.categoryId}|${placement.period}`;
}

function mergePlacements(existingPlacements = [], nextPlacement) {
  const map = new Map(existingPlacements.map((placement) => [placementKey(placement), placement]));
  map.set(placementKey(nextPlacement), nextPlacement);
  return [...map.values()];
}

function isValidAuthorBook(book, author) {
  const bookAuthor = String(book?.author || "");
  const title = String(book?.title || "").trim();
  if (!book?.url || !book.url.includes("/books/")) return false;
  if (title.length < 2) return false;
  if (!title || title === author) return false;
  if (title.includes(`${author} -`) || title.includes(`${author},`)) return false;
  if (!bookAuthor.includes(author)) return false;
  if (bookAuthor.includes("\uC791\uAC00 \u00B7")) return false;
  return true;
}

function mergeBook(existing = {}, scraped, placement) {
  const normalizedExisting = normalizeBook(existing);
  const placements = mergePlacements(normalizedExisting.placements || [], placement);
  const scrapedTags = normalizeTags(scraped.tags);
  const scrapedReviews = normalizeReviews(scraped.topReviews);
  return normalizeBook({
    ...scraped,
    ...normalizedExisting,
    title: scraped.title || normalizedExisting.title,
    originalTitle: scraped.originalTitle || normalizedExisting.originalTitle,
    volume: scraped.volume || normalizedExisting.volume,
    author: scraped.author || normalizedExisting.author,
    cover: scraped.cover || normalizedExisting.cover,
    coverTrusted: Boolean(scraped.coverTrusted || normalizedExisting.coverTrusted),
    url: scraped.url || normalizedExisting.url,
    genreTag: scraped.genreTag || normalizedExisting.genreTag || "",
    tags: scrapedTags.length ? scrapedTags : normalizedExisting.tags,
    topReviews: scrapedReviews.length ? scrapedReviews : normalizedExisting.topReviews,
    rating: optionalNumber(scraped.rating) ?? normalizedExisting.rating,
    reviewCount: optionalNumber(scraped.reviewCount) ?? normalizedExisting.reviewCount,
    rank: placement.rank,
    categoryId: placement.categoryId,
    categoryName: placement.categoryName,
    period: placement.period,
    periodName: placement.periodName,
    scrapedAt: placement.scrapedAt,
    placements
  });
}

function isBlNovelBook(book) {
  const tags = Array.isArray(book.tags) ? book.tags : [];
  const detailText = [book.genreTag, ...tags].join(" ");
  if (/웹툰|만화|코믹|comic/i.test(detailText)) return false;
  if (/BL/i.test(detailText) && /소설|e북|ebook/i.test(detailText)) return true;
  return String(book.categoryId || "") === EVENT_SOURCE.id || String(book.categoryName || "").includes("BL \uC18C\uC124");
}

let imageHeadersRegistered = false;

function registerImageHeaders() {
  if (imageHeadersRegistered) return;
  imageHeadersRegistered = true;
  session.defaultSession.webRequest.onBeforeSendHeaders(
    { urls: ["https://img.ridicdn.net/*", "http://img.ridicdn.net/*"] },
    (details, callback) => {
      details.requestHeaders.Referer = "https://ridibooks.com/";
      details.requestHeaders["User-Agent"] = details.requestHeaders["User-Agent"] || "Mozilla/5.0";
      callback({ requestHeaders: details.requestHeaders });
    }
  );
}

async function createWindow() {
  const win = new BrowserWindow({
    width: 1240,
    height: 820,
    minWidth: 980,
    minHeight: 680,
    title: APP_NAME,
    backgroundColor: "#f7f9fc",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  win.setMenuBarVisibility(false);
  await win.loadURL(pathToFileURL(path.join(__dirname, "renderer", "index.html")).toString()).catch((error) => {
    console.warn("Could not load renderer:", error);
  });
}

app.whenReady().then(async () => {
  app.setName(APP_NAME);
  app.setPath("userData", path.join(app.getPath("appData"), DATA_DIR_NAME));
  await migrateLegacyShelf();
  registerImageHeaders();
  ipcMain.handle("shelf:load", readShelf);

  ipcMain.handle("shelf:save", async (_event, data) => {
    return writeShelf(data);
  });

  ipcMain.handle("shelf:enrichBook", async (_event, bookId) => {
    const shelf = await readShelf();
    const existing = shelf.books?.[bookId];
    if (!existing) return { shelf, book: null };

    const [enriched] = await enrichBooksWithDetails([existing], { limit: 1, interactiveReviews: true });
    const tags = normalizeTags(enriched?.tags);
    const topReviews = normalizeReviews(enriched?.topReviews);
    const nextBook = normalizeBook({
      ...existing,
      cover: enriched?.cover || existing.cover,
      coverTrusted: Boolean(enriched?.coverTrusted || existing.coverTrusted),
      tags: tags.length ? tags : existing.tags,
      topReviews: topReviews.length ? topReviews : existing.topReviews,
      detailSyncedAt: new Date().toISOString()
    });
    const nextShelf = await writeShelf({
      ...shelf,
      books: {
        ...shelf.books,
        [bookId]: nextBook
      }
    });
    return { shelf: nextShelf, book: nextBook };
  });

  ipcMain.handle("shelf:scrape", async (event, payload) => {
    const shelf = await readShelf();
    const selectedCategories = Array.isArray(payload) ? payload : payload?.categories;
    const period = Array.isArray(payload) ? "monthly" : payload?.period || "weekly";
    const targetCategories = Array.isArray(selectedCategories) && selectedCategories.length
      ? selectedCategories.map(normalizeCategory)
      : shelf.categories;
    const existingIds = new Set(Object.keys(shelf.books));

    const results = [];
    for (const [index, category] of targetCategories.entries()) {
      const scrapeTarget = {
        ...category,
        period,
        periodName: periodNameForId(period),
        url: buildCategoryUrl(category.id, period)
      };
      try {
        event.sender.send("shelf:progress", {
          type: "category",
          current: index + 1,
          total: targetCategories.length,
          label: `${periodNameForId(period)} \u00B7 ${category.name}`
        });
        const books = await scrapeCategory({ ...scrapeTarget, expectedCount: 200 }, {
          onProgress: (progress) => {
            event.sender.send("shelf:progress", {
              type: "category",
              current: index + 1,
              total: targetCategories.length,
              label: `${periodNameForId(period)} · ${category.name}`,
              title: `${progress.page}/${progress.pages}페이지 · ${progress.found || 0}권`
            });
          }
        });
        results.push({ status: "fulfilled", value: books, category: scrapeTarget });
      } catch (error) {
        results.push({ status: "rejected", reason: error, category: scrapeTarget });
      }
    }

    const books = { ...shelf.books };
    const errors = [];
    const addedIds = [];
    const addedByCategory = {};

    results.forEach((result) => {
      if (result.status === "rejected") {
        errors.push({
          categoryId: result.category.id,
          categoryName: result.category.name,
          message: result.reason?.message || String(result.reason)
        });
        return;
      }

      result.value.forEach((book) => {
        const placement = normalizePlacement({
          categoryId: result.category.id,
          categoryName: result.category.name,
          period,
          rank: book.rank,
          scrapedAt: book.scrapedAt
        });
        if (!existingIds.has(book.id) && !books[book.id]) {
          addedIds.push(book.id);
          addedByCategory[result.category.id] = (addedByCategory[result.category.id] || 0) + 1;
        }
        books[book.id] = books[book.id] ? mergeBook(books[book.id], book, placement) : normalizeBook({ ...book, placements: [placement] });
      });
    });

    const nextShelf = await writeShelf({
      ...shelf,
      categories: shelf.categories,
      periods: PERIODS,
      books,
      lastSyncedAt: new Date().toISOString(),
      lastAddedCount: addedIds.length,
      lastAddedIds: addedIds
    });

    return { shelf: nextShelf, errors, addedCount: addedIds.length, addedIds, addedByCategory };
  });

  ipcMain.handle("shelf:scrapeAuthors", async (event, payload) => {
    const shelf = await readShelf();
    const authors = normalizeAuthors(payload?.authors || shelf.watchedAuthors);
    const existingIds = new Set(Object.keys(shelf.books));
    const books = { ...shelf.books };
    const errors = [];
    const addedIds = [];

    for (const [authorIndex, author] of authors.entries()) {
      const source = {
        id: `author:${author}`,
        name: `\uC791\uAC00 \u00B7 ${author}`,
        url: buildAuthorSearchUrl(author),
        period: "author",
        periodName: "\uC791\uAC00 \uAC10\uC2DC",
        authorName: author
      };
      try {
        event.sender.send("shelf:progress", {
          type: "author",
          phase: "search",
          current: authorIndex + 1,
          total: authors.length,
          label: `\uC791\uAC00 \uAC80\uC0C9 \u00B7 ${author}`
        });
        const scrapedBooks = await scrapeCategory({ ...source, expectedCount: 200 }, {
          onProgress: (progress) => {
            event.sender.send("shelf:progress", {
              type: "author",
              phase: "search",
              current: authorIndex + 1,
              total: authors.length,
              label: `작가 검색 · ${author}`,
              title: `${progress.page}/${progress.pages}페이지 · ${progress.found || 0}권`
            });
          }
        });
        const booksToMerge = scrapedBooks.filter((book) => isValidAuthorBook(book, author));
        const enrichedBooks = await enrichBooksWithDetails(booksToMerge, {
          limit: booksToMerge.length,
          onProgress: (progress) => {
            event.sender.send("shelf:progress", {
              type: "author",
              phase: "detail",
              current: progress.current,
              total: progress.total,
              label: `\uD45C\uC9C0/\uD0DC\uADF8 \uBCF4\uAC15 \u00B7 ${author}`,
              title: progress.title
            });
          }
        });
        enrichedBooks.forEach((book) => {
          const placement = normalizePlacement({
            categoryId: source.id,
            categoryName: source.name,
            period: "author",
            rank: book.rank,
            scrapedAt: book.scrapedAt
          });
          if (!existingIds.has(book.id) && !books[book.id]) addedIds.push(book.id);
          books[book.id] = books[book.id] ? mergeBook(books[book.id], book, placement) : normalizeBook({ ...book, placements: [placement] });
        });
      } catch (error) {
        errors.push({ author, message: error.message || String(error) });
      }
    }

    const nextShelf = await writeShelf({
      ...shelf,
      books,
      watchedAuthors: authors,
      lastAuthorSyncedAt: new Date().toISOString(),
      lastSyncedAt: new Date().toISOString(),
      lastAddedCount: addedIds.length,
      lastAddedIds: addedIds
    });

    return { shelf: nextShelf, errors, addedCount: addedIds.length, addedIds };
  });

  ipcMain.handle("shelf:scrapeEvent", async (event) => {
    const shelf = await readShelf();
    const existingIds = new Set(Object.keys(shelf.books));
    const books = { ...shelf.books };
    const addedIds = [];
    const errors = [];

    try {
      event.sender.send("shelf:progress", {
        type: "event",
        current: 1,
        total: 2,
        label: "\uC774\uBCA4\uD2B8 \uBAA9\uB85D \uC218\uC9D1"
      });
      const scrapedBooks = await scrapeCategory({ ...EVENT_SOURCE, expectedCount: 200 }, {
        onProgress: (progress) => {
          event.sender.send("shelf:progress", {
            type: "event",
            current: 1,
            total: 2,
            label: "\uC774\uBCA4\uD2B8 \uBAA9\uB85D \uC218\uC9D1",
            title: `${progress.page}/${progress.pages}\uD398\uC774\uC9C0 \u00B7 ${progress.found || 0}\uAD8C`
          });
        }
      });
      const enrichedBooks = await enrichBooksWithDetails(scrapedBooks, {
        limit: scrapedBooks.length,
        interactiveReviews: true,
        onProgress: (progress) => {
          event.sender.send("shelf:progress", {
            type: "event",
            current: progress.current,
            total: progress.total,
            label: "\uC774\uBCA4\uD2B8 \uC0C1\uC138 \uBCF4\uAC15",
            title: progress.title
          });
        }
      });

      enrichedBooks.filter(isBlNovelBook).forEach((book, index) => {
        const placement = normalizePlacement({
          categoryId: EVENT_SOURCE.id,
          categoryName: EVENT_SOURCE.name,
          period: EVENT_SOURCE.period,
          periodName: EVENT_SOURCE.periodName,
          rank: index + 1,
          scrapedAt: book.scrapedAt
        });
        if (!existingIds.has(book.id) && !books[book.id]) addedIds.push(book.id);
        books[book.id] = books[book.id] ? mergeBook(books[book.id], book, placement) : normalizeBook({ ...book, rank: index + 1, categoryId: EVENT_SOURCE.id, categoryName: EVENT_SOURCE.name, period: EVENT_SOURCE.period, periodName: EVENT_SOURCE.periodName, placements: [placement] });
      });
    } catch (error) {
      errors.push({ event: EVENT_SOURCE.name, message: error.message || String(error) });
    }

    const nextShelf = await writeShelf({
      ...shelf,
      categories: mergeCategoryList(shelf.categories, EVENT_SOURCE),
      books,
      lastSyncedAt: new Date().toISOString(),
      lastAddedCount: addedIds.length,
      lastAddedIds: addedIds
    });

    return { shelf: nextShelf, errors, addedCount: addedIds.length, addedIds };
  });

  ipcMain.handle("shell:openExternal", async (_event, url) => {
    await shell.openExternal(url);
  });

  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});













