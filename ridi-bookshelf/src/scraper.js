const { BrowserWindow, net } = require("electron");
const cheerio = require("cheerio");
const crypto = require("node:crypto");

const USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36";
const LOAD_HEADERS = "Accept-Language: ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6";
const STATUS_WISHLIST = "\uAD00\uC2EC";
const DETAIL_HEADERS = {
  "User-Agent": USER_AGENT,
  "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
  "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
};

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function loadWithRetry(win, url, options = {}, attempts = 3, timeoutMs = 12000) {
  let lastError;
  for (let index = 0; index < attempts; index += 1) {
    try {
      const result = await Promise.race([
        win.loadURL(url, options).then(() => "loaded"),
        delay(timeoutMs).then(() => "timeout")
      ]);
      if (result === "timeout" && win.webContents.isLoading()) {
        win.webContents.stop();
      }
      return;
    } catch (error) {
      lastError = error;
      if (win.webContents.isLoading()) win.webContents.stop();
      await delay(650 + index * 550);
    }
  }
  if (lastError) throw lastError;
}

function normalizeText(value) {
  return (value || "").replace(/\s+/g, " ").trim();
}

function normalizeCover(cover) {
  if (!cover) return "";
  const value = String(cover);
  const productId = value.match(/img\.ridicdn\.net\/cover\/([^/?#]+)/)?.[1];
  if (productId) return coverFromProductId(productId);
  return value.replace(/\/(small|large|xlarge|xxlarge)(?:[?#].*)?$/, "/xxlarge?dpi=xhdpi");
}

function absoluteUrl(url) {
  if (!url) return "";
  const clean = String(url).split(" ")[0].replace(/,+$/, "");
  try {
    return new URL(clean, "https://ridibooks.com").toString().replace(/\/(small|large|xlarge)([?#]?)$/, "/xxlarge$2");
  } catch {
    return "";
  }
}

function parseRatingReview(text) {
  const clean = normalizeText(text);
  const compactMatch = clean.match(/\b([0-5](?:\.\d)?)\s*\(([0-9,]+)\)/);
  if (compactMatch) {
    return {
      rating: Number(compactMatch[1]),
      reviewCount: Number(compactMatch[2].replace(/,/g, ""))
    };
  }

  const ratingMatch = clean.match(/(?:\uD3C9\uC810|\uBCC4\uC810)\s*([0-5](?:\.\d)?)/);
  const reviewMatch = clean.match(/(?:\uB9AC\uBDF0|\uD3C9\uAC00)\s*([0-9,]+)/);
  return {
    rating: ratingMatch ? Number(ratingMatch[1]) : null,
    reviewCount: reviewMatch ? Number(reviewMatch[1].replace(/,/g, "")) : null
  };
}

function parseRatingDistribution(ratings) {
  if (!Array.isArray(ratings)) return { rating: null, reviewCount: null };
  const total = ratings.reduce((sum, item) => sum + Number(item?.count || 0), 0);
  if (!total) return { rating: null, reviewCount: null };
  const weighted = ratings.reduce((sum, item) => sum + Number(item?.rating || 0) * Number(item?.count || 0), 0);
  return {
    rating: Math.round((weighted / total) * 10) / 10,
    reviewCount: total
  };
}

function pickNumber(...values) {
  for (const value of values) {
    if (value === null || value === undefined || value === "") continue;
    const number = Number(String(value).replace(/,/g, ""));
    if (Number.isFinite(number)) return number;
  }
  return null;
}

function bookIdFromUrl(url) {
  return String(url || "").match(/\/books\/([^/?#]+)/)?.[1] || "";
}

function coverFromProductId(productId) {
  return productId ? `https://img.ridicdn.net/cover/${productId}/xxlarge?dpi=xhdpi` : "";
}

function extractGenreTag(text) {
  const clean = normalizeText(text);
  const genres = ["\uD604\uB300\uBB3C", "\uD310\uD0C0\uC9C0\uBB3C", "\uC5ED\uC0AC/\uC2DC\uB300\uBB3C", "\uD574\uC678 \uC18C\uC124", "\uC131\uC778"];
  return genres.find((genre) => clean.includes(genre)) || "";
}

function cleanSearchTitle(title) {
  return normalizeText(title)
    .replace(/^\[e\uBD81\]\s*/, "")
    .replace(/\s+/g, " ")
    .trim();
}

function stableBookId(book) {
  const url = book.url || "";
  const productMatch = url.match(/\/books\/([^/?#]+)/);

  if (productMatch) {
    return `ridi-${productMatch[1]}`;
  }

  return crypto
    .createHash("sha1")
    .update(`${book.title}|${book.author}|${url}`)
    .digest("hex");
}

function pickFirst(value) {
  if (Array.isArray(value)) return value.map(pickFirst).filter(Boolean).join(", ");
  if (value && typeof value === "object") {
    for (const key of ["name", "title", "text", "author_name", "authorName"]) {
      if (typeof value[key] === "string" || typeof value[key] === "number") return String(value[key]);
    }
    return Object.values(value).map(pickFirst).filter(Boolean).join(", ");
  }
  return value ? String(value) : "";
}

function parseNextData($, category) {
  const raw = $("#__NEXT_DATA__").text();
  if (!raw) return [];

  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    return [];
  }

  const candidates = [];

  function walk(value) {
    if (!value || typeof value !== "object") return;

    if (Array.isArray(value)) {
      value.forEach(walk);
      return;
    }
    const title = value.title || value.book_title || value.bookTitle || value.name;
    const author = value.author || value.authors || value.author_name || value.authorName || value.writer;
    const bookId = value.book_id || value.bookId || value.id || value.b_id;
    const thumbnail =
      value.thumbnail ||
      value.thumbnail_url ||
      value.thumbnailUrl ||
      value.cover ||
      value.cover_url ||
      value.coverUrl ||
      value.image ||
      value.image_url ||
      value.imageUrl;
    const productId = bookId ? String(bookId) : "";
    const hasBookSignal = thumbnail || author || value.book_title || value.bookTitle || value.publisher || value.price;
    const href = value.url || value.href || value.link || (productId.length >= 5 ? `/books/${productId}` : "");
    if (title && hasBookSignal && String(href).includes("/books/")) {
      const ratingReview = parseRatingReview(pickFirst(value.rating_text || value.ratingText || value.review || value.reviews || ""));
      const ratingDistribution = parseRatingDistribution(value.ratings);
      candidates.push({
        title: normalizeText(pickFirst(title)),
        author: normalizeText(pickFirst(author)),
        url: absoluteUrl(href),
        cover: absoluteUrl(pickFirst(thumbnail)),
        description: normalizeText(pickFirst(value.description || value.introduction || "")),
        rating: pickNumber(value.rating, value.star_rate, value.starRate, value.rate, value.score) ?? ratingDistribution.rating ?? ratingReview.rating,
        reviewCount: pickNumber(value.review_count, value.reviewCount, value.rating_count, value.ratingCount, value.comment_count, value.commentCount) ?? ratingDistribution.reviewCount ?? ratingReview.reviewCount,
        rawRank: value.rank || value.ranking || value.index || value.order
      });
    }

    Object.values(value).forEach(walk);
  }

  walk(data);
  return dedupeCandidates(candidates, category);
}

function findBookScope($, link, title) {
  const fallback = link.closest("li, article, section, div");
  const parents = link.parents("li, article, section, div").toArray();
  for (const element of parents) {
    const scope = $(element);
    const text = normalizeText(scope.text());
    if (!text.includes(title)) continue;
    if (!scope.find("img").length) continue;
    if (parseRatingReview(text).rating !== null || /\([0-9,]+\)/.test(text)) return scope;
  }
  return fallback.length ? fallback : link;
}

function parseAuthorCards($, category) {
  const authorName = normalizeText(category.authorName || "");
  if (!authorName) return [];

  const grouped = new Map();
  $("a[href*='/books/']").each((index, element) => {
    const link = $(element);
    const href = absoluteUrl(link.attr("href"));
    const productId = bookIdFromUrl(href);
    if (!productId) return;

    const entry = grouped.get(productId) || {
      url: href,
      productId,
      titleCandidates: [],
      texts: [],
      cover: ""
    };

    const linkText = cleanSearchTitle(link.text());
    if (linkText && linkText.length <= 80) entry.titleCandidates.push(linkText);

    const img = link.find("img").first();
    const imgAlt = cleanSearchTitle(img.attr("alt") || "");
    const imgSrc = absoluteUrl(img.attr("src") || img.attr("data-src") || img.attr("srcset")?.split(",")?.[0]);
    if (imgAlt && imgAlt.length <= 80) entry.titleCandidates.push(imgAlt);
    if (imgSrc && imgSrc.includes("img.ridicdn.net/cover")) entry.cover = imgSrc;

    link.parents("li, article, section, div").slice(0, 6).each((_parentIndex, parent) => {
      const text = normalizeText($(parent).text());
      if (text) entry.texts.push(text);
      const parentImg = $(parent).find("img").first();
      const parentImgAlt = cleanSearchTitle(parentImg.attr("alt") || "");
      const parentImgSrc = absoluteUrl(parentImg.attr("src") || parentImg.attr("data-src") || parentImg.attr("srcset")?.split(",")?.[0]);
      if (parentImgAlt && parentImgAlt.length <= 80) entry.titleCandidates.push(parentImgAlt);
      if (!entry.cover && parentImgSrc && parentImgSrc.includes("img.ridicdn.net/cover")) entry.cover = parentImgSrc;
    });

    grouped.set(productId, entry);
  });

  const candidates = [];
  [...grouped.values()].forEach((entry) => {
    const title = [...new Set(entry.titleCandidates)]
      .filter((candidate) => candidate && !candidate.includes("http"))
      .sort((a, b) => a.length - b.length)[0] || "";
    if (!title || title === authorName) return;
    if (title.includes(`${authorName} -`) || title.includes(`${authorName},`)) return;

    const matchingTexts = entry.texts
      .filter((text) => text.includes(authorName) && text.includes(title))
      .sort((a, b) => a.length - b.length);
    const bestText = matchingTexts.find((text) => parseRatingReview(text).rating !== null) || matchingTexts[0] || "";
    if (!bestText) return;

    const ratingReview = parseRatingReview(bestText);
    candidates.push({
      title,
      author: authorName,
      url: entry.url,
      cover: normalizeCover(entry.cover).includes(`/cover/${entry.productId}/`) ? normalizeCover(entry.cover) : "",
      description: "",
      genreTag: extractGenreTag(bestText),
      rating: ratingReview.rating,
      reviewCount: ratingReview.reviewCount,
      rawRank: candidates.length + 1
    });
  });

  return dedupeCandidates(candidates, category);
}

function parseCards($, category) {
  const candidates = [];

  $("a[href*='/books/']").each((index, element) => {
    const link = $(element);
    const card = link.closest("li, article, section, div");
    let scope = card.length ? card : link;
    const href = absoluteUrl(link.attr("href"));
    if (!href) return;

    let image = scope.find("img").first();
    const cover = absoluteUrl(
      image.attr("src") ||
      image.attr("data-src") ||
      image.attr("data-original") ||
      image.attr("srcset")?.split(",")?.[0]
    );

    const title = normalizeText(
      image.attr("alt") ||
      link.attr("title") ||
      scope.find("[class*='title'], [class*='Title']").first().text() ||
      link.text()
    );

    if (!title || title.length > 120 || title.toLowerCase() === "ridi") return;

    scope = findBookScope($, link, title);
    image = scope.find("img").first();
    const text = normalizeText(scope.text());
    const rankMatch = text.match(/^(\d{1,3})\b/) || text.match(/\b(\d{1,3})\uC704\b/);
    const ratingReview = parseRatingReview(text);
    const author = guessAuthor(scope, title);

    candidates.push({
      title,
      author,
      url: href,
      cover,
      description: "",
      genreTag: extractGenreTag(text),
      rating: ratingReview.rating,
      reviewCount: ratingReview.reviewCount,
      rawRank: rankMatch ? Number(rankMatch[1]) : null
    });
  });

  return dedupeCandidates(candidates, category);
}

function guessAuthor(scope, title) {
  const direct = normalizeText(
    scope.find("[class*='author'], [class*='Author'], [class*='metadata'], [class*='Metadata']").first().text()
  );
  if (direct && direct !== title && direct.length < 80) return direct;

  const text = normalizeText(scope.text().replace(title, ""));
  const authorMatch = text.match(/([\uAC00-\uD7A3A-Za-z0-9_.\-\s]{2,40})\s*(\uC800|\uAE00|\uC6D0\uC791|\uADF8\uB9BC|\uC9C0\uC74C|\uC791\uAC00)/);
  return normalizeText(authorMatch?.[1] || "");
}

function extractVolume(title) {
  const match = normalizeText(title).match(/(?:\(|\s)(\d+\s*\uAD8C)\)?$/);
  return match ? match[1].replace(/\s+/g, "") : "";
}

function normalizeSeriesTitle(title) {
  const clean = normalizeText(title);
  const normalized = clean
    .replace(/\s*\(?\d+\s*\uAD8C\)?$/u, "")
    .replace(/\s*\(?\uC81C\s*\d+\s*\uAD8C\)?$/u, "")
    .trim();
  return normalized || clean;
}

function dedupeCandidates(candidates, category) {
  const seen = new Set();
  const scrapedAt = new Date().toISOString();
  const unique = [];

  candidates
    .filter((book) => book.title && book.url)
    .forEach((book) => {
      const id = stableBookId(book);
      if (seen.has(id)) return;
      seen.add(id);
      unique.push({ ...book, id });
    });

  return unique
    .map((book, index) => ({
      id: book.id,
      title: normalizeSeriesTitle(book.title),
      originalTitle: book.title,
      volume: extractVolume(book.title),
      author: book.author,
      cover: book.cover,
      url: book.url,
      description: book.description,
      genreTag: book.genreTag || "",
      rating: pickNumber(book.rating),
      reviewCount: pickNumber(book.reviewCount),
      rank: Number(book.rawRank) || index + 1,
      categoryId: category.id,
      categoryName: category.name,
      scrapedAt,
      favorite: false,
      status: STATUS_WISHLIST,
      note: ""
    }))
    .sort((a, b) => a.rank - b.rank);
}

function parseBooksFromHtml(html, category) {
  const $ = cheerio.load(html || "");
  if (category.period === "author") {
    const authorBooks = parseAuthorCards($, category);
    if (authorBooks.length) return authorBooks;
  }
  const nextDataBooks = parseNextData($, category);
  const cardBooks = parseCards($, category);
  return cardBooks.length >= nextDataBooks.length ? cardBooks : nextDataBooks;
}

function cleanTag(value) {
  const tag = normalizeText(value).replace(/^#/, "").trim();
  if (!tag || tag.length > 24) return "";
  if (/https?:|ridibooks|^\d+$|[{}[\]]|ebook|\uC804\uC790\uCC45|\uC18C\uC124\s*e\uBD81/i.test(tag)) return "";
  if (/(\uB9AC\uBDF0|\uD3C9\uC810|\uAD6C\uB9E4|\uB300\uC5EC|\uC18C\uC7A5|\uAC00\uACA9|\uCD9C\uAC04|\uD398\uC774\uC9C0|\uBCC4\uC810|\uD560\uC778|\uC6D0\uC774\uC0C1|\uB9CC\uC6D0|\uAD8C\uC774\uC0C1|\uC778\uC2A4\uD0C0\uADF8\uB7A8|\uBB34\uB8CC\uC774\uC6A9\uAD8C|\uB2E4\uB978 \uD0A4\uC6CC\uB4DC|\uBC1C\uAE09 \uAC00\uB2A5|\uC0AC\uC6A9\uAE30\uD55C)/.test(tag)) return "";
  return tag;
}

function uniqueList(values, limit = 12) {
  const seen = new Set();
  const result = [];
  values.forEach((value) => {
    const clean = cleanTag(value);
    if (!clean || seen.has(clean)) return;
    seen.add(clean);
    result.push(clean);
  });
  return result.slice(0, limit);
}

function normalizeReviewText(value) {
  return normalizeText(value)
    .replace(/\s*(\uC88B\uC544\uC694|\uCD94\uCC9C|\uC2E0\uACE0)\s*[0-9,]*\s*$/g, "")
    .trim();
}

function normalizeDetailReviews(reviews) {
  const seen = new Set();
  return reviews
    .map((review) => ({
      text: normalizeReviewText(review?.text || review?.content || ""),
      likes: pickNumber(review?.likes, review?.likeCount, review?.likeVoteCnt, review?.recommendCount, review?.helpfulCount) || 0,
      rating: pickNumber(review?.rating, review?.score),
      reviewer: normalizeText(review?.reviewer || review?.nickname || review?.userName || review?.userId || "")
    }))
    .filter((review) => review.text.length >= 8 && review.text.length <= 700)
    .filter((review) => {
      const key = review.text.slice(0, 120);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .sort((a, b) => (b.likes || 0) - (a.likes || 0))
    .slice(0, 5);
}

function collectDetailMetadata(value, detail, parentKey = "") {
  if (!value) return;
  if (Array.isArray(value)) {
    value.forEach((item) => collectDetailMetadata(item, detail, parentKey));
    return;
  }
  if (typeof value === "string" || typeof value === "number") {
    const text = String(value);
    const cover = absoluteUrl(text);
    if (cover.includes("img.ridicdn.net/cover")) detail.covers.push(cover);
    if (/tag|keyword|genre|category/i.test(parentKey)) detail.tags.push(text);
    const ratingReview = parseRatingReview(text);
    if (ratingReview.rating !== null) detail.ratings.push(ratingReview.rating);
    if (ratingReview.reviewCount !== null) detail.reviewCounts.push(ratingReview.reviewCount);
    return;
  }
  if (typeof value !== "object") return;

  const keys = Object.keys(value);
  const rating = pickNumber(value.rating, value.starRate, value.star_rate, value.averageRating, value.average_rating, value.avgRating, value.avg_rating, value.ratingAverage, value.rating_average, value.score);
  const reviewCount = pickNumber(value.reviewCount, value.review_count, value.ratingCount, value.rating_count, value.commentCount, value.comment_count, value.review_cnt);
  if (rating !== null && rating >= 0 && rating <= 5) detail.ratings.push(rating);
  if (reviewCount !== null && reviewCount >= 0) detail.reviewCounts.push(reviewCount);

  const reviewSignal = keys.some((key) => /review|comment|rating|score|like|vote|helpful|recommend/i.test(key));
  const reviewText = pickFirst(value.content || value.comment || value.body || value.review || value.reviewText || value.review_text || value.text || value.message);
  if (reviewSignal && reviewText) {
    detail.reviews.push({
      text: reviewText,
      likes: pickNumber(value.likeCount, value.like_count, value.likeVoteCnt, value.like_vote_cnt, value.voteCount, value.vote_count, value.recommendCount, value.recommend_count, value.helpfulCount, value.helpful_count),
      rating: pickNumber(value.rating, value.score, value.starRate, value.star_rate),
      reviewer: pickFirst(value.nickname || value.userName || value.user_name || value.userId || value.user_id || value.writer || value.name)
    });
  }

  Object.entries(value).forEach(([key, child]) => {
    if (/tag|keyword|genre|category/i.test(key)) {
      const picked = pickFirst(child);
      if (picked) detail.tags.push(...String(picked).split(/[,/]/));
    }
    collectDetailMetadata(child, detail, key);
  });
}

function parseDetailFromHtml(html, url) {
  const $ = cheerio.load(html || "");
  const productId = bookIdFromUrl(url);
  const detail = { covers: [], tags: [], reviews: [], ratings: [], reviewCounts: [] };

  $("meta[name='keywords']").each((_index, element) => {
    const keywords = normalizeText($(element).attr("content") || "");
    if (keywords) detail.tags.push(...keywords.split(","));
  });

  $("meta[property='og:image'], meta[name='twitter:image']").each((_index, element) => {
    const cover = absoluteUrl($(element).attr("content"));
    if (cover.includes("img.ridicdn.net/cover")) detail.covers.push(cover);
  });

  $("img").each((_index, element) => {
    const image = $(element);
    const srcset = image.attr("srcset")?.split(",")?.map((item) => item.trim().split(" ")[0]) || [];
    [image.attr("src"), image.attr("data-src"), image.attr("data-original"), ...srcset].forEach((src) => {
      const cover = absoluteUrl(src);
      if (cover.includes("img.ridicdn.net/cover")) detail.covers.push(cover);
    });
  });

  $("[class*='keyword'], [class*='Keyword'], [class*='tag'], [class*='Tag'], a[href*='keyword'], a[href*='tag']").each((_index, element) => {
    const text = normalizeText($(element).text());
    if (text) detail.tags.push(...text.split(/[,/]/));
  });

  $("#ISLANDS__Keyword button[aria-label], [id*='Keyword'] button[aria-label], [aria-label^='#']").each((_index, element) => {
    const tag = normalizeText($(element).attr("aria-label") || $(element).text());
    if (tag) detail.tags.push(tag);
  });

  $("[class*='review'], [class*='Review'], [data-testid*='review']").each((_index, element) => {
    const text = normalizeText($(element).text());
    if (text.length < 30 || text.length > 700) return;
    if (!/(\uB9AC\uBDF0|\uD3C9\uC810|\uC88B\uC544\uC694|\uCD94\uCC9C|\uAD6C\uB9E4\uC790)/.test(text)) return;
    const likeMatch = text.match(/(?:\uC88B\uC544\uC694|\uCD94\uCC9C|\uB3C4\uC6C0)\s*([0-9,]+)/);
    detail.reviews.push({ text, likes: pickNumber(likeMatch?.[1]) || 0 });
  });

  const rawNext = $("#__NEXT_DATA__").text();
  if (rawNext) {
    try {
      collectDetailMetadata(JSON.parse(rawNext), detail);
    } catch {
      // Detail enrichment is optional; keep the search result if page data changes.
    }
  }

  $("script[type='application/json']").each((_index, element) => {
    const raw = $(element).text();
    if (!raw || !/reviews|keyword|Keyword|ratingSummary|cover/i.test(raw)) return;
    try {
      collectDetailMetadata(JSON.parse(raw), detail);
    } catch {
      // Ignore non-JSON snippets.
    }
  });

  const exactCover = detail.covers.find((cover) => productId && cover.includes(`/cover/${productId}/`)) || "";
  const fallbackCover = detail.covers.find(Boolean) || "";
  const rating = detail.ratings.find((value) => value > 0) ?? null;
  const reviewCount = detail.reviewCounts.sort((a, b) => b - a)[0] ?? null;
  return {
    cover: normalizeCover(exactCover || fallbackCover || coverFromProductId(productId)),
    tags: uniqueList(detail.tags),
    topReviews: normalizeDetailReviews(detail.reviews),
    rating,
    reviewCount
  };
}

async function fetchInteractiveDetailHtml(url) {
  const win = new BrowserWindow({
    width: 1280,
    height: 1100,
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  });

  try {
    win.webContents.setUserAgent(USER_AGENT);
    await loadWithRetry(win, url, { userAgent: USER_AGENT, extraHeaders: LOAD_HEADERS }, 2, 9000);
    await delay(1800);
    await win.webContents.executeJavaScript(`(() => {
      const groups = [...document.querySelectorAll("div, section, nav")]
        .map((node) => [...node.querySelectorAll(":scope > button")])
        .filter((buttons) => buttons.length >= 4 && buttons.slice(0, 4).some((button) => button.getAttribute("aria-pressed") === "true"));
      const sortButtons = groups.find((buttons) => buttons.slice(0, 4).every((button) => button.hasAttribute("aria-pressed")));
      if (sortButtons && sortButtons[1]) sortButtons[1].click();
    })()`, true);
    await delay(2200);
    return win.webContents.executeJavaScript("document.documentElement ? document.documentElement.outerHTML : ''", true);
  } finally {
    if (!win.isDestroyed()) win.destroy();
  }
}

async function fetchDetailHtml(url) {
  const requestFetch = typeof net?.fetch === "function" ? net.fetch.bind(net) : fetch;
  const response = await requestFetch(url, {
    headers: DETAIL_HEADERS,
    redirect: "follow"
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.text();
}

function pageUrl(url, page) {
  try {
    const parsed = new URL(url);
    parsed.searchParams.set("page", String(page));
    return parsed.toString();
  } catch {
    return url;
  }
}

async function scrapeCategoryPage(win, category, url, expectedCount) {
  await loadWithRetry(win, url, { userAgent: USER_AGENT, extraHeaders: LOAD_HEADERS });

  let lastText = "";
  let bestBooks = [];
  for (let attempt = 0; attempt < 12; attempt += 1) {
    await delay(attempt === 0 ? 2400 : 950);
    const snapshot = await win.webContents.executeJavaScript(`({
      title: document.title,
      text: document.body ? document.body.innerText.slice(0, 1600) : "",
      html: document.documentElement ? document.documentElement.outerHTML : ""
    })`, true);

    lastText = normalizeText(`${snapshot.title} ${snapshot.text}`);
    const books = parseBooksFromHtml(snapshot.html, category);
    if (books.length > bestBooks.length) bestBooks = books;
    if (books.length >= expectedCount) return books.slice(0, expectedCount);

    if (/403|Forbidden|Access Denied/i.test(lastText)) {
      throw new Error("\uB9AC\uB514\uAC00 \uC790\uB3D9 \uC218\uC9D1 \uC694\uCCAD\uC744 \uCC28\uB2E8\uD588\uC2B5\uB2C8\uB2E4. \uC571\uC744 \uB2E4\uC2DC \uC2E4\uD589\uD558\uAC70\uB098 \uC7A0\uC2DC \uB4A4 \uC7AC\uC2DC\uB3C4\uD574\uC8FC\uC138\uC694.");
    }
  }

  if (bestBooks.length) return bestBooks.slice(0, expectedCount);
  throw new Error(`\uB3C4\uC11C \uBAA9\uB85D\uC744 \uCC3E\uC9C0 \uBABB\uD588\uC2B5\uB2C8\uB2E4. \uD398\uC774\uC9C0 \uAD6C\uC870\uAC00 \uBC14\uB00C\uC5C8\uC744 \uC218 \uC788\uC2B5\uB2C8\uB2E4. (${lastText.slice(0, 80)})`);
}

async function enrichBooksWithDetails(books, options = {}) {
  const list = Array.isArray(books) ? books : [];
  const limit = Math.min(Number(options.limit || list.length), list.length);
  if (!limit) return list;

  const enriched = [];
  for (let index = 0; index < list.length; index += 1) {
    const book = list[index];
    if (index >= limit || !book?.url) {
      enriched.push(book);
      continue;
    }

    try {
      const html = options.interactiveReviews
        ? await Promise.race([
          fetchInteractiveDetailHtml(book.url),
          delay(9000).then(() => fetchDetailHtml(book.url))
        ])
        : await fetchDetailHtml(book.url);
      const detail = parseDetailFromHtml(html, book.url);
        enriched.push({
          ...book,
          cover: detail.cover || book.cover,
          coverTrusted: Boolean(detail.cover),
          tags: detail.tags,
          topReviews: detail.topReviews,
          rating: detail.rating ?? book.rating,
          reviewCount: detail.reviewCount ?? book.reviewCount,
          detailSyncedAt: new Date().toISOString()
        });
      } catch {
        enriched.push(book);
      }
      if (typeof options.onProgress === "function") {
        options.onProgress({ current: Math.min(index + 1, limit), total: limit, title: book.title || "" });
      }
    await delay(120);
  }
  return enriched;
}

async function scrapeCategory(category, options = {}) {
  if (!category?.url) {
    throw new Error("\uCE74\uD14C\uACE0\uB9AC URL\uC774 \uC5C6\uC2B5\uB2C8\uB2E4.");
  }

  const win = new BrowserWindow({
    width: 1280,
    height: 1100,
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  });

  try {
    win.webContents.setUserAgent(USER_AGENT);
    await loadWithRetry(win, "https://ridibooks.com", { userAgent: USER_AGENT, extraHeaders: LOAD_HEADERS }, 2, 9000);
    await delay(900);

    const expectedCount = Number(category.expectedCount || 200);
    const maxPages = Math.max(1, Math.ceil(expectedCount / 50));
    const merged = new Map();

    for (let page = 1; page <= maxPages && merged.size < expectedCount; page += 1) {
      if (typeof options.onProgress === "function") {
        options.onProgress({ page, pages: maxPages, found: merged.size });
      }
      const books = await scrapeCategoryPage(win, category, pageUrl(category.url, page), 50);
      if (!books.length) break;
      books.forEach((book) => {
        if (!merged.has(book.id)) {
          merged.set(book.id, {
            ...book,
            rank: merged.size + 1
          });
        }
      });
      if (typeof options.onProgress === "function") {
        options.onProgress({ page, pages: maxPages, found: merged.size, pageCount: books.length });
      }
      if (books.length < 45) break;
    }

    return [...merged.values()].slice(0, expectedCount);
  } finally {
    if (!win.isDestroyed()) win.destroy();
  }
}

module.exports = {
  parseBooksFromHtml,
  parseDetailFromHtml,
  enrichBooksWithDetails,
  scrapeCategory
};






















