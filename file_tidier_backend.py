from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from pathlib import PurePosixPath

from file_tidier_core import (
    BOOK_EXTENSIONS,
    CHUNK_SIZE,
    FileRecord,
    SkippedRecord,
    ZIP_EXTENSIONS,
    apply_rename_plan,
    book_volume_signature,
    decode_zip_member_name,
    extension_text,
    extract_author_from_stem,
    catalog_records_from_files,
    normalize_rename_title_format,
    choose_keep_candidate,
    filter_title_groups,
    format_size,
    generate_rename_plan,
    group_by_content,
    group_by_size,
    group_title_records,
    hash_file,
    iter_files,
    normalize_book_title,
    normalize_series_title,
    title_records_from_files,
)


TEXT_EXTENSIONS = {".txt", ".html", ".htm", ".xhtml", ".xml", ".epub"}
ARCHIVE_EXTENSIONS = {".zip", ".cbz", ".epub"}
ZIP_HEALTH_SECONDS = 0.25
ZIP_HEALTH_BYTES = 256 * 1024
ZIP_HEALTH_ENTRIES = 2
THUMBNAIL_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".svg"}
THUMBNAIL_DIR = Path(__file__).resolve().parent / "electron-app" / "renderer" / ".thumb-cache"
WEB_COVER_MAX_BYTES = 4 * 1024 * 1024
COVER_CAPABLE_EXTENSIONS = {".epub", ".zip", ".cbz"}
COMPREHENSIVE_TEXT_SOURCE_LIMIT = 12000
ZIP_INDEX_CACHE_VERSION = 1
FILE_HASH_CACHE_VERSION = 1
TEXT_FINGERPRINT_CACHE_VERSION = 1
ZIP_TEXT_CACHE_VERSION = 1
CACHE_COMMIT_EVERY = 500
ZIP_INDEX_CACHE_ENV = "FILE_TIDIER_CACHE_DIR"


def index_cache_path() -> Path:
    configured = os.environ.get(ZIP_INDEX_CACHE_ENV, "").strip()
    if configured:
        cache_dir = Path(configured)
    else:
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        cache_dir = Path(local_app_data) / "File Tidier" if local_app_data else Path(tempfile.gettempdir()) / "File Tidier"
    return cache_dir / "zip-index.sqlite3"


def open_index_cache() -> sqlite3.Connection | None:
    """스캔 결과를 재사용하기 위한 sqlite 캐시를 연다.

    zip 내부 목록(zip_index), 파일 해시(file_hash), 본문 지문(text_fingerprint,
    zip_text_index)이 한 파일에 같이 들어간다. 열지 못하면 None 을 돌려주고
    호출자는 캐시 없이 그냥 동작한다.
    """
    try:
        cache_path = index_cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(cache_path, timeout=5)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS zip_index (
                path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                cache_version INTEGER NOT NULL,
                payload TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS file_hash (
                path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                cache_version INTEGER NOT NULL,
                hash TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS text_fingerprint (
                path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                cache_version INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                sentence_count INTEGER NOT NULL,
                preview TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS zip_text_index (
                path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                cache_version INTEGER NOT NULL,
                payload TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        connection.commit()
        return connection
    except (OSError, sqlite3.Error):
        return None


def close_index_cache(connection: sqlite3.Connection | None) -> None:
    if connection is None:
        return
    try:
        connection.commit()
        connection.close()
    except sqlite3.Error:
        pass


def load_cached_zip_index(
    connection: sqlite3.Connection | None,
    path: Path,
    size: int,
    mtime_ns: int,
) -> dict | None:
    if connection is None:
        return None
    try:
        row = connection.execute(
            """
            SELECT payload
            FROM zip_index
            WHERE path = ? AND size = ? AND mtime_ns = ? AND cache_version = ?
            """,
            (str(path), size, mtime_ns, ZIP_INDEX_CACHE_VERSION),
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row[0])
        if not isinstance(payload, dict) or not isinstance(payload.get("members"), list):
            return None
        return payload
    except (json.JSONDecodeError, sqlite3.Error, TypeError):
        return None


def save_cached_zip_index(
    connection: sqlite3.Connection | None,
    path: Path,
    size: int,
    mtime_ns: int,
    members: list[dict],
    errors: list[str],
) -> bool:
    if connection is None:
        return False
    payload = {
        "members": [
            {
                "innerName": member["innerName"],
                "name": member["name"],
                "extension": member["extension"],
                "size": member["size"],
                "hash": member["hash"],
            }
            for member in members
        ],
        "errors": list(errors),
    }
    try:
        connection.execute(
            """
            INSERT INTO zip_index(path, size, mtime_ns, cache_version, payload, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                size = excluded.size,
                mtime_ns = excluded.mtime_ns,
                cache_version = excluded.cache_version,
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (
                str(path),
                size,
                mtime_ns,
                ZIP_INDEX_CACHE_VERSION,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                int(time.time()),
            ),
        )
        return True
    except (OSError, sqlite3.Error, TypeError, KeyError):
        return False


class FileHashCache:
    """파일 sha256 을 (경로·크기·수정시각) 기준으로 기억해 두는 캐시.

    같은 폴더를 다시 스캔할 때 디스크를 다시 읽지 않게 하는 것이 목적이다.
    CACHE_COMMIT_EVERY 건마다 커밋하므로 스캔이 중간에 끊겨도 그때까지
    읽어 둔 해시는 남는다. connection 의 수명은 호출자가 관리한다.
    """

    def __init__(self, connection: sqlite3.Connection | None) -> None:
        self.connection = connection
        self.hits = 0
        self.misses = 0
        self._pending = 0

    @property
    def enabled(self) -> bool:
        return self.connection is not None

    def lookup(self, path: Path, size: int, mtime_ns: int) -> str:
        if self.connection is None:
            return ""
        try:
            row = self.connection.execute(
                """
                SELECT hash
                FROM file_hash
                WHERE path = ? AND size = ? AND mtime_ns = ? AND cache_version = ?
                """,
                (str(path), size, mtime_ns, FILE_HASH_CACHE_VERSION),
            ).fetchone()
        except sqlite3.Error:
            return ""
        return str(row[0]) if row else ""

    def remember(self, path: Path, size: int, mtime_ns: int, digest: str) -> None:
        if self.connection is None or not digest:
            return
        try:
            self.connection.execute(
                """
                INSERT INTO file_hash(path, size, mtime_ns, cache_version, hash, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    size = excluded.size,
                    mtime_ns = excluded.mtime_ns,
                    cache_version = excluded.cache_version,
                    hash = excluded.hash,
                    updated_at = excluded.updated_at
                """,
                (str(path), size, mtime_ns, FILE_HASH_CACHE_VERSION, digest, int(time.time())),
            )
        except sqlite3.Error:
            return
        self._pending += 1
        if self._pending >= CACHE_COMMIT_EVERY:
            self.flush()

    def flush(self) -> None:
        if self.connection is None or not self._pending:
            return
        try:
            self.connection.commit()
        except sqlite3.Error:
            pass
        self._pending = 0


def make_hash_provider(cache: FileHashCache, cancel_event=None):
    """group_by_content 에 넘길 캐시 우선 해시 함수를 만든다."""

    def provider(record: FileRecord) -> str:
        cached = cache.lookup(record.path, record.size, record.mtime_ns)
        if cached:
            cache.hits += 1
            return cached
        cache.misses += 1
        digest = hash_file(record.path, cancel_event)
        cache.remember(record.path, record.size, record.mtime_ns, digest)
        return digest

    return provider


def load_cached_text_fingerprint(
    connection: sqlite3.Connection | None,
    path: Path,
    size: int,
    mtime_ns: int,
) -> tuple[str, int, str] | None:
    if connection is None:
        return None
    try:
        row = connection.execute(
            """
            SELECT fingerprint, sentence_count, preview
            FROM text_fingerprint
            WHERE path = ? AND size = ? AND mtime_ns = ? AND cache_version = ?
            """,
            (str(path), size, mtime_ns, TEXT_FINGERPRINT_CACHE_VERSION),
        ).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return str(row[0]), int(row[1]), str(row[2])


def save_cached_text_fingerprint(
    connection: sqlite3.Connection | None,
    path: Path,
    size: int,
    mtime_ns: int,
    fingerprint: str,
    sentence_count: int,
    preview: str,
) -> None:
    if connection is None or not fingerprint:
        return
    try:
        connection.execute(
            """
            INSERT INTO text_fingerprint(
                path, size, mtime_ns, cache_version, fingerprint, sentence_count, preview, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                size = excluded.size,
                mtime_ns = excluded.mtime_ns,
                cache_version = excluded.cache_version,
                fingerprint = excluded.fingerprint,
                sentence_count = excluded.sentence_count,
                preview = excluded.preview,
                updated_at = excluded.updated_at
            """,
            (
                str(path),
                size,
                mtime_ns,
                TEXT_FINGERPRINT_CACHE_VERSION,
                fingerprint,
                sentence_count,
                preview,
                int(time.time()),
            ),
        )
    except sqlite3.Error:
        return


def load_cached_zip_text(
    connection: sqlite3.Connection | None,
    path: Path,
    size: int,
    mtime_ns: int,
) -> list[dict] | None:
    if connection is None:
        return None
    try:
        row = connection.execute(
            """
            SELECT payload
            FROM zip_text_index
            WHERE path = ? AND size = ? AND mtime_ns = ? AND cache_version = ?
            """,
            (str(path), size, mtime_ns, ZIP_TEXT_CACHE_VERSION),
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row[0])
    except (json.JSONDecodeError, sqlite3.Error, TypeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        return None
    return payload["entries"]


def save_cached_zip_text(
    connection: sqlite3.Connection | None,
    path: Path,
    size: int,
    mtime_ns: int,
    entries: list[dict],
) -> None:
    if connection is None:
        return
    try:
        connection.execute(
            """
            INSERT INTO zip_text_index(path, size, mtime_ns, cache_version, payload, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                size = excluded.size,
                mtime_ns = excluded.mtime_ns,
                cache_version = excluded.cache_version,
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (
                str(path),
                size,
                mtime_ns,
                ZIP_TEXT_CACHE_VERSION,
                json.dumps({"entries": entries}, ensure_ascii=False, separators=(",", ":")),
                int(time.time()),
            ),
        )
    except (sqlite3.Error, TypeError, ValueError):
        return


def item_volume_signature(item) -> str:
    if isinstance(item, FileRecord):
        return book_volume_signature(item.path.name)
    if isinstance(item, dict):
        return book_volume_signature(str(item.get("name") or item.get("title") or ""))
    return book_volume_signature(str(item))


def partition_conflicting_volumes(items: list) -> list[list]:
    """Keep byte/text matches from bridging explicitly different book volumes."""
    by_signature: dict[str, list] = {}
    unmarked: list = []
    for item in items:
        signature = item_volume_signature(item)
        if signature:
            by_signature.setdefault(signature, []).append(item)
        else:
            unmarked.append(item)
    if len(by_signature) <= 1:
        return [items]
    partitions = list(by_signature.values())
    if unmarked:
        partitions.append(unmarked)
    return partitions


def write_json(payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8", errors="replace")
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.write(b"\n")


def write_progress(step: str, current: int = 0, total: int = 0, detail: str = "") -> None:
    payload = {
        "kind": "progress",
        "step": step,
        "current": current,
        "total": total,
        "detail": detail,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8", errors="replace")
    sys.stderr.buffer.write(data)
    sys.stderr.buffer.write(b"\n")
    sys.stderr.flush()


def smart_meta_from_name(filename: str) -> dict:
    stem = Path(filename).stem
    normalized = normalize_rename_title_format(re.sub(r"\s+", " ", stem).strip())
    writer = ""
    tags: list[str] = []
    episode_count = 0
    is_complete = False

    brackets = re.findall(r"\[([^\]]+)\]", normalized)
    if brackets:
        writer = brackets[0].strip()
        for tag_text in brackets[1:]:
            tags.extend([tag.strip() for tag in re.split(r"[,/]", tag_text) if tag.strip()])
    trailing_bracket = re.search(r"\[([^\]]{1,40})\]\s*$", normalized)
    if trailing_bracket and looks_like_author(trailing_bracket.group(1)):
        writer = trailing_bracket.group(1).strip()
        normalized = normalized[: trailing_bracket.start()].strip()

    extracted_writer, cleaned_stem = extract_author_from_stem(normalized)
    if extracted_writer:
        writer = extracted_writer
        normalized = cleaned_stem
    dash_author = re.match(r"^(?P<title>.+?)\s+-\s*(?P<author>[^\-@\[\](){}]{1,30})$", normalized)
    if dash_author and looks_like_author(dash_author.group("author")):
        writer = dash_author.group("author").strip()
        normalized = dash_author.group("title").strip()

    episode_match = re.search(r"(\d{1,4})\s*(?:화|권|권째|부|완|完|t|T)?", normalized)
    if episode_match:
        episode_count = int(episode_match.group(1))
    elif "단편" in normalized:
        episode_count = 1

    is_complete = any(token in normalized for token in ("완결", "[완]", "(완)", " 完", "완 ", "외전"))
    return {
        "writer": writer,
        "episodeCount": episode_count,
        "isComplete": is_complete,
        "tags": tags,
        "cleanStem": normalized,
}


def looks_like_author(value: str) -> bool:
    text = re.sub(r"\s+", " ", value or "").strip().strip("[](){}")
    if not text or len(text) > 30:
        return False
    if re.search(r"\d{2,}|권|화|완결|외전|목차|텍본|개정|본편", text):
        return False
    return bool(re.search(r"[A-Za-z가-힣ぁ-ゟァ-ヿ一-龯]", text))


def looks_suspicious_title(value: str) -> bool:
    text = re.sub(r"\s+", "", value or "")
    if not text:
        return True
    meaningful = re.findall(r"[A-Za-z0-9가-힣ぁ-ゟァ-ヿ一-龯]", text)
    if len(text) <= 4 and len(meaningful) <= 2:
        return True
    return len(meaningful) / max(1, len(text)) < 0.45


def choose_display_title(file_title: str, epub_title: str) -> str:
    clean_file_title = normalize_book_title(file_title)
    clean_epub_title = clean_display_text(epub_title)
    if clean_epub_title and not looks_suspicious_title(clean_epub_title):
        return clean_epub_title
    return clean_file_title


def thumbnail_cache_path(source: Path, suffix: str, extra: str = "") -> Path:
    try:
        stat = source.stat()
        key = f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{extra}"
    except OSError:
        key = f"{source}|{extra}"
    digest = hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:16]
    suffix = suffix.lower() if suffix.lower() in THUMBNAIL_EXTENSIONS else ".jpg"
    return THUMBNAIL_DIR / f"thumb_{digest}{suffix}"


def write_thumbnail(source_zip: zipfile.ZipFile, entry_name: str, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with source_zip.open(entry_name) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output, length=64 * 1024)
    return str(target)


def extract_epub_meta_and_cover(epub_path: Path) -> dict:
    result = {
        "thumbnail": "",
        "thumbnailName": "",
        "thumbnailSize": 0,
        "epubTitle": "",
        "epubCreator": "",
        "epubSource": "unknown",
    }
    try:
        with zipfile.ZipFile(epub_path) as archive:
            names = archive.namelist()
            opf_path = ""
            if "META-INF/container.xml" in names:
                container = archive.read("META-INF/container.xml").decode("utf-8", errors="ignore")
                match = re.search(r'full-path="([^"]+)"', container)
                if match:
                    opf_path = urllib.parse.unquote(match.group(1))
            if not opf_path:
                opf_path = next((name for name in names if name.lower().endswith(".opf")), "")

            cover_href = ""
            opf_dir = ""
            if opf_path and opf_path in names:
                opf_dir = str(PurePosixPath(opf_path).parent)
                if opf_dir == ".":
                    opf_dir = ""
                try:
                    root = ET.fromstring(archive.read(opf_path))
                    result["epubSource"] = identify_epub_source_from_root(root)
                    for elem in root.iter():
                        tag = elem.tag.lower()
                        if tag.endswith("title") and elem.text and not result["epubTitle"]:
                            result["epubTitle"] = clean_display_text(elem.text)
                        elif tag.endswith("creator") and elem.text and not result["epubCreator"]:
                            result["epubCreator"] = clean_display_text(elem.text)
                        elif tag.endswith("item"):
                            properties = elem.attrib.get("properties", "").lower()
                            media_type = elem.attrib.get("media-type", "").lower()
                            href = elem.attrib.get("href", "")
                            item_id = elem.attrib.get("id", "").lower()
                            if href and (
                                "cover-image" in properties
                                or ("image" in media_type and any(token in f"{href} {item_id}".lower() for token in ("cover", "thumb", "front", "title")))
                            ):
                                cover_href = href
                                break
                    if not cover_href:
                        cover_id = ""
                        for elem in root.iter():
                            if elem.tag.lower().endswith("meta") and elem.attrib.get("name") == "cover":
                                cover_id = elem.attrib.get("content", "")
                                break
                        if cover_id:
                            for elem in root.iter():
                                if elem.tag.lower().endswith("item") and elem.attrib.get("id") == cover_id:
                                    cover_href = elem.attrib.get("href", "")
                                    break
                except (ET.ParseError, UnicodeError, KeyError):
                    pass

            candidates: list[str] = []
            if cover_href:
                decoded_href = urllib.parse.unquote(cover_href)
                joined = str(PurePosixPath(opf_dir) / decoded_href) if opf_dir else decoded_href
                joined = str(PurePosixPath(joined))
                candidates.extend([joined, decoded_href, cover_href])
            candidates.extend(
                name
                for name in names
                if PurePosixPath(name).suffix.lower() in THUMBNAIL_EXTENSIONS
                and "__MACOSX" not in name
                and "cover" in name.lower()
            )
            images = [
                name
                for name in names
                if PurePosixPath(name).suffix.lower() in THUMBNAIL_EXTENSIONS and "__MACOSX" not in name
            ]
            candidates.extend(sorted(images, key=lambda name: (not any(token in name.lower() for token in ("cover", "front", "title", "thumb")), name.lower())))
            for candidate in candidates:
                matched = next((name for name in names if name == candidate or name.endswith(candidate)), "")
                if not matched:
                    continue
                suffix = PurePosixPath(matched).suffix.lower()
                result["thumbnail"] = write_thumbnail(archive, matched, thumbnail_cache_path(epub_path, suffix, matched))
                result["thumbnailName"] = PurePosixPath(matched).name
                try:
                    result["thumbnailSize"] = archive.getinfo(matched).file_size
                except KeyError:
                    result["thumbnailSize"] = 0
                break
    except (OSError, RuntimeError, UnicodeError, zipfile.BadZipFile):
        return result
    return result


def identify_epub_source_from_root(root: ET.Element) -> str:
    """Best-effort display-only EPUB source hint from the old v4 logic."""
    score = 0
    publisher = ""
    has_isbn = False
    generator = ""
    for elem in root.iter():
        tag = elem.tag.lower()
        if tag.endswith("publisher") and elem.text and not publisher:
            publisher = elem.text.strip().lower()
        elif tag.endswith("identifier") and elem.text:
            if "isbn" in elem.text.lower() or re.search(r"\d{13}", elem.text):
                has_isbn = True
        elif tag.endswith("meta") and elem.attrib.get("name") == "generator":
            generator = elem.attrib.get("content", "").lower()

    if publisher:
        if any(token in publisher for token in ("출판", "미디어", "코믹스", "북스", "연재", "book", "media", "comics")):
            score += 40
        elif publisher in {"미상", "unknown"}:
            score -= 20
    else:
        score -= 30
    score += 50 if has_isbn else -20
    if any(token in generator for token in ("calibre", "easypub", "sigil", "text", "converter")):
        score -= 60
    return "personal" if score < 0 else "published"


def extract_zip_cover(zip_path: Path) -> str:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            image_names = [
                info.filename
                for info in archive.infolist()
                if not info.is_dir()
                and PurePosixPath(info.filename).suffix.lower() in THUMBNAIL_EXTENSIONS
                and "__MACOSX" not in info.filename
            ]
            if not image_names:
                return ""
            image_names.sort()
            first_image = image_names[0]
            suffix = PurePosixPath(first_image).suffix.lower()
            return write_thumbnail(archive, first_image, thumbnail_cache_path(zip_path, suffix, first_image))
    except (OSError, RuntimeError, zipfile.BadZipFile):
        return ""


def web_thumbnail_cache_path(source: Path, suffix: str, query: str) -> Path:
    try:
        stat = source.stat()
        key = f"web|{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{query}"
    except OSError:
        key = f"web|{source}|{query}"
    digest = hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:16]
    suffix = suffix.lower()
    if suffix not in THUMBNAIL_EXTENSIONS:
        suffix = ".jpg"
    return THUMBNAIL_DIR / f"web_{digest}{suffix}"


def suffix_from_url_or_type(url: str, content_type: str) -> str:
    path_suffix = PurePosixPath(urllib.parse.urlparse(url).path).suffix.lower()
    if path_suffix in THUMBNAIL_EXTENSIONS:
        return path_suffix
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    if "gif" in content_type:
        return ".gif"
    return ".jpg"


def extract_image_urls_from_search_html(text: str) -> list[str]:
    urls: list[str] = []
    patterns = [
        r'data-lazy-src="([^"]+)"',
        r'data-source="([^"]+)"',
        r'"imageUrl"\s*:\s*"([^"]+)"',
        r'"originalUrl"\s*:\s*"([^"]+)"',
        r'src="(https?://[^"]+)"',
    ]
    for pattern in patterns:
        for raw in re.findall(pattern, text):
            url = html.unescape(raw).encode("utf-8").decode("unicode_escape", errors="ignore")
            if not url.startswith("http"):
                continue
            if url not in urls:
                urls.append(url)
    return urls


def download_first_web_cover(query: str, source: Path) -> tuple[str, str]:
    encoded = urllib.parse.urlencode({"where": "image", "query": f"{query} 소설 표지"})
    search_url = f"https://search.naver.com/search.naver?{encoded}"
    request = urllib.request.Request(
        search_url,
        headers={
            "User-Agent": "Mozilla/5.0 FileTidier/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        search_html = response.read(1024 * 1024).decode("utf-8", errors="ignore")

    for image_url in extract_image_urls_from_search_html(search_html)[:20]:
        try:
            image_request = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0 FileTidier/1.0"})
            with urllib.request.urlopen(image_request, timeout=10) as image_response:
                content_type = image_response.headers.get("content-type", "")
                if not content_type.startswith("image/"):
                    continue
                data = image_response.read(WEB_COVER_MAX_BYTES + 1)
                if len(data) > WEB_COVER_MAX_BYTES or len(data) < 1024:
                    continue
                target = web_thumbnail_cache_path(source, suffix_from_url_or_type(image_url, content_type), query)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                return str(target), image_url
        except (OSError, UnicodeError, TimeoutError):
            continue
    return "", ""


def scan_web_covers(args: argparse.Namespace) -> dict:
    write_progress("웹 표지 검색 준비 중")
    allowed_extensions = allowed_extensions_from_args(args)
    records = filter_records_by_extensions(
        iter_files(Path(args.folder), args.recursive),
        allowed_extensions,
        include_zip_container=False,
    )
    skipped: list[SkippedRecord] = []
    catalog = catalog_records_from_files(
        records,
        include_zip=False,
        query=args.query,
        skipped=skipped,
    )
    max_items = max(1, args.max_items)
    candidates = []
    for record in catalog:
        if record.kind != "file" or record.extension.lower() not in {".epub", ".zip", ".cbz"}:
            continue
        item = enrich_catalog_item(asdict(record) | {"sizeText": format_size(record.size)}, with_thumbnails=False)
        title = item.get("displayTitle") or item.get("title") or item.get("name")
        author = item.get("displayAuthor") or item.get("writer") or ""
        if not title or looks_suspicious_title(title):
            continue
        candidates.append((record, " ".join(part for part in (author, title) if part).strip()))
        if len(candidates) >= max_items:
            break

    results: list[dict] = []
    for index, (record, query) in enumerate(candidates, start=1):
        write_progress("웹 표지 검색 중", index, len(candidates), query)
        try:
            thumbnail, image_url = download_first_web_cover(query, Path(record.location))
            results.append(
                {
                    "location": record.location,
                    "name": record.name,
                    "query": query,
                    "thumbnail": thumbnail,
                    "imageUrl": image_url,
                    "ok": bool(thumbnail),
                    "error": "" if thumbnail else "이미지 후보를 찾지 못했습니다.",
                }
            )
        except Exception as exc:
            results.append(
                {
                    "location": record.location,
                    "name": record.name,
                    "query": query,
                    "thumbnail": "",
                    "imageUrl": "",
                    "ok": False,
                    "error": str(exc),
                }
            )

    return {
        "ok": True,
        "items": results,
        "updated": sum(1 for item in results if item["ok"]),
        "total": len(results),
        "skipped": [asdict(item) for item in skipped],
    }


def clean_display_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def can_extract_local_thumbnail(item: dict) -> bool:
    return item.get("kind") == "file" and item.get("extension", "").lower() in COVER_CAPABLE_EXTENSIONS


def parse_allowed_extensions(value: str = "") -> set[str] | None:
    raw = str(value or "").strip()
    if not raw or raw in {"*", "all", "ALL"}:
        return None
    extensions: set[str] = set()
    for token in re.split(r"[\s,;/]+", raw):
        token = token.strip().lower()
        if not token:
            continue
        if token in {"*", "all"}:
            return None
        if token in {"zip종류", "zip류", "zip-kind", "zips"}:
            extensions.update(ZIP_EXTENSIONS)
            continue
        if not token.startswith("."):
            token = f".{token}"
        extensions.add(token)
    return extensions or None


def allowed_extensions_from_args(args: argparse.Namespace) -> set[str] | None:
    return parse_allowed_extensions(getattr(args, "allowed_extensions", ""))


def record_extension_allowed(record: FileRecord, allowed: set[str] | None, include_zip_container: bool = False) -> bool:
    if allowed is None:
        return True
    suffix = record.path.suffix.lower()
    return suffix in allowed or (include_zip_container and suffix in ZIP_EXTENSIONS)


def filter_records_by_extensions(
    records: list[FileRecord],
    allowed: set[str] | None,
    include_zip_container: bool = False,
) -> list[FileRecord]:
    if allowed is None:
        return records
    return [record for record in records if record_extension_allowed(record, allowed, include_zip_container)]


def row_extension_allowed(row: dict, allowed: set[str] | None) -> bool:
    if allowed is None:
        return True
    return str(row.get("extension", "")).lower() in allowed


def enrich_catalog_item(item: dict, with_thumbnails: bool) -> dict:
    meta = smart_meta_from_name(item.get("name", ""))
    item.update(meta)
    item["thumbnail"] = ""
    item["displayTitle"] = normalize_book_title(meta.get("cleanStem") or item.get("title", ""))
    item["seriesTitle"] = normalize_series_title(meta.get("cleanStem") or item.get("title", ""))
    item["displayAuthor"] = meta["writer"]
    item["sourceHint"] = ""
    if not with_thumbnails or not can_extract_local_thumbnail(item):
        return item
    path = Path(item.get("location", ""))
    extension = item.get("extension", "").lower()
    if extension == ".epub":
        epub_meta = extract_epub_meta_and_cover(path)
        item["thumbnail"] = epub_meta["thumbnail"]
        item["sourceHint"] = epub_meta.get("epubSource", "")
        item["displayTitle"] = choose_display_title(item["displayTitle"], epub_meta["epubTitle"])
        if epub_meta["epubCreator"] and looks_like_author(epub_meta["epubCreator"]):
            item["displayAuthor"] = epub_meta["epubCreator"]
            item["writer"] = epub_meta["epubCreator"]
    elif extension in {".zip", ".cbz"}:
        item["thumbnail"] = extract_zip_cover(path)
    return item


def scan_catalog(args: argparse.Namespace) -> dict:
    allowed_extensions = allowed_extensions_from_args(args)
    write_progress("폴더 훑는 중")
    records = iter_files(Path(args.folder), args.recursive)
    write_progress("파일 목록 확인 중", 0, len(records), f"{len(records)}개 발견")
    skipped: list[SkippedRecord] = []
    catalog = catalog_records_from_files(
        records,
        include_zip=args.include_zip,
        query=args.query,
        skipped=skipped,
    )
    if allowed_extensions:
        catalog = [item for item in catalog if item.extension.lower() in allowed_extensions]
    limit = max(0, args.limit)
    visible = catalog[:limit] if limit else catalog
    thumbnail_limit = max(0, getattr(args, "thumbnail_limit", 0))
    items = []
    thumbnail_count = 0
    for item in visible:
        row = asdict(item) | {"sizeText": format_size(item.size)}
        should_extract_thumbnail = False
        if args.with_thumbnails and can_extract_local_thumbnail(row):
            should_extract_thumbnail = thumbnail_limit == 0 or thumbnail_count < thumbnail_limit
            if should_extract_thumbnail:
                thumbnail_count += 1
        items.append(enrich_catalog_item(row, should_extract_thumbnail))
    return {
        "ok": True,
        "total": len(catalog),
        "shown": len(visible),
        "thumbnailTried": thumbnail_count,
        "items": items,
        "skipped": [asdict(item) for item in skipped],
    }


def scan_titles(args: argparse.Namespace) -> dict:
    allowed_extensions = allowed_extensions_from_args(args)
    write_progress("폴더 훑는 중")
    records = iter_files(Path(args.folder), args.recursive)
    write_progress("제목 추출 중", 0, len(records), f"{len(records)}개 발견")
    skipped: list[SkippedRecord] = []
    titles = title_records_from_files(records, include_zip=args.include_zip, skipped=skipped)
    write_progress("제목 그룹 정리 중", len(records), len(records), f"{len(titles)}개 제목")
    groups = filter_title_groups(group_title_records(titles), args.query)
    limit = max(0, args.limit)
    rows = []
    for group_index, items in enumerate(sorted(groups.values(), key=lambda group: group[0].title.casefold()), start=1):
        keep = choose_keep_candidate(items)
        for item in sorted(items, key=lambda candidate: (candidate.location != keep.location, candidate.location.casefold())):
            rows.append(
                {
                    **asdict(item),
                    "group": group_index,
                    "keep": item.location == keep.location,
                    "keepText": "남김" if item.location == keep.location else "중복",
                    "sizeText": format_size(item.size),
                }
            )
    visible = rows[:limit] if limit else rows
    return {
        "ok": True,
        "groups": len(groups),
        "total": len(rows),
        "shown": len(visible),
        "items": visible,
        "skipped": [asdict(item) for item in skipped],
    }


def decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def strip_markup(text: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return html.unescape(text)


def strip_extraction_artifacts(text: str) -> str:
    # Converted TXT/EPUB files can leak paragraph style names such as "c2".
    # Keep this analysis-only so original files and names are never changed.
    text = re.sub(r"(?im)^(\s*(?:[•\-*]\s*)?)c\d{1,4}\s+", r"\1", text)
    text = re.sub(r"(?i)(?<=[.!?。！？]\s)c\d{1,4}\s+", "", text)
    return text


def strip_sentence_artifacts(sentence: str) -> str:
    sentence = re.sub(r"(?i)^\s*(?:[•\-*]\s*)?c\d{1,4}\s+", "", sentence)
    sentence = re.sub(r"(?i)\bchapter\d+_\d+\b", " ", sentence)
    sentence = re.sub(r"\b\d+\s*권\s*\d+\s*화\b", " ", sentence)
    sentence = re.sub(r"(^|\s)\d+\.\s+(?=[A-Z가-힣■“])", r"\1", sentence)
    sentence = re.sub(r"([A-Za-z][A-Za-z ]{1,40})\d+\)", r"\1 ", sentence)
    sentence = re.sub(r"(?<=[.!?。！？])\d+\)\s*", " ", sentence)
    sentence = re.sub(r"^\s*(?:(?:\*\s*){1,8}|[•·ㆍ・]\s*)+", "", sentence)
    sentence = re.sub(r"\s*[\-–—―─━]{3,}\s*", " ", sentence)
    sentence = re.sub(r"[♥❤]\s*", "♥", sentence)
    sentence = re.sub(r"(?<=\S)\s+(?=[(\[])", "", sentence)
    sentence = re.sub(r"(?<=[0-9A-Za-z가-힣])-\s+(?=[0-9A-Za-z가-힣])", "-", sentence)
    sentence = re.sub(r"\s+", " ", sentence)
    return sentence.strip()


def normalize_invisible_format_chars(text: str) -> str:
    normalized: list[str] = []
    for char in text:
        codepoint = ord(char)
        if 0xFE00 <= codepoint <= 0xFE0F or 0xE0100 <= codepoint <= 0xE01EF:
            continue
        normalized.append(" " if unicodedata.category(char) == "Cf" else char)
    return "".join(normalized)


def normalize_text_content(text: str) -> str:
    text = strip_markup(text)
    text = strip_extraction_artifacts(text)
    text = normalize_invisible_format_chars(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalized_sentences(text: str, min_length: int = 12) -> list[str]:
    text = normalize_invisible_format_chars(text)
    sentences = [
        strip_sentence_artifacts(sentence)
        for sentence in re.split(r"(?<=[.!?。！？]|[다요죠까네음함임됨])\s+|\n+", text)
        if len(strip_sentence_artifacts(sentence)) >= min_length
    ]
    if not sentences:
        sentences = [text]
    return sorted(set(sentences))


def sentence_fingerprint(text: str) -> tuple[str, int, str]:
    unique_sentences = normalized_sentences(text)
    joined = "\n".join(unique_sentences)
    preview = " ".join(unique_sentences[:3])[:180]
    return hashlib.sha256(joined.encode("utf-8", errors="replace")).hexdigest(), len(unique_sentences), preview


def text_source_entry(meta: dict, text: str) -> dict:
    """본문을 지문으로 바꿔 담는다. 원문 텍스트는 여기서 버린다.

    예전에는 파일마다 본문 전체를 리스트에 쌓아 두고 나중에 한꺼번에
    비교했다. 수만 개짜리 폴더에서는 그것만으로 메모리가 수십 GB 로 불어나
    스왑이 터지고 시스템이 멈췄다. 지문(64자)·문장 수·미리보기 180자만
    남기면 항목당 수백 바이트로 끝난다.
    """
    fingerprint, sentence_count, preview = sentence_fingerprint(text)
    return {**meta, "fingerprint": fingerprint, "sentenceCount": sentence_count, "preview": preview}


def cached_text_source_entry(
    meta: dict,
    read_text,
    cache: sqlite3.Connection | None,
    path: Path,
    size: int,
    mtime_ns: int,
) -> dict | None:
    """지문을 캐시에서 먼저 찾고, 없을 때만 read_text() 로 파일을 읽는다."""
    cached = load_cached_text_fingerprint(cache, path, size, mtime_ns)
    if cached is not None:
        fingerprint, sentence_count, preview = cached
        return {**meta, "fingerprint": fingerprint, "sentenceCount": sentence_count, "preview": preview}
    text = read_text()
    if len(text) < 30:
        return None
    entry = text_source_entry(meta, text)
    save_cached_text_fingerprint(
        cache, path, size, mtime_ns, entry["fingerprint"], entry["sentenceCount"], entry["preview"]
    )
    return entry


def extract_epub_text(data: bytes) -> str:
    parts: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as epub:
            for info in sorted(epub.infolist(), key=lambda item: item.filename):
                if info.is_dir():
                    continue
                suffix = PurePosixPath(info.filename).suffix.lower()
                if suffix not in {".xhtml", ".html", ".htm", ".txt", ".xml"}:
                    continue
                try:
                    parts.append(decode_bytes(epub.read(info)))
                except (OSError, RuntimeError, zipfile.BadZipFile):
                    continue
    except (OSError, zipfile.BadZipFile):
        return ""
    return "\n".join(parts)


def text_from_bytes(data: bytes, extension: str) -> str:
    if extension == ".epub":
        return normalize_text_content(extract_epub_text(data))
    return normalize_text_content(decode_bytes(data))


def text_sources_from_zip(
    zip_path: Path,
    skipped: list[SkippedRecord],
    progress_prefix: str = "zip 내부 확인 중",
    allowed_locations: set[str] | None = None,
    cache: sqlite3.Connection | None = None,
    with_text: bool = False,
) -> list[dict]:
    """zip 안 본문 파일들의 지문 목록을 만든다. 본문 원문은 들고 나오지 않는다.

    같은 zip 을 통째로 훑은 적이 있으면 압축을 다시 풀지 않고 캐시에서 꺼낸다.
    일부만 보는 요청(allowed_locations)은 완전하지 않으므로 캐시에 쓰지 않는다.

    with_text=True 면 원문도 함께 담는다. 문장 단위 대조처럼 지문만으로는
    안 되는 곳에서만 쓰고, 호출자가 다 쓴 즉시 버려야 한다. 이때는 캐시를
    타지 않는다(캐시에는 지문만 들어가므로).
    """
    if with_text:
        cache = None
    try:
        stat = zip_path.stat()
        size_key, mtime_key = stat.st_size, stat.st_mtime_ns
    except OSError as exc:
        skipped.append(SkippedRecord(str(zip_path), str(exc)))
        return []

    cached_entries = load_cached_zip_text(cache, zip_path, size_key, mtime_key)
    if cached_entries is not None:
        if allowed_locations is None:
            return cached_entries
        return [entry for entry in cached_entries if entry.get("location") in allowed_locations]

    sources: list[dict] = []
    full_pass = allowed_locations is None
    try:
        with zipfile.ZipFile(zip_path) as archive:
            infos = archive.infolist()
            total = len(infos)
            for index, info in enumerate(infos, start=1):
                if index == 1 or index % 25 == 0 or index == total:
                    write_progress(progress_prefix, index, total, zip_path.name)
                if info.is_dir():
                    continue
                decoded_name = decode_zip_member_name(info.filename, info.flag_bits)
                name = PurePosixPath(decoded_name).name
                extension = extension_text(name)
                if extension not in TEXT_EXTENSIONS:
                    continue
                location = f"{zip_path} :: {decoded_name}"
                if allowed_locations is not None and location not in allowed_locations:
                    continue
                try:
                    text = text_from_bytes(archive.read(info), extension)
                except (OSError, RuntimeError, UnicodeError, zipfile.BadZipFile) as exc:
                    skipped.append(SkippedRecord(str(zip_path), f"{decoded_name}: {exc}"))
                    full_pass = False
                    continue
                if len(text) < 30:
                    continue
                entry = text_source_entry(
                    {
                        "name": name,
                        "title": normalize_book_title(name),
                        "extension": extension,
                        "size": info.file_size,
                        "sizeText": format_size(info.file_size),
                        "location": location,
                    },
                    text,
                )
                if with_text:
                    entry["text"] = text
                sources.append(entry)
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        skipped.append(SkippedRecord(str(zip_path), str(exc)))
        full_pass = False

    if full_pass:
        save_cached_zip_text(cache, zip_path, size_key, mtime_key, sources)
    return sources


def group_text_duplicates(sources: list[dict], progress_step: str = "", same_extension_only: bool = False) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    total = len(sources)
    for index, source in enumerate(sources, start=1):
        if progress_step and (index == 1 or index % 25 == 0 or index == total):
            write_progress(progress_step, index, total, source.get("name", ""))
        key = source.get("fingerprint", "")
        if not key:
            # 지문 없이 본문을 들고 온 항목이 있으면 여기서 계산한다.
            key, sentence_count, preview = sentence_fingerprint(source.get("text", ""))
            source["sentenceCount"] = sentence_count
            source["preview"] = preview
        if same_extension_only:
            key = f"{source.get('extension', '').casefold()}\0{key}"
        groups.setdefault(key, []).append(source)

    rows: list[dict] = []
    group_index = 0
    for _key, items in groups.items():
        if len(items) < 2:
            continue
        group_index += 1
        keep = max(items, key=lambda item: (item["extension"] == ".epub", item["size"]))
        preview = items[0]["preview"]
        for item in sorted(items, key=lambda candidate: (candidate["location"] != keep["location"], candidate["location"].casefold())):
            rows.append(
                {
                    "group": group_index,
                    "keep": item["location"] == keep["location"],
                    "keepText": "남김" if item["location"] == keep["location"] else "중복",
                    "name": item["name"],
                    "title": item["title"],
                    "extension": item["extension"],
                    "sizeText": item["sizeText"],
                    "sentenceCount": item["sentenceCount"],
                    "location": item["location"],
                    "preview": preview,
                }
            )
    return rows


def scan_text_duplicates(args: argparse.Namespace) -> dict:
    skipped: list[SkippedRecord] = []
    sources: list[dict] = []
    min_size = max(0, int(getattr(args, "min_size_kb", 0) * 1024))
    cache = open_index_cache()
    try:
        if args.zip_file:
            sources.extend(text_sources_from_zip(Path(args.zip_file), skipped, cache=cache))
        else:
            write_progress("폴더 훑는 중")
            records = iter_files(Path(args.folder), args.recursive)
            write_progress("본문 추출 중", 0, len(records), f"{len(records)}개 발견")
            for index, record in enumerate(records, start=1):
                if index == 1 or index % 25 == 0 or index == len(records):
                    write_progress("본문 추출 중", index, len(records), record.path.name)
                suffix = record.path.suffix.lower()
                if suffix in TEXT_EXTENSIONS - {".epub"} and record.size >= min_size:
                    try:
                        entry = cached_text_source_entry(
                            {
                                "name": record.path.name,
                                "title": normalize_book_title(record.path.name),
                                "extension": suffix,
                                "size": record.size,
                                "sizeText": format_size(record.size),
                                "location": str(record.path),
                            },
                            lambda: text_from_bytes(record.path.read_bytes(), suffix),
                            cache,
                            record.path,
                            record.size,
                            record.mtime_ns,
                        )
                    except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
                        skipped.append(SkippedRecord(str(record.path), str(exc)))
                        continue
                    if entry is not None:
                        sources.append(entry)
                if args.include_zip and suffix in {".zip", ".cbz"}:
                    sources.extend(text_sources_from_zip(record.path, skipped, cache=cache))
    finally:
        close_index_cache(cache)

    write_progress("중복 문장 비교 중", len(sources), len(sources), f"{len(sources)}개 본문")
    rows = group_text_duplicates(sources)
    if args.query:
        query = args.query.casefold()
        rows = [
            row
            for row in rows
            if query in row["name"].casefold()
            or query in row["title"].casefold()
            or query in row["extension"].casefold()
            or query == row["extension"].lstrip(".").casefold()
            or query in row["location"].casefold()
        ]
    visible = rows[: args.limit] if args.limit else rows
    group_count = len({row["group"] for row in rows})
    return {
        "ok": True,
        "groups": group_count,
        "total": len(rows),
        "shown": len(visible),
        "items": visible,
        "skipped": [asdict(item) for item in skipped],
    }


def scan_reference_sentences(args: argparse.Namespace) -> dict:
    skipped: list[SkippedRecord] = []
    reference_sources = text_sources_from_zip(
        Path(args.reference_zip), skipped, "참조 zip 읽는 중", with_text=True
    )
    write_progress("참조 문장 정리 중", 0, len(reference_sources), f"{len(reference_sources)}개 본문")
    # 문장 -> 그 문장이 나온 참조 파일 이름들. 원문은 여기서 버린다.
    reference_map: dict[str, list[str]] = {}
    for source in reference_sources:
        for sentence in normalized_sentences(source.pop("text", "")):
            reference_map.setdefault(sentence, []).append(source["name"])
    reference_sources.clear()

    if not reference_map:
        return {
            "ok": True,
            "referenceSentences": 0,
            "total": 0,
            "shown": 0,
            "items": [],
            "skipped": [asdict(item) for item in skipped],
        }

    rows: list[dict] = []
    reference_sentences = set(reference_map)

    def consider(source: dict, text: str) -> None:
        """본문 하나를 참조와 대조하고 결과 한 줄만 남긴다.

        예전에는 검사 대상 전부의 본문을 리스트에 모아 둔 뒤 대조했다.
        읽는 즉시 대조하고 원문을 버리면 메모리에 남는 건 결과 줄뿐이다.
        """
        target_sentences = set(normalized_sentences(text))
        matched = sorted(target_sentences & reference_sentences)
        if not matched:
            return
        reference_names = sorted({name for sentence in matched for name in reference_map[sentence]})
        rows.append(
            {
                "name": source["name"],
                "title": source["title"],
                "extension": source["extension"],
                "sizeText": source["sizeText"],
                "location": source["location"],
                "matchCount": len(matched),
                "sentenceCount": len(target_sentences),
                "matchRatio": round(len(matched) / max(1, len(target_sentences)) * 100, 1),
                "referenceFiles": ", ".join(reference_names[:4]),
                "preview": " ".join(matched[:3])[:220],
            }
        )

    write_progress("폴더 훑는 중")
    records = iter_files(Path(args.folder), args.recursive)
    write_progress("참조 문장 대조 중", 0, len(records), f"{len(records)}개 발견")
    for index, record in enumerate(records, start=1):
        if index == 1 or index % 25 == 0 or index == len(records):
            write_progress("참조 문장 대조 중", index, len(records), record.path.name)
        suffix = record.path.suffix.lower()
        if suffix in TEXT_EXTENSIONS:
            try:
                text = text_from_bytes(record.path.read_bytes(), suffix)
            except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
                skipped.append(SkippedRecord(str(record.path), str(exc)))
                continue
            if len(text) >= 30:
                consider(
                    {
                        "name": record.path.name,
                        "title": normalize_book_title(record.path.name),
                        "extension": suffix,
                        "size": record.size,
                        "sizeText": format_size(record.size),
                        "location": str(record.path),
                    },
                    text,
                )
            del text
        if args.include_zip and suffix in {".zip", ".cbz"}:
            for zip_source in text_sources_from_zip(record.path, skipped, with_text=True):
                consider(zip_source, zip_source.pop("text", ""))

    rows.sort(key=lambda item: (-item["matchCount"], -item["matchRatio"], item["location"].casefold()))
    if args.query:
        query = args.query.casefold()
        rows = [
            row
            for row in rows
            if query in row["name"].casefold()
            or query in row["title"].casefold()
            or query in row["extension"].casefold()
            or query == row["extension"].lstrip(".").casefold()
            or query in row["location"].casefold()
            or query in row["referenceFiles"].casefold()
        ]
    visible = rows[: args.limit] if args.limit else rows
    return {
        "ok": True,
        "referenceSentences": len(reference_sentences),
        "total": len(rows),
        "shown": len(visible),
        "items": visible,
        "skipped": [asdict(item) for item in skipped],
    }


def scan_size_duplicates(args: argparse.Namespace) -> dict:
    allowed_extensions = allowed_extensions_from_args(args)
    write_progress("폴더 훑는 중")
    records = iter_files(Path(args.folder), args.recursive)
    write_progress("크기 비교 중", 0, len(records), f"{len(records)}개 발견")
    groups = group_by_size(
        filter_records_by_extensions(records, allowed_extensions),
        max(0, int(args.min_size_kb * 1024)),
    )
    rows = []
    health_cache: dict[Path, dict] = {}
    for group_index, (_size, items) in enumerate(sorted(groups.items()), start=1):
        for item in items:
            health = health_cache.setdefault(item.path, quick_archive_health(item.path, read_sample=False))
            rows.append(
                {
                    "group": group_index,
                    "name": item.path.name,
                    "extension": item.path.suffix.lower(),
                    "size": item.size,
                    "sizeText": format_size(item.size),
                    "modified": item.modified,
                    "healthStatus": health["status"],
                    "healthDetail": health["detail"],
                    "healthBroken": health["broken"],
                    "location": str(item.path),
                }
            )
    visible = rows[: args.limit] if args.limit else rows
    return {"ok": True, "groups": len(groups), "total": len(rows), "shown": len(visible), "items": visible, "skipped": []}


def scan_content_duplicates(args: argparse.Namespace) -> dict:
    write_progress("폴더 훑는 중")
    allowed_extensions = allowed_extensions_from_args(args)
    records = filter_records_by_extensions(
        iter_files(Path(args.folder), args.recursive),
        allowed_extensions,
        include_zip_container=False,
    )
    write_progress("내용 해시 비교 중", 0, len(records), f"{len(records)}개 발견")
    read_errors: list[SkippedRecord] = []
    cache_connection = open_index_cache()
    hash_cache = FileHashCache(cache_connection)
    try:
        raw_groups = group_by_content(
            records,
            max(0, int(args.min_size_kb * 1024)),
            hash_provider=make_hash_provider(hash_cache),
            error_callback=lambda record, exc: read_errors.append(SkippedRecord(str(record.path), str(exc))),
        )
    finally:
        hash_cache.flush()
        close_index_cache(cache_connection)
    groups: dict[str, list[FileRecord]] = {}
    for file_hash, items in raw_groups.items():
        extension_groups: dict[str, list[FileRecord]] = {}
        for item in items:
            extension_groups.setdefault(item.path.suffix.lower(), []).append(item)
        for extension, same_extension_items in extension_groups.items():
            if len(same_extension_items) > 1:
                groups[f"{extension}\0{file_hash}"] = same_extension_items
    rows = []
    health_cache: dict[Path, dict] = {}
    for group_index, (group_key, items) in enumerate(sorted(groups.items()), start=1):
        file_hash = group_key.split("\0", 1)[1]
        for item in items:
            health = health_cache.setdefault(item.path, quick_archive_health(item.path, read_sample=False))
            rows.append(
                {
                    "group": group_index,
                    "name": item.path.name,
                    "extension": item.path.suffix.lower(),
                    "size": item.size,
                    "sizeText": format_size(item.size),
                    "modified": item.modified,
                    "hash": file_hash[:16],
                    "healthStatus": health["status"],
                    "healthDetail": health["detail"],
                    "healthBroken": health["broken"],
                    "location": str(item.path),
                }
            )
    visible = rows[: args.limit] if args.limit else rows
    return {
        "ok": True,
        "groups": len(groups),
        "total": len(rows),
        "shown": len(visible),
        "items": visible,
        "skipped": [asdict(item) for item in read_errors],
        "hashCacheHits": hash_cache.hits,
        "hashCacheMisses": hash_cache.misses,
    }


def hash_zip_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    with archive.open(info) as member:
        while True:
            chunk = member.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def scan_zip_internal_hashes(args: argparse.Namespace) -> dict:
    write_progress("폴더 훑는 중")
    allowed_extensions = allowed_extensions_from_args(args)
    records = filter_records_by_extensions(
        iter_files(Path(args.folder), args.recursive),
        allowed_extensions,
        include_zip_container=True,
    )
    min_size = max(0, int(args.min_size_kb * 1024))
    skipped: list[SkippedRecord] = []
    candidates: list[dict] = []
    archive_index: dict[str, dict] = {}
    zip_records = [record for record in records if record.path.suffix.lower() in ZIP_EXTENSIONS]
    zip_cache = open_index_cache()
    cache_hits = 0
    cache_misses = 0
    cache_writes = 0
    cache_enabled = zip_cache is not None
    include_external_files = args.command != "zip-internal-hashes"

    def is_zip_noise(path_text: str) -> bool:
        parts = [part.casefold() for part in PurePosixPath(path_text).parts]
        name = parts[-1] if parts else ""
        return (
            "__macosx" in parts
            or name in {".ds_store", "thumbs.db", "desktop.ini"}
            or name.startswith("._")
        )

    zip_index = 0
    for record in records:
        suffix = record.path.suffix.lower()
        if (
            include_external_files
            and suffix in BOOK_EXTENSIONS
            and suffix not in ZIP_EXTENSIONS
            and record.size >= min_size
        ):
            try:
                file_hash = hash_file(record.path)
            except OSError as exc:
                skipped.append(SkippedRecord(str(record.path), str(exc)))
                continue
            candidates.append(
                {
                    "sourceType": "file",
                    "kind": "file",
                    "name": record.path.name,
                    "title": normalize_book_title(record.path.name),
                    "extension": suffix,
                    "size": record.size,
                    "sizeText": format_size(record.size),
                    "hash": file_hash,
                    "hashShort": file_hash[:16],
                    "archive": "",
                    "innerName": "",
                    "location": str(record.path),
                    "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(record.modified)),
                }
            )
        if suffix not in ZIP_EXTENSIONS:
            continue
        zip_index += 1
        if zip_index == 1 or zip_index % 25 == 0 or zip_index == len(zip_records):
            write_progress(
                "ZIP 인덱스 확인 중",
                zip_index,
                len(zip_records),
                f"캐시 {cache_hits}개 · 새 검사 {cache_misses}개 · {record.path.name}",
            )
        archive_path = str(record.path)
        archive_index.setdefault(
            archive_path,
            {
                "path": archive_path,
                "name": record.path.name,
                "extension": suffix,
                "size": record.size,
                "sizeText": format_size(record.size),
                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(record.modified)),
                "members": [],
                "errors": [],
            },
        )
        try:
            stat = record.path.stat()
            cached_payload = load_cached_zip_index(zip_cache, record.path, record.size, stat.st_mtime_ns)
        except OSError:
            cached_payload = None
            stat = None

        if cached_payload is not None:
            cache_hits += 1
            for cached_member in cached_payload.get("members", []):
                decoded_name = str(cached_member.get("innerName", ""))
                name = str(cached_member.get("name") or PurePosixPath(decoded_name).name)
                extension = str(cached_member.get("extension") or extension_text(name))
                member_hash = str(cached_member.get("hash", ""))
                member_size = int(cached_member.get("size") or 0)
                if not decoded_name or not member_hash:
                    continue
                member = {
                    "sourceType": "zip item",
                    "kind": "zip item",
                    "name": name,
                    "title": normalize_book_title(name),
                    "extension": extension,
                    "size": member_size,
                    "sizeText": format_size(member_size),
                    "hash": member_hash,
                    "hashShort": member_hash[:16],
                    "archive": archive_path,
                    "innerName": decoded_name,
                    "location": f"{record.path} :: {decoded_name}",
                    "modified": "-",
                }
                archive_index[archive_path]["members"].append(member)
                if (
                    member_size >= min_size
                    and extension in BOOK_EXTENSIONS
                    and extension not in ZIP_EXTENSIONS
                    and (not allowed_extensions or extension in allowed_extensions)
                ):
                    candidates.append(member)
            for error in cached_payload.get("errors", []):
                error_text = str(error)
                archive_index[archive_path]["errors"].append(error_text)
                skipped.append(SkippedRecord(archive_path, error_text))
            continue

        cache_misses += 1
        try:
            with zipfile.ZipFile(record.path) as archive:
                infos = archive.infolist()
                for inner_index, info in enumerate(infos, start=1):
                    if inner_index == 1 or inner_index % 50 == 0 or inner_index == len(infos):
                        write_progress("zip 내부 항목 해시 중", inner_index, len(infos), record.path.name)
                    if info.is_dir():
                        continue
                    decoded_name = decode_zip_member_name(info.filename, info.flag_bits)
                    if is_zip_noise(decoded_name):
                        continue
                    name = PurePosixPath(decoded_name).name
                    extension = extension_text(name)
                    try:
                        member_hash = hash_zip_member(archive, info)
                    except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile) as exc:
                        skipped.append(SkippedRecord(str(record.path), f"{decoded_name}: {exc}"))
                        archive_index[archive_path]["errors"].append(f"{decoded_name}: {exc}")
                        continue
                    member = {
                        "sourceType": "zip item",
                        "kind": "zip item",
                        "name": name,
                        "title": normalize_book_title(name),
                        "extension": extension,
                        "size": info.file_size,
                        "sizeText": format_size(info.file_size),
                        "hash": member_hash,
                        "hashShort": member_hash[:16],
                        "archive": str(record.path),
                        "innerName": decoded_name,
                        "location": f"{record.path} :: {decoded_name}",
                        "modified": "-",
                    }
                    archive_index[archive_path]["members"].append(member)
                    if (
                        info.file_size >= min_size
                        and extension in BOOK_EXTENSIONS
                        and extension not in ZIP_EXTENSIONS
                        and (not allowed_extensions or extension in allowed_extensions)
                    ):
                        candidates.append(member)
        except (OSError, RuntimeError, UnicodeDecodeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            skipped.append(SkippedRecord(str(record.path), str(exc)))
            archive_index[archive_path]["errors"].append(str(exc))

        if stat is not None and save_cached_zip_index(
            zip_cache,
            record.path,
            record.size,
            stat.st_mtime_ns,
            archive_index[archive_path]["members"],
            archive_index[archive_path]["errors"],
        ):
            cache_writes += 1
            if cache_writes % 10 == 0:
                try:
                    zip_cache.commit()
                except sqlite3.Error:
                    pass

    if zip_cache is not None:
        try:
            zip_cache.commit()
            zip_cache.close()
        except sqlite3.Error:
            pass

    groups: dict[str, list[dict]] = {}
    for item in candidates:
        groups.setdefault(f"{item['extension'].casefold()}\0{item['hash']}", []).append(item)
    duplicate_groups = []
    for items in groups.values():
        for compatible_items in partition_conflicting_volumes(items):
            if len(compatible_items) > 1 and any(item["sourceType"] == "zip item" for item in compatible_items):
                duplicate_groups.append(compatible_items)

    signature_groups: dict[tuple[str, ...], list[str]] = {}
    for archive_path, archive in archive_index.items():
        signature = tuple(
            sorted(
                f"{member['extension'].casefold()}\0{member['hash']}\0{member['size']}"
                for member in archive["members"]
            )
        )
        if signature:
            signature_groups.setdefault(signature, []).append(archive_path)

    keep_archive_by_signature: dict[tuple[str, ...], str] = {}
    signature_group_by_signature: dict[tuple[str, ...], str] = {}
    duplicate_signature_index = 1
    for signature, archive_paths in signature_groups.items():
        if len(archive_paths) > 1:
            signature_group_by_signature[signature] = f"ZIP{duplicate_signature_index}"
            duplicate_signature_index += 1
            keep_archive_by_signature[signature] = sorted(
                archive_paths,
                key=lambda value: (
                    archive_index[value]["modified"],
                    archive_index[value]["name"].casefold(),
                    value.casefold(),
                ),
            )[0]

    def member_signature(member: dict) -> str:
        return f"{member['extension'].casefold()}\0{member['hash']}\0{member['size']}"

    def member_name_key(member: dict) -> str:
        return f"{PurePosixPath(member.get('innerName') or member['name']).name.casefold()}\0{member['extension'].casefold()}"

    archive_counters = {
        archive_path: Counter(member_signature(member) for member in archive["members"])
        for archive_path, archive in archive_index.items()
    }
    archives_by_signature: dict[str, set[str]] = {}
    for archive_path, signatures in archive_counters.items():
        for signature in signatures:
            archives_by_signature.setdefault(signature, set()).add(archive_path)
    archive_title_keys = {
        archive_path: re.sub(r"[^0-9a-z가-힣]+", "", normalize_book_title(archive["name"]).casefold())
        for archive_path, archive in archive_index.items()
    }
    archives_by_title: dict[str, set[str]] = {}
    for archive_path, title_key in archive_title_keys.items():
        if title_key:
            archives_by_title.setdefault(title_key, set()).add(archive_path)

    def compare_archive_members(source_path: str, target_path: str) -> dict:
        source_members = archive_index[source_path]["members"]
        target_members = archive_index[target_path]["members"]
        target_remaining = archive_counters[target_path].copy()
        matched: list[str] = []
        source_unmatched: list[dict] = []
        for member in source_members:
            signature = member_signature(member)
            if target_remaining[signature] > 0:
                target_remaining[signature] -= 1
                matched.append(member["innerName"])
            else:
                source_unmatched.append(member)

        source_remaining = archive_counters[source_path].copy()
        target_unmatched: list[dict] = []
        for member in target_members:
            signature = member_signature(member)
            if source_remaining[signature] > 0:
                source_remaining[signature] -= 1
            else:
                target_unmatched.append(member)

        target_by_name: dict[str, list[dict]] = {}
        for member in target_members:
            target_by_name.setdefault(member_name_key(member), []).append(member)
        changed_names: set[str] = set()
        changed: list[str] = []
        changed_pairs: list[dict] = []
        for member in source_unmatched:
            same_name = target_by_name.get(member_name_key(member), [])
            different = next((other for other in same_name if other["hash"] != member["hash"]), None)
            if different:
                changed_names.add(member_name_key(member))
                changed.append(
                    f"{member['innerName']} (A {member['hash'][:10]}... / B {different['hash'][:10]}...)"
                )
                changed_pairs.append(
                    {
                        "leftName": member["innerName"],
                        "rightName": different["innerName"],
                        "left": f"{source_path} :: {member['innerName']}",
                        "right": f"{target_path} :: {different['innerName']}",
                    }
                )

        only_source = [
            member["innerName"]
            for member in source_unmatched
            if member_name_key(member) not in changed_names
        ]
        only_target = [
            member["innerName"]
            for member in target_unmatched
            if member_name_key(member) not in changed_names
        ]
        source_only_members = [
            member for member in source_unmatched if member_name_key(member) not in changed_names
        ]
        target_only_members = [
            member for member in target_unmatched if member_name_key(member) not in changed_names
        ]
        compare_pairs: list[dict] = changed_pairs
        used_target_indexes: set[int] = set()
        for source_member in source_only_members:
            source_title = normalize_book_title(source_member["name"])
            source_series = normalize_series_title(source_member["name"])
            source_volume = book_volume_signature(source_member["name"])
            best: tuple[float, int, dict] | None = None
            for target_index, target_member in enumerate(target_only_members):
                if target_index in used_target_indexes or source_member["extension"] != target_member["extension"]:
                    continue
                target_volume = book_volume_signature(target_member["name"])
                if source_volume and target_volume and source_volume != target_volume:
                    continue
                target_title = normalize_book_title(target_member["name"])
                target_series = normalize_series_title(target_member["name"])
                title_score = difflib.SequenceMatcher(None, source_title.casefold(), target_title.casefold()).ratio()
                series_score = difflib.SequenceMatcher(None, source_series.casefold(), target_series.casefold()).ratio()
                score = max(title_score, series_score)
                if source_volume and target_volume and source_volume == target_volume:
                    score += 0.25
                if best is None or score > best[0]:
                    best = (score, target_index, target_member)
            if best is None or best[0] < 0.58:
                continue
            _, target_index, target_member = best
            used_target_indexes.add(target_index)
            compare_pairs.append(
                {
                    "leftName": source_member["innerName"],
                    "rightName": target_member["innerName"],
                    "left": f"{source_path} :: {source_member['innerName']}",
                    "right": f"{target_path} :: {target_member['innerName']}",
                }
            )
        return {
            "matched": matched,
            "matchedCount": len(matched),
            "onlySource": only_source,
            "onlyTarget": only_target,
            "changed": changed,
            "contentComparePairs": compare_pairs[:12],
        }

    archive_comparisons: dict[str, dict] = {}
    for source_path, source_archive in archive_index.items():
        source_count = sum(archive_counters[source_path].values())
        candidates_for_source: list[tuple[tuple, str, str, dict]] = []
        if source_count and not source_archive["errors"]:
            candidate_paths: set[str] = set()
            for signature in archive_counters[source_path]:
                candidate_paths.update(archives_by_signature.get(signature, set()))
            source_title_key = archive_title_keys.get(source_path, "")
            if source_title_key:
                candidate_paths.update(archives_by_title.get(source_title_key, set()))
            candidate_paths.discard(source_path)
            for target_path in candidate_paths:
                target_archive = archive_index[target_path]
                if target_archive["errors"]:
                    continue
                details = compare_archive_members(source_path, target_path)
                common_count = details["matchedCount"]
                target_count = sum(archive_counters[target_path].values())
                contained = common_count == source_count
                relation = "subset" if contained and target_count > source_count else "exact" if contained else "partial"
                if relation == "partial":
                    same_title = bool(source_title_key and source_title_key == archive_title_keys.get(target_path, ""))
                    source_volume = book_volume_signature(source_archive["name"])
                    target_volume = book_volume_signature(target_archive["name"])
                    if not same_title or (source_volume and target_volume and source_volume != target_volume):
                        continue
                relation_rank = {"subset": 0, "exact": 1, "partial": 2}[relation]
                difference_count = len(details["onlySource"]) + len(details["onlyTarget"]) + len(details["changed"])
                sort_key = (
                    relation_rank,
                    target_count - source_count if contained else -common_count,
                    difference_count,
                    target_path.casefold(),
                )
                candidates_for_source.append((sort_key, target_path, relation, details))
        if candidates_for_source:
            _, target_path, relation, details = min(candidates_for_source, key=lambda entry: entry[0])
            archive_comparisons[source_path] = {
                "targetPath": target_path,
                "targetName": archive_index[target_path]["name"],
                "relation": relation,
                **details,
            }

    for source_path, comparison in list(archive_comparisons.items()):
        if comparison["relation"] != "subset":
            continue
        target_path = comparison["targetPath"]
        if target_path in archive_comparisons and archive_comparisons[target_path]["relation"] in {"subset", "exact"}:
            continue
        reverse_details = compare_archive_members(target_path, source_path)
        archive_comparisons[target_path] = {
            "targetPath": source_path,
            "targetName": archive_index[source_path]["name"],
            "relation": "superset",
            **reverse_details,
        }

    comparison_parent = {archive_path: archive_path for archive_path in archive_index}

    def comparison_find(archive_path: str) -> str:
        while comparison_parent[archive_path] != archive_path:
            comparison_parent[archive_path] = comparison_parent[comparison_parent[archive_path]]
            archive_path = comparison_parent[archive_path]
        return archive_path

    def comparison_union(left: str, right: str) -> None:
        left_root = comparison_find(left)
        right_root = comparison_find(right)
        if left_root != right_root:
            comparison_parent[right_root] = left_root

    for source_path, comparison in archive_comparisons.items():
        comparison_union(source_path, comparison["targetPath"])

    comparison_components: dict[str, list[str]] = {}
    comparison_paths = set(archive_comparisons)
    comparison_paths.update(comparison["targetPath"] for comparison in archive_comparisons.values())
    for archive_path in comparison_paths:
        root = comparison_find(archive_path)
        comparison_components.setdefault(root, []).append(archive_path)
    comparison_group_by_path: dict[str, str] = {}
    for group_index, paths in enumerate(
        sorted(comparison_components.values(), key=lambda values: min(value.casefold() for value in values)),
        start=1,
    ):
        for archive_path in paths:
            comparison_group_by_path[archive_path] = f"ZIP{group_index}"

    zip_summaries: list[dict] = []
    for summary_index, (archive_path, archive) in enumerate(
        sorted(archive_index.items(), key=lambda entry: entry[0].casefold()),
        start=1,
    ):
        members = archive["members"]
        member_count = len(members)
        matched_count = 0
        external_matched_count = 0
        unmatched_examples: list[str] = []
        matched_hashes: list[str] = []
        match_examples: list[str] = []
        signature = tuple(
            sorted(
                f"{member['extension'].casefold()}\0{member['hash']}\0{member['size']}"
                for member in members
            )
        )
        keep_archive = keep_archive_by_signature.get(signature)
        zip_duplicate_group = signature_group_by_signature.get(signature, "")
        for member in members:
            same_hash_items = groups.get(f"{member['extension'].casefold()}\0{member['hash']}", [])
            other_items = [
                item
                for item in same_hash_items
                if item["sourceType"] == "file" or item.get("archive") != archive_path
            ]
            external_items = [item for item in same_hash_items if item["sourceType"] == "file"]
            if other_items:
                matched_count += 1
                matched_hashes.append(member["hash"])
                for other in other_items[:2]:
                    if other["sourceType"] == "file":
                        target_text = f"압축 안 된 파일: {other['name']}"
                    else:
                        target_archive = Path(other.get("archive", "")).name
                        target_text = f"다른 zip 내부: {target_archive} :: {other.get('innerName', other['name'])}"
                    match_examples.append(f"{member['innerName']} -> {target_text}")
            else:
                unmatched_examples.append(member["innerName"])
            if external_items:
                external_matched_count += 1

        comparison = archive_comparisons.get(archive_path, {})
        relation = comparison.get("relation", "")
        directly_contained = relation in {"exact", "subset"}
        if comparison:
            matched_count = comparison["matchedCount"]
            match_examples = [
                f"{name} -> {comparison['targetName']}"
                for name in comparison["matched"][:30]
            ]
            unmatched_examples = comparison["onlySource"]
        all_matched = member_count > 0 and not archive["errors"] and directly_contained
        externally_covered = member_count > 0 and external_matched_count == member_count
        identical_zip_keep = bool(relation == "exact" and keep_archive and keep_archive == archive_path)
        auto_select = bool(all_matched and (relation == "subset" or not identical_zip_keep))
        if archive["errors"]:
            status = "broken"
            status_text = "확인 실패"
        elif member_count == 0:
            status = "empty"
            status_text = "비교 항목 없음"
        elif relation == "subset":
            status = "subset"
            status_text = "상위 ZIP에 포함"
        elif relation == "superset":
            status = "superset"
            status_text = "보관 기준"
        elif all_matched and identical_zip_keep:
            status = "keep"
            status_text = "남김"
        elif all_matched:
            status = "all-matched"
            status_text = "전부 일치"
        elif relation == "partial" or matched_count > 0:
            status = "partial"
            status_text = "일부 일치"
        else:
            status = "unmatched"
            status_text = "일치 없음"

        zip_summaries.append(
            {
                "sourceType": "zip archive",
                "kind": "file",
                "group": f"Z{summary_index}",
                "name": archive["name"],
                "title": normalize_book_title(archive["name"]),
                "extension": archive["extension"],
                "size": archive["size"],
                "sizeText": archive["sizeText"],
                "hash": ", ".join(dict.fromkeys(matched_hashes)),
                "hashShort": ", ".join(hash_value[:12] for hash_value in dict.fromkeys(matched_hashes[:3])),
                "archive": archive_path,
                "innerName": "",
                "location": archive_path,
                "modified": archive["modified"],
                "zipStatus": status,
                "zipStatusText": status_text,
                "matchText": f"{matched_count}/{member_count}",
                "memberCount": member_count,
                "matchedCount": matched_count,
                "externalMatchedCount": external_matched_count,
                "matchedZipCount": max(0, matched_count - external_matched_count),
                "matchTargetText": " / ".join(match_examples[:3]) if match_examples else comparison.get("targetName", "-"),
                "matchTargetDetail": "\n".join(match_examples[:30]) if match_examples else comparison.get("targetName", "-"),
                "comparisonTargetName": comparison.get("targetName", ""),
                "comparisonTargetPath": comparison.get("targetPath", ""),
                "zipComparisonGroup": comparison_group_by_path.get(archive_path, ""),
                "onlyHereExamples": comparison.get("onlySource", [])[:30],
                "onlyTargetExamples": comparison.get("onlyTarget", [])[:30],
                "changedExamples": comparison.get("changed", [])[:30],
                "contentComparePairs": comparison.get("contentComparePairs", [])[:12],
                "containmentRelation": relation,
                "zipDuplicateGroup": zip_duplicate_group,
                "canChooseKeep": bool(zip_duplicate_group and relation != "subset"),
                "keepText": "남김" if identical_zip_keep else "중복",
                "autoSelect": auto_select,
                "keep": identical_zip_keep,
                "unmatchedExamples": unmatched_examples[:30],
                "errorCount": len(archive["errors"]),
            }
        )

    scanned_zip_archives = len(zip_summaries)
    zip_summaries = [row for row in zip_summaries if row["comparisonTargetPath"]]

    rows: list[dict] = []
    for group_index, items in enumerate(sorted(duplicate_groups, key=lambda group: group[0]["title"].casefold()), start=1):
        for item in sorted(items, key=lambda entry: (entry["sourceType"] != "file", entry["archive"].casefold(), entry["innerName"].casefold(), entry["location"].casefold())):
            rows.append({**item, "group": group_index})

    if allowed_extensions:
        rows = [row for row in rows if row_extension_allowed(row, allowed_extensions)]

    if args.query:
        query = args.query.casefold()
        zip_summaries = [
            row
            for row in zip_summaries
            if query in row["name"].casefold()
            or query in row["title"].casefold()
            or query in row["extension"].casefold()
            or query == row["extension"].lstrip(".").casefold()
            or query in row["location"].casefold()
            or query in row["zipStatusText"].casefold()
        ]
        rows = [
            row
            for row in rows
            if query in row["name"].casefold()
            or query in row["title"].casefold()
            or query in row["extension"].casefold()
            or query == row["extension"].lstrip(".").casefold()
            or query in row["location"].casefold()
            or query in row["archive"].casefold()
        ]
    if allowed_extensions:
        rows = [row for row in rows if row_extension_allowed(row, allowed_extensions)]

    combined_rows = zip_summaries
    visible = combined_rows[: args.limit] if args.limit else combined_rows
    return {
        "ok": True,
        "groups": len({row["zipComparisonGroup"] for row in zip_summaries}),
        "total": len(combined_rows),
        "shown": len(visible),
        "items": visible,
        "zipSummaries": zip_summaries,
        "stats": {
            "scannedFiles": len(records),
            "hashedItems": len(candidates),
            "zipRows": 0,
            "externalRows": 0,
            "scannedZipArchives": scanned_zip_archives,
            "zipArchives": len(zip_summaries),
            "zipAllMatched": sum(
                1 for row in zip_summaries if row["zipStatus"] in {"all-matched", "subset"}
            ),
            "zipExternallyCovered": sum(
                1
                for row in zip_summaries
                if row["memberCount"] > 0 and row["externalMatchedCount"] == row["memberCount"]
            ),
            "zipSelectable": sum(1 for row in zip_summaries if row["autoSelect"]),
            "zipCacheHits": cache_hits,
            "zipCacheMisses": cache_misses,
            "zipCacheWrites": cache_writes,
            "zipCacheEnabled": cache_enabled,
        },
        "skipped": [asdict(item) for item in skipped],
    }


class DuplicateUnion:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, value: str) -> None:
        self.parent.setdefault(value, value)

    def find(self, value: str) -> str:
        self.add(value)
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union_many(self, values: list[str]) -> None:
        clean_values = [value for value in values if value]
        if len(clean_values) < 2:
            for value in clean_values:
                self.add(value)
            return
        root = clean_values[0]
        self.add(root)
        for value in clean_values[1:]:
            self.parent[self.find(value)] = self.find(root)


def scan_comprehensive_duplicates(args: argparse.Namespace) -> dict:
    write_progress("폴더 훑는 중")
    allowed_extensions = allowed_extensions_from_args(args)
    records = filter_records_by_extensions(
        iter_files(Path(args.folder), args.recursive),
        allowed_extensions,
        include_zip_container=args.include_zip,
    )
    min_size = max(0, int(args.min_size_kb * 1024))
    skipped: list[SkippedRecord] = []
    union = DuplicateUnion()
    item_map: dict[str, dict] = {}
    evidence: dict[str, set[str]] = {}
    exact_locations: set[str] = set()
    text_locations: set[str] = set()
    hash_by_location: dict[str, str] = {}
    health_cache: dict[Path, dict] = {}
    text_candidate_locations: set[str] = set()
    zip_payload: dict | None = None
    read_errors: list[SkippedRecord] = []
    cache_connection = open_index_cache()
    hash_cache = FileHashCache(cache_connection)

    def add_evidence(location: str, label: str) -> None:
        evidence.setdefault(location, set()).add(label)

    def remember_item(item: dict) -> None:
        location = item.get("location", "")
        if not location:
            return
        union.add(location)
        current = item_map.get(location, {})
        merged = {**current, **item}
        item_map[location] = merged

    write_progress("제목 비교 중", 0, len(records), f"{len(records)}개 발견")
    titles = title_records_from_files(records, include_zip=args.include_zip, skipped=skipped)
    if allowed_extensions:
        titles = [item for item in titles if item.extension.lower() in allowed_extensions]
    title_groups = group_title_records(titles)
    for items in title_groups.values():
        locations = [item.location for item in items]
        union.union_many(locations)
        for item in items:
            remember_item(asdict(item) | {"sizeText": format_size(item.size)})
            add_evidence(item.location, "제목 같음")
            if item.extension.lower() in TEXT_EXTENSIONS:
                text_candidate_locations.add(item.location)

    write_progress("해시 후보 계산 중", 0, len(records), f"전체 {len(records)}개")

    def report_hash_progress(current: int, total: int, record: FileRecord) -> None:
        interval = max(1, total // 500)
        if current == 1 or current == total or current % interval == 0:
            write_progress("파일 해시 비교 중", current, total, record.path.name)

    hash_groups = group_by_content(
        records,
        min_size,
        progress_callback=report_hash_progress,
        hash_provider=make_hash_provider(hash_cache),
        error_callback=lambda record, exc: read_errors.append(SkippedRecord(str(record.path), str(exc))),
    )
    hash_group_count = 0
    for file_hash, items in hash_groups.items():
        extension_groups: dict[str, list[FileRecord]] = {}
        for item in items:
            extension_groups.setdefault(item.path.suffix.lower(), []).append(item)
        for same_extension_items in extension_groups.values():
            for compatible_items in partition_conflicting_volumes(same_extension_items):
                if len(compatible_items) < 2:
                    continue
                hash_group_count += 1
                locations = [str(item.path) for item in compatible_items]
                union.union_many(locations)
                for item in compatible_items:
                    health = health_cache.setdefault(item.path, quick_archive_health(item.path, read_sample=False))
                    location = str(item.path)
                    remember_item(
                        {
                            "name": item.path.name,
                            "title": normalize_book_title(item.path.name),
                            "extension": item.path.suffix.lower(),
                            "kind": "file",
                            "size": item.size,
                            "sizeText": format_size(item.size),
                            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(item.modified)),
                            "location": location,
                            "healthStatus": health["status"],
                            "healthDetail": health["detail"],
                            "healthBroken": health["broken"],
                        }
                    )
                    hash_by_location[location] = file_hash
                    exact_locations.add(location)
                    add_evidence(location, "해시 같음")

    write_progress("본문 비교 후보 추리기", 0, len(records), f"{len(text_candidate_locations)}개 후보")
    if args.include_zip:
        try:
            zip_payload = scan_zip_internal_hashes(args)
            zip_exact_groups: dict[str, list[dict]] = {}
            for zip_item in zip_payload.get("items", []):
                if zip_item.get("sourceType") not in {"file", "zip item"}:
                    continue
                if not zip_item.get("hash") or not zip_item.get("location"):
                    continue
                zip_exact_groups.setdefault(str(zip_item.get("group")), []).append(zip_item)
            for items in zip_exact_groups.values():
                has_real_file = any(item.get("sourceType") == "file" for item in items)
                has_zip_item = any(item.get("sourceType") == "zip item" for item in items)
                if not has_real_file or not has_zip_item:
                    continue
                locations = [item["location"] for item in items]
                union.union_many(locations)
                for item in items:
                    remember_item(item)
                    hash_by_location[item["location"]] = str(item.get("hash", ""))
                    exact_locations.add(item["location"])
                    add_evidence(item["location"], "\u007a\u0069\u0070 \ub0b4\ubd80 \ud30c\uc77c\uacfc \ud574\uc2dc \uac19\uc74c")
        except Exception as exc:
            skipped.append(SkippedRecord(str(args.folder), f"\u007a\u0069\u0070 \ub0b4\ubd80 \ud574\uc2dc \ud1b5\ud569 \uc2e4\ud328: {exc}"))

    sources: list[dict] = []
    candidate_file_locations = {location for location in text_candidate_locations if " :: " not in location}
    candidate_zip_locations = {location for location in text_candidate_locations if " :: " in location}
    candidate_zip_paths = {location.split(" :: ", 1)[0] for location in candidate_zip_locations}
    candidate_records = [
        record
        for record in records
        if str(record.path) in candidate_file_locations
        or (args.include_zip and record.path.suffix.lower() in {".zip", ".cbz"} and str(record.path) in candidate_zip_paths)
    ]
    for index, record in enumerate(candidate_records, start=1):
        if index == 1 or index % 25 == 0 or index == len(candidate_records):
            write_progress("본문 후보 읽는 중", index, len(candidate_records), record.path.name)
        if len(sources) >= COMPREHENSIVE_TEXT_SOURCE_LIMIT:
            skipped.append(SkippedRecord(str(record.path), f"종합 모드 본문 후보 {COMPREHENSIVE_TEXT_SOURCE_LIMIT}개 제한"))
            break
        extension = record.path.suffix.lower()
        if str(record.path) in candidate_file_locations and extension in TEXT_EXTENSIONS:
            try:
                entry = cached_text_source_entry(
                    {
                        "name": record.path.name,
                        "title": normalize_book_title(record.path.name),
                        "extension": extension,
                        "kind": "file",
                        "size": record.size,
                        "sizeText": format_size(record.size),
                        "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(record.modified)),
                        "location": str(record.path),
                    },
                    lambda: text_from_bytes(record.path.read_bytes(), extension),
                    cache_connection,
                    record.path,
                    record.size,
                    record.mtime_ns,
                )
            except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
                skipped.append(SkippedRecord(str(record.path), str(exc)))
                continue
            if entry is not None:
                sources.append(entry)
        if args.include_zip and extension in {".zip", ".cbz"}:
            sources.extend(
                text_sources_from_zip(
                    record.path,
                    skipped,
                    "zip 내부 후보 본문 비교 중",
                    allowed_locations=candidate_zip_locations,
                    cache=cache_connection,
                )
            )
            if len(sources) >= COMPREHENSIVE_TEXT_SOURCE_LIMIT:
                sources = sources[:COMPREHENSIVE_TEXT_SOURCE_LIMIT]
                skipped.append(SkippedRecord(str(record.path), f"종합 모드 본문 후보 {COMPREHENSIVE_TEXT_SOURCE_LIMIT}개 제한"))
                break

    # 여기까지가 디스크를 읽는 구간이다. 남은 작업은 메모리 안에서만 돈다.
    hash_cache.flush()
    close_index_cache(cache_connection)
    skipped.extend(read_errors)

    text_rows = group_text_duplicates(sources, "본문 지문 비교 중", same_extension_only=True)
    text_groups: dict[int, list[dict]] = {}
    for row in text_rows:
        text_groups.setdefault(row["group"], []).append(row)
    for items in text_groups.values():
        for compatible_items in partition_conflicting_volumes(items):
            if len(compatible_items) < 2:
                continue
            locations = [item["location"] for item in compatible_items]
            union.union_many(locations)
            for item in compatible_items:
                remember_item(item)
                text_locations.add(item["location"])
                add_evidence(item["location"], "본문 같음")

    components: dict[str, list[str]] = {}
    for location in item_map:
        components.setdefault(union.find(location), []).append(location)

    rows: list[dict] = []
    group_index = 0
    for locations in sorted(components.values(), key=lambda group: item_map[group[0]].get("title", "").casefold()):
        if len(locations) < 2:
            continue
        group_index += 1
        items = [item_map[location] for location in locations]
        group_has_exact = any(location in exact_locations for location in locations)
        group_has_real_file = any(item.get("sourceType") == "file" or item.get("kind") == "file" for item in items)
        group_has_zip_item = any(item.get("sourceType") == "zip item" or item.get("kind") == "zip item" for item in items)

        def keep_order(item: dict) -> tuple:
            modified = str(item.get("modified") or "")
            if not re.match(r"^\d{4}-\d{2}-\d{2}", modified):
                modified = "9999-12-31 23:59"
            if group_has_real_file and group_has_zip_item:
                return (
                    0 if item.get("sourceType") == "zip item" or item.get("kind") == "zip item" else 1,
                    modified,
                    str(item.get("location") or "").casefold(),
                )
            return (
                0 if item.get("kind") == "file" else 1,
                modified,
                str(item.get("location") or "").casefold(),
            )

        keep = min(items, key=keep_order)
        keep_location = keep.get("location", "")
        keep_hash = hash_by_location.get(keep_location, "")
        keep_extension = str(keep.get("extension") or "").casefold()
        for item in sorted(items, key=lambda candidate: (candidate.get("location") != keep.get("location"), str(candidate.get("location", "")).casefold())):
            location = item.get("location", "")
            item_evidence = sorted(evidence.get(location, set()))
            item_hash = hash_by_location.get(location, "")
            same_as_keep = bool(
                location != keep_location
                and keep_hash
                and item_hash == keep_hash
                and str(item.get("extension") or "").casefold() == keep_extension
            )
            if location == keep_location:
                confidence = "확실" if keep_hash else "검토"
                keep_text = "남김"
                auto_select = False
            elif same_as_keep:
                confidence = "확실"
                keep_text = "중복"
                auto_select = True
            elif location in exact_locations:
                confidence = "검토"
                keep_text = "검토"
                auto_select = False
                item_evidence.append("현재 남김 기준과 해시 다름")
            elif location in text_locations:
                confidence = "검토"
                keep_text = "검토"
                auto_select = False
            elif group_has_exact:
                confidence = "검토"
                keep_text = "검토"
                auto_select = False
            else:
                confidence = "검토"
                keep_text = "검토"
                auto_select = False
            rows.append(
                {
                    **item,
                    "group": group_index,
                    "keep": location == keep_location,
                    "keepText": keep_text,
                    "confidence": confidence,
                    "evidence": ", ".join(item_evidence) or "연결됨",
                    "hash": hash_by_location.get(location, ""),
                    "matchesKeepHash": None if not item_hash else bool(location == keep_location or same_as_keep),
                    "autoSelect": bool(auto_select and item.get("kind") == "file"),
                }
            )

    if allowed_extensions:
        rows = [row for row in rows if row_extension_allowed(row, allowed_extensions)]

    if args.query:
        query = args.query.casefold()
        rows = [
            row
            for row in rows
            if query in str(row.get("name", "")).casefold()
            or query in str(row.get("title", "")).casefold()
            or query in str(row.get("extension", "")).casefold()
            or query == str(row.get("extension", "")).lstrip(".").casefold()
            or query in str(row.get("location", "")).casefold()
            or query in str(row.get("evidence", "")).casefold()
        ]
    if args.include_zip:
        try:
            if zip_payload is None:
                zip_payload = scan_zip_internal_hashes(args)
            zip_rows = []
            zip_group_offset = group_index
            for zip_index, zip_row in enumerate(
                [item for item in zip_payload.get("items", []) if item.get("sourceType") == "zip archive"],
                start=1,
            ):
                zip_status = zip_row.get("zipStatus", "")
                if zip_status == "all-matched":
                    confidence = "확실"
                    keep_text = "중복"
                    evidence_text = "ZIP 내부 완전 동일"
                elif zip_status == "subset":
                    confidence = "확실"
                    keep_text = "중복"
                    evidence_text = "다른 ZIP에 내부 전체 포함"
                elif zip_status == "superset":
                    confidence = "확실"
                    keep_text = "남김"
                    evidence_text = "하위 ZIP의 내부 전체 포함"
                elif zip_status == "keep":
                    confidence = "확실"
                    keep_text = "남김"
                    evidence_text = "ZIP 내부 완전 동일"
                else:
                    confidence = "검토"
                    keep_text = "검토"
                    evidence_text = "zip 내부 일부 일치"
                zip_auto_select = bool(zip_row.get("autoSelect"))
                zip_rows.append(
                    {
                        **zip_row,
                        "group": zip_row.get("zipDuplicateGroup") or f"Z{zip_group_offset + zip_index}",
                        "confidence": confidence,
                        "keepText": keep_text,
                        "evidence": f"{evidence_text}: {zip_row.get('matchTargetText', '-')}",
                        "autoSelect": zip_auto_select,
                        "modified": zip_row.get("modified", "-"),
                        "healthStatus": "",
                        "healthDetail": "",
                        "healthBroken": False,
                    }
                )
            rows.extend(zip_rows)
        except Exception as exc:
            skipped.append(SkippedRecord(str(args.folder), f"ZIP 종합 중복 정리 실패: {exc}"))
    visible = rows[: args.limit] if args.limit else rows
    analysis_stats = {
        "scannedFiles": len(records),
        "titleCandidateItems": len(text_candidate_locations),
        "textCandidateFiles": len(candidate_records),
        "textSources": len(sources),
        "hashGroups": hash_group_count,
        "hashCacheHits": hash_cache.hits,
        "hashCacheMisses": hash_cache.misses,
        "hashCacheEnabled": hash_cache.enabled,
        "readErrors": len(read_errors),
        "hashMatchedRows": len(exact_locations),
        "reviewRows": sum(1 for row in rows if not row.get("hash")),
        "selectableRows": sum(1 for row in rows if row.get("autoSelect")),
        "zipItemRows": sum(1 for row in rows if " :: " in str(row.get("location", ""))),
        "zipArchiveRows": sum(1 for row in rows if row.get("sourceType") == "zip archive"),
        "zipSelectableRows": sum(1 for row in rows if row.get("sourceType") == "zip archive" and row.get("autoSelect")),
    }
    if zip_payload:
        zip_stats = zip_payload.get("stats", {})
        analysis_stats.update(
            {
                "zipCacheHits": zip_stats.get("zipCacheHits", 0),
                "zipCacheMisses": zip_stats.get("zipCacheMisses", 0),
                "zipCacheWrites": zip_stats.get("zipCacheWrites", 0),
                "zipCacheEnabled": zip_stats.get("zipCacheEnabled", False),
            }
        )
    return {
        "ok": True,
        "groups": len({row["group"] for row in rows}),
        "total": len(rows),
        "shown": len(visible),
        "items": visible,
        "analysisStats": analysis_stats,
        "skipped": [asdict(item) for item in skipped],
    }


def preview_rename(args: argparse.Namespace) -> dict:
    allowed_extensions = allowed_extensions_from_args(args)
    write_progress("폴더 훑는 중")
    records = iter_files(Path(args.folder), args.recursive)
    write_progress("이름 변경 미리보기 생성 중", 0, len(records), f"{len(records)}개 발견")
    plan = generate_rename_plan(
        filter_records_by_extensions(records, allowed_extensions),
        args.find,
        args.replace,
        args.regex,
        args.prefix,
        args.suffix,
        args.case,
        args.start_number,
        args.padding,
        True,
        args.position,
        args.author,
        args.strip_copy_suffix,
        args.auto_author,
        args.normalize_title_format,
        args.author_pattern,
    )
    all_rows = [
        {
            "old": entry.source.name,
            "new": entry.target.name,
            "extension": entry.target.suffix.lower(),
            "status": entry.status,
            "location": str(entry.source.parent),
        }
        for entry in plan
    ]
    rows = [row for row in all_rows if row["status"] != "unchanged"]
    visible = rows[: args.limit] if args.limit else rows
    ready = sum(1 for row in rows if row["status"] == "ready")
    unchanged = len(all_rows) - len(rows)
    return {
        "ok": True,
        "total": len(all_rows),
        "shown": len(visible),
        "ready": ready,
        "unchanged": unchanged,
        "items": visible,
        "skipped": [],
    }


def apply_rename(args: argparse.Namespace) -> dict:
    allowed_extensions = allowed_extensions_from_args(args)
    records = filter_records_by_extensions(
        iter_files(Path(args.folder), args.recursive),
        allowed_extensions,
        include_zip_container=args.include_zip,
    )
    plan = generate_rename_plan(
        filter_records_by_extensions(records, allowed_extensions),
        args.find,
        args.replace,
        args.regex,
        args.prefix,
        args.suffix,
        args.case,
        args.start_number,
        args.padding,
        True,
        args.position,
        args.author,
        args.strip_copy_suffix,
        args.auto_author,
        args.normalize_title_format,
        args.author_pattern,
    )
    applied, errors = apply_rename_plan(plan)
    ready = sum(1 for entry in plan if entry.status == "ready")
    return {
        "ok": not errors,
        "applied": applied,
        "ready": ready,
        "errors": errors,
        "error": "\n".join(errors[:5]) if errors else "",
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quick_archive_health(path: Path, read_sample: bool = False) -> dict:
    suffix = path.suffix.lower()
    if suffix not in ARCHIVE_EXTENSIONS:
        return {"status": "\uc77c\ubc18 \ud30c\uc77c", "detail": "", "broken": False}
    try:
        started_at = time.monotonic()
        checked_entries = 0
        checked_bytes = 0
        with zipfile.ZipFile(path) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if not read_sample:
                return {"status": "\ubaa9\ub85d \ud655\uc778 \uc815\uc0c1", "detail": f"\ub0b4\ubd80 {len(infos)}\uac1c", "broken": False}
            for info in infos[:ZIP_HEALTH_ENTRIES]:
                if time.monotonic() - started_at >= ZIP_HEALTH_SECONDS:
                    return {
                        "status": "\ube60\ub978 \ud655\uc778 \uc81c\ud55c",
                        "detail": f"\ub0b4\ubd80 {len(infos)}\uac1c, \uc0d8\ud50c {checked_entries}\uac1c",
                        "broken": False,
                    }
                try:
                    with archive.open(info) as member:
                        remaining = max(0, ZIP_HEALTH_BYTES - checked_bytes)
                        if remaining <= 0:
                            break
                        data = member.read(min(64 * 1024, remaining))
                        checked_bytes += len(data)
                        checked_entries += 1
                except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile) as exc:
                    name = decode_zip_member_name(info.filename, info.flag_bits)
                    return {"status": "\uc190\uc0c1 \uc758\uc2ec", "detail": f"{name}: {exc}", "broken": True}
        return {"status": "\ube60\ub978 \ud655\uc778 \uc815\uc0c1", "detail": f"\ub0b4\ubd80 {len(infos)}\uac1c, \uc0d8\ud50c {checked_entries}\uac1c", "broken": False}
    except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        return {"status": "\uc190\uc0c1 \uc758\uc2ec", "detail": str(exc), "broken": True}

def zip_summary(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        entries = []
        for info in archive.infolist():
            if info.is_dir():
                continue
            decoded_name = decode_zip_member_name(info.filename, info.flag_bits)
            entries.append(
                {
                    "name": decoded_name,
                    "extension": extension_text(PurePosixPath(decoded_name).name) or "(none)",
                    "size": info.file_size,
                    "compressed": info.compress_size,
                    "crc": info.CRC,
                    "date": info.date_time,
                    "method": info.compress_type,
                    "extraSize": len(info.extra or b""),
                    "commentSize": len(info.comment or b""),
                    "flagBits": info.flag_bits,
                }
            )
        archive_comment_size = len(archive.comment or b"")
    names = [entry["name"] for entry in entries]
    return {
        "count": len(entries),
        "names": set(names),
        "entries": {entry["name"]: entry for entry in entries},
        "totalSize": sum(entry["size"] for entry in entries),
        "compressedSize": sum(entry["compressed"] for entry in entries),
        "extraFieldSize": sum(entry["extraSize"] for entry in entries),
        "memberCommentSize": sum(entry["commentSize"] for entry in entries),
        "archiveCommentSize": archive_comment_size,
        "extensions": Counter(entry["extension"] for entry in entries),
        "health": quick_archive_health(path, read_sample=True),
    }


def short_hash(value: str) -> str:
    if len(value) <= 20:
        return value
    return f"{value[:8]}...{value[-8:]}"


def format_zip_date(date_time: tuple[int, int, int, int, int, int] | None) -> str:
    if not date_time:
        return "-"
    year, month, day, hour, minute, _second = date_time
    return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"


def summarize_internal_names(names: list[str], limit: int = 18) -> str:
    if not names:
        return "-"
    basenames = [PurePosixPath(name).name for name in names]
    visible = basenames[:limit]
    extra = len(basenames) - len(visible)
    text = ", ".join(visible)
    if extra > 0:
        text += f" ... (+{extra})"
    return text


def internal_category(name: str) -> str:
    lower_name = name.lower()
    suffix = extension_text(PurePosixPath(name).name)
    if suffix in {".ttf", ".otf", ".woff", ".woff2"}:
        return "폰트"
    if suffix == ".css":
        return "CSS"
    if suffix in {".ncx", ".opf"} or any(token in lower_name for token in ("toc", "nav")):
        return "목차/메타"
    if suffix in THUMBNAIL_EXTENSIONS or "cover" in lower_name:
        return "표지/이미지"
    if suffix in {".xhtml", ".html", ".htm"}:
        return "본문/안내 XHTML"
    return suffix or "기타"


def summarize_internal_categories(names: list[str]) -> str:
    if not names:
        return "-"
    counts = Counter(internal_category(name) for name in names)
    return ", ".join(f"{category} {count}개" for category, count in sorted(counts.items()))


def zip_member_hashes(path: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            decoded_name = decode_zip_member_name(info.filename, info.flag_bits)
            digest = hashlib.sha256()
            with archive.open(info) as source:
                while chunk := source.read(CHUNK_SIZE):
                    digest.update(chunk)
            hashes[decoded_name] = digest.hexdigest()
    return hashes


def epub_summary(path: Path, visible_text_length: int) -> dict:
    summary = zip_summary(path)
    cover = extract_epub_meta_and_cover(path)
    latest_date = max((entry["date"] for entry in summary["entries"].values()), default=None)
    file_hash = file_sha256(path)
    return {
        **summary,
        "fileSize": path.stat().st_size,
        "fileSizeText": format_size(path.stat().st_size),
        "visibleTextLength": visible_text_length,
        "sha256": file_hash,
        "sha256Short": short_hash(file_hash),
        "latestDate": latest_date,
        "latestDateText": format_zip_date(latest_date),
        "cover": cover,
        "memberHashes": zip_member_hashes(path),
    }


def cover_compare_item(cover: dict) -> dict:
    thumbnail = str(cover.get("thumbnail") or "")
    thumbnail_uri = ""
    if thumbnail:
        try:
            thumbnail_uri = Path(thumbnail).resolve().as_uri()
        except (OSError, ValueError):
            thumbnail_uri = ""
    size = int(cover.get("thumbnailSize") or 0)
    return {
        "name": str(cover.get("thumbnailName") or "표지 없음"),
        "size": size,
        "sizeText": format_size(size) if size else "-",
        "src": thumbnail_uri,
    }


def image_dimensions(data: bytes, suffix: str) -> str:
    """Read common raster dimensions without adding a heavyweight image dependency."""
    try:
        if suffix == ".png" and len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
            return f"{int.from_bytes(data[16:20], 'big')}x{int.from_bytes(data[20:24], 'big')}"
        if suffix == ".gif" and len(data) >= 10 and data[:6] in {b"GIF87a", b"GIF89a"}:
            return f"{int.from_bytes(data[6:8], 'little')}x{int.from_bytes(data[8:10], 'little')}"
        if suffix == ".bmp" and len(data) >= 26 and data.startswith(b"BM"):
            return f"{int.from_bytes(data[18:22], 'little')}x{abs(int.from_bytes(data[22:26], 'little', signed=True))}"
        if suffix in {".jpg", ".jpeg"} and len(data) >= 4 and data.startswith(b"\xff\xd8"):
            offset = 2
            while offset + 9 < len(data):
                if data[offset] != 0xFF:
                    offset += 1
                    continue
                marker = data[offset + 1]
                offset += 2
                if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                    continue
                if offset + 2 > len(data):
                    break
                segment_length = int.from_bytes(data[offset:offset + 2], "big")
                if segment_length < 2 or offset + segment_length > len(data):
                    break
                if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                    height = int.from_bytes(data[offset + 3:offset + 5], "big")
                    width = int.from_bytes(data[offset + 5:offset + 7], "big")
                    return f"{width}x{height}"
                offset += segment_length
        if suffix == ".svg":
            head = data[:64 * 1024].decode("utf-8", errors="ignore")
            view_box = re.search(r"viewBox\s*=\s*['\"]\s*[-.\d]+\s+[-.\d]+\s+([.\d]+)\s+([.\d]+)", head, re.I)
            if view_box:
                return f"{view_box.group(1)}x{view_box.group(2)}"
            width = re.search(r"\bwidth\s*=\s*['\"]([.\d]+)", head, re.I)
            height = re.search(r"\bheight\s*=\s*['\"]([.\d]+)", head, re.I)
            if width and height:
                return f"{width.group(1)}x{height.group(1)}"
    except (IndexError, ValueError, UnicodeError):
        return ""
    return ""


def epub_image_items(path: Path) -> list[dict]:
    items: list[dict] = []
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            decoded_name = decode_zip_member_name(info.filename, info.flag_bits)
            suffix = PurePosixPath(decoded_name).suffix.lower()
            if suffix not in THUMBNAIL_EXTENSIONS or "__MACOSX" in decoded_name:
                continue
            target = thumbnail_cache_path(path, suffix, decoded_name)
            thumbnail = write_thumbnail(archive, info.filename, target)
            try:
                thumbnail_uri = Path(thumbnail).resolve().as_uri()
            except (OSError, ValueError):
                thumbnail_uri = ""
            try:
                with archive.open(info) as source:
                    header = source.read(min(info.file_size, 512 * 1024))
                    image_hasher = hashlib.sha256()
                    image_hasher.update(header)
                    while chunk := source.read(1024 * 1024):
                        image_hasher.update(chunk)
                    image_sha256 = image_hasher.hexdigest()
            except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile):
                header = b""
                image_sha256 = ""
            items.append(
                {
                    "path": decoded_name,
                    "name": PurePosixPath(decoded_name).name,
                    "size": info.file_size,
                    "sizeText": format_size(info.file_size),
                    "compressedSize": info.compress_size,
                    "compressedSizeText": format_size(info.compress_size),
                    "crc": f"{info.CRC:08x}",
                    "sha256": image_sha256,
                    "dimensions": image_dimensions(header, suffix),
                    "src": thumbnail_uri,
                }
            )
    return items


def align_epub_images(left_items: list[dict], right_items: list[dict]) -> list[dict]:
    remaining = set(range(len(right_items)))
    rows: list[dict] = []

    def find_match(left_item: dict, matcher) -> int | None:
        return next((index for index in sorted(remaining) if matcher(left_item, right_items[index])), None)

    for left_item in left_items:
        match_index = find_match(left_item, lambda a, b: a["path"].casefold() == b["path"].casefold())
        basis = "같은 경로"
        if match_index is None:
            match_index = find_match(left_item, lambda a, b: bool(a["sha256"]) and a["sha256"] == b["sha256"])
            basis = "같은 이미지 바이트"
        if match_index is None:
            match_index = find_match(left_item, lambda a, b: a["name"].casefold() == b["name"].casefold())
            basis = "같은 파일명"
        right_item = right_items[match_index] if match_index is not None else None
        if match_index is not None:
            remaining.remove(match_index)
        if right_item is None:
            status = "A에만 있음"
        elif bool(left_item["sha256"]) and left_item["sha256"] == right_item["sha256"]:
            status = "완전 동일"
        elif left_item["size"] == right_item["size"]:
            status = "크기는 같고 내용 다름"
        else:
            status = "내용/크기 다름"
        rows.append({"left": left_item, "right": right_item, "status": status, "basis": basis})

    for index in sorted(remaining):
        rows.append({"left": None, "right": right_items[index], "status": "B에만 있음", "basis": ""})
    return rows


def epub_compare_payload(left: Path, right: Path, left_text_length: int, right_text_length: int) -> dict:
    try:
        left_summary = epub_summary(left, left_text_length)
        right_summary = epub_summary(right, right_text_length)
        left_images = epub_image_items(left)
        right_images = epub_image_items(right)
    except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        return {
            "metrics": [],
            "sections": [{"title": "EPUB 내부 분석 오류", "items": [str(exc)]}],
            "details": [f"epub internal analysis failed: {exc}"],
            "media": {},
        }

    left_names = set(left_summary["names"])
    right_names = set(right_summary["names"])
    common_names = sorted(left_names & right_names)
    left_only_names = sorted(left_names - right_names)
    right_only_names = sorted(right_names - left_names)
    changed_names = [
        name
        for name in common_names
        if left_summary["memberHashes"].get(name) != right_summary["memberHashes"].get(name)
    ]
    changed_set = set(changed_names)
    same_names = [name for name in common_names if name not in changed_set]
    same_size_different_content = [
        name
        for name in changed_names
        if left_summary["entries"][name]["size"] == right_summary["entries"][name]["size"]
    ]
    recompressed_names = [
        name
        for name in same_names
        if (
            left_summary["entries"][name]["compressed"] != right_summary["entries"][name]["compressed"]
            or left_summary["entries"][name]["method"] != right_summary["entries"][name]["method"]
        )
    ]
    metadata_changed_names = [
        name
        for name in same_names
        if (
            left_summary["entries"][name]["date"] != right_summary["entries"][name]["date"]
            or left_summary["entries"][name]["extraSize"] != right_summary["entries"][name]["extraSize"]
            or left_summary["entries"][name]["commentSize"] != right_summary["entries"][name]["commentSize"]
            or left_summary["entries"][name]["flagBits"] != right_summary["entries"][name]["flagBits"]
        )
    ]

    left_hash_paths: dict[str, list[str]] = {}
    right_hash_paths: dict[str, list[str]] = {}
    for name, digest in left_summary["memberHashes"].items():
        left_hash_paths.setdefault(digest, []).append(name)
    for name, digest in right_summary["memberHashes"].items():
        right_hash_paths.setdefault(digest, []).append(name)
    renamed_same_payload = []
    for digest in sorted(set(left_hash_paths) & set(right_hash_paths)):
        for left_name in left_hash_paths[digest]:
            for right_name in right_hash_paths[digest]:
                if left_name != right_name:
                    renamed_same_payload.append(f"{left_name} ↔ {right_name} · SHA-256 {short_hash(digest)}")

    changed_details = []
    for name in changed_names:
        left_entry = left_summary["entries"][name]
        right_entry = right_summary["entries"][name]
        size_note = "크기는 같지만 SHA-256이 다름" if left_entry["size"] == right_entry["size"] else "크기와 SHA-256이 다름"
        changed_details.append(
            f"{name} · {size_note} · "
            f"A {left_entry['size']:,} bytes/{left_entry['compressed']:,} compressed/{short_hash(left_summary['memberHashes'][name])} · "
            f"B {right_entry['size']:,} bytes/{right_entry['compressed']:,} compressed/{short_hash(right_summary['memberHashes'][name])}"
        )

    recompressed_details = [
        (
            f"{name} · 내부 SHA-256 동일 · 압축 후 "
            f"A {left_summary['entries'][name]['compressed']:,} bytes/방식 {left_summary['entries'][name]['method']} · "
            f"B {right_summary['entries'][name]['compressed']:,} bytes/방식 {right_summary['entries'][name]['method']}"
        )
        for name in recompressed_names
    ]

    left_overhead = left_summary["fileSize"] - left_summary["compressedSize"]
    right_overhead = right_summary["fileSize"] - right_summary["compressedSize"]
    compressed_delta = left_summary["compressedSize"] - right_summary["compressedSize"]
    overhead_delta = left_overhead - right_overhead
    all_internal_content_equal = not left_only_names and not right_only_names and not changed_names
    image_rows = align_epub_images(left_images, right_images)
    changed_image_rows = [row for row in image_rows if row["status"] != "완전 동일"]

    cause_items = [
        f"EPUB 전체 크기 차이: A {left_summary['fileSize']:,} bytes · B {right_summary['fileSize']:,} bytes · 차이 {signed_number(left_summary['fileSize'] - right_summary['fileSize'], ' bytes')}",
        f"내부 압축 데이터 합계 차이: A {left_summary['compressedSize']:,} bytes · B {right_summary['compressedSize']:,} bytes · 차이 {signed_number(compressed_delta, ' bytes')}",
        f"ZIP 헤더/파일명/중앙 디렉터리 등 포장 영역: A {left_overhead:,} bytes · B {right_overhead:,} bytes · 차이 {signed_number(overhead_delta, ' bytes')}",
    ]
    if all_internal_content_equal:
        cause_items.append("내부 파일 이름과 각 파일의 SHA-256이 전부 같습니다. EPUB 바깥 크기 차이는 압축률 또는 ZIP 포장 정보 차이입니다.")
    if recompressed_names:
        cause_items.append(f"내용은 같은데 압축 결과가 다른 내부 파일 {len(recompressed_names)}개가 전체 크기 차이에 영향을 줍니다.")
    if metadata_changed_names:
        cause_items.append(f"내용은 같지만 내부 수정일/ZIP 부가정보가 다른 파일 {len(metadata_changed_names)}개가 있습니다. 날짜 자체는 보통 크기를 늘리지 않지만 전체 해시를 바꿉니다.")
    if same_size_different_content:
        cause_items.append(f"파일 크기는 같아도 SHA-256이 다른 내부 파일이 {len(same_size_different_content)}개 있습니다. 같은 이미지 크기만으로 동일 파일이라고 판단하면 안 됩니다.")
    if changed_image_rows:
        cause_items.append(f"이미지 비교에서 완전 동일하지 않은 행이 {len(changed_image_rows)}개입니다. 아래 이미지 목록의 상태와 SHA/크기를 확인하세요.")

    if all_internal_content_equal:
        conclusion = "추출 본문뿐 아니라 EPUB 내부 파일의 SHA-256도 전부 같습니다. 두 파일의 차이는 압축률·헤더·수정일 같은 포장 정보이며, 둘 중 하나만 보관해도 내부 내용은 유지됩니다."
    elif not changed_names and (left_only_names or right_only_names):
        conclusion = "공통 내부 파일은 SHA-256까지 같지만 한쪽에만 있는 파일이 있습니다. 추가 파일이 표지·본문·목차인지 확인한 뒤 보관본을 고르세요."
    elif changed_names:
        conclusion = f"같은 경로인데 SHA-256이 다른 내부 파일이 {len(changed_names)}개 있습니다. 본문이 같아 보여도 EPUB 전체는 동일하지 않으므로 자동 삭제 대상으로 보면 안 됩니다."
    else:
        conclusion = "EPUB 내부 구성을 비교했습니다. 아래 크기 차이 원인과 한쪽에만 있는 파일을 확인하세요."

    recommended = "-"
    if left_summary["latestDate"] and right_summary["latestDate"]:
        if left_summary["latestDate"] > right_summary["latestDate"]:
            recommended = f"내부 수정일은 A({left.name})가 더 나중입니다."
        elif right_summary["latestDate"] > left_summary["latestDate"]:
            recommended = f"내부 수정일은 B({right.name})가 더 나중입니다."
    if recommended == "-" and left_text_length != right_text_length:
        side = "A" if left_text_length > right_text_length else "B"
        recommended = f"보이는 텍스트는 {side} 쪽이 더 많습니다."

    return {
        "summary": conclusion,
        "media": {"left": left_images, "right": right_images, "rows": image_rows},
        "metrics": [
            {"label": "파일 bytes", "value": f"{left_summary['fileSize']:,} vs {right_summary['fileSize']:,}"},
            {"label": "압축 데이터", "value": f"{left_summary['compressedSize']:,} vs {right_summary['compressedSize']:,}"},
            {"label": "포장 영역", "value": f"{left_overhead:,} vs {right_overhead:,}"},
            {"label": "내부 수정일", "value": f"{left_summary['latestDateText']} vs {right_summary['latestDateText']}"},
            {"label": "EPUB 내부 파일", "value": f"{left_summary['count']} vs {right_summary['count']}"},
            {"label": "EPUB 변경 파일", "value": f"{len(changed_names)}개"},
            {"label": "이미지", "value": f"{len(left_images)} vs {len(right_images)}"},
            {"label": "SHA256", "value": f"{left_summary['sha256Short']} vs {right_summary['sha256Short']}"},
        ],
        "sections": [
            {
                "title": "EPUB 내부 요약",
                "items": [
                    f"A: {left_summary['fileSize']:,} bytes, 내부 최신 수정일 {left_summary['latestDateText']}, 보이는 텍스트 {left_summary['visibleTextLength']:,}자, SHA256 {left_summary['sha256Short']}",
                    f"B: {right_summary['fileSize']:,} bytes, 내부 최신 수정일 {right_summary['latestDateText']}, 보이는 텍스트 {right_summary['visibleTextLength']:,}자, SHA256 {right_summary['sha256Short']}",
                    f"A 내부 파일 {left_summary['count']}개: {summarize_internal_names(sorted(left_names))}",
                    f"B 내부 파일 {right_summary['count']}개: {summarize_internal_names(sorted(right_names))}",
                    recommended,
                ],
            },
            {"title": "크기 차이 원인", "items": cause_items},
            {
                "title": "동일 내부 파일",
                "items": [
                    f"같은 경로에서 SHA-256까지 동일한 파일 {len(same_names)}개: {summarize_internal_categories(same_names)}",
                    summarize_internal_names(same_names, 100),
                ],
            },
            {"title": "내용은 같고 압축 결과만 다른 파일", "items": recompressed_details or ["없음"]},
            {"title": "같은 이름, 다른 내부 파일", "items": changed_details or ["없음"]},
            {"title": "경로만 다르고 내용이 같은 파일", "items": renamed_same_payload[:100] or ["없음"]},
            {
                "title": "한쪽에만 있는 내부 파일",
                "items": [
                    f"A에만 있음: {summarize_internal_names(left_only_names, 100)}",
                    f"B에만 있음: {summarize_internal_names(right_only_names, 100)}",
                ],
            },
        ],
        "details": [
            f"A bytes: {left_summary['fileSize']:,}",
            f"B bytes: {right_summary['fileSize']:,}",
            f"A compressed payload: {left_summary['compressedSize']:,}",
            f"B compressed payload: {right_summary['compressedSize']:,}",
            f"A packaging overhead: {left_overhead:,}",
            f"B packaging overhead: {right_overhead:,}",
            f"A latest internal modified: {left_summary['latestDateText']}",
            f"B latest internal modified: {right_summary['latestDateText']}",
            f"A visible text: {left_summary['visibleTextLength']:,}",
            f"B visible text: {right_summary['visibleTextLength']:,}",
            f"A sha256: {left_summary['sha256']}",
            f"B sha256: {right_summary['sha256']}",
            f"same internal files by SHA-256: {len(same_names)}",
            f"changed internal files by SHA-256: {len(changed_names)}",
            f"A-only internal files: {len(left_only_names)}",
            f"B-only internal files: {len(right_only_names)}",
        ],
    }


def short_list(values: list[str], limit: int = 5) -> str:
    if not values:
        return "-"
    visible = values[:limit]
    extra = len(values) - len(visible)
    suffix = f" ... (+{extra})" if extra > 0 else ""
    return ", ".join(visible) + suffix


def signed_number(value: int, unit: str = "") -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,}{unit}"


def sentence_diff_ranges(left: str, right: str) -> tuple[list[list[int]], list[list[int]], float]:
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    left_ranges: list[list[int]] = []
    right_ranges: list[list[int]] = []
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        if left_start != left_end:
            left_ranges.append([left_start, left_end])
        if right_start != right_end:
            right_ranges.append([right_start, right_end])
    return left_ranges, right_ranges, round(matcher.ratio() * 100, 1)


def sentence_ngrams(value: str, width: int = 4) -> set[str]:
    compact = re.sub(r"\s+", "", value.casefold())
    if len(compact) <= width:
        return {compact} if compact else set()
    return {compact[index:index + width] for index in range(len(compact) - width + 1)}


def pair_similar_sentences(left_only: list[str], right_only: list[str], limit: int = 24) -> list[dict]:
    """Pair near-identical sentences before showing one-sided additions."""
    left_pool = left_only[:800]
    right_pool = right_only[:2500]
    right_grams = [sentence_ngrams(value) for value in right_pool]
    gram_index: dict[str, list[int]] = {}
    for index, grams in enumerate(right_grams):
        for gram in grams:
            gram_index.setdefault(gram, []).append(index)

    candidates: list[tuple[float, int, int]] = []
    for left_index, left_value in enumerate(left_pool):
        grams = sentence_ngrams(left_value)
        shared_counts: Counter[int] = Counter()
        for gram in grams:
            shared_counts.update(gram_index.get(gram, ()))
        for right_index, _shared in shared_counts.most_common(8):
            quick_overlap = _shared / max(1, min(len(grams), len(right_grams[right_index])))
            if quick_overlap < 0.12:
                continue
            ratio = difflib.SequenceMatcher(None, left_value, right_pool[right_index], autojunk=False).ratio()
            if ratio >= 0.28:
                candidates.append((ratio, left_index, right_index))

    used_left: set[int] = set()
    used_right: set[int] = set()
    result: list[dict] = []
    for _ratio, left_index, right_index in sorted(candidates, reverse=True):
        if left_index in used_left or right_index in used_right:
            continue
        left_value = left_pool[left_index]
        right_value = right_pool[right_index]
        left_ranges, right_ranges, similarity = sentence_diff_ranges(left_value, right_value)
        result.append(
            {
                "left": left_value,
                "right": right_value,
                "leftRanges": left_ranges,
                "rightRanges": right_ranges,
                "similarity": similarity,
            }
        )
        used_left.add(left_index)
        used_right.add(right_index)
        if len(result) >= limit:
            return result

    for left_index, value in enumerate(left_pool):
        if left_index in used_left:
            continue
        result.append({"left": value, "right": "", "leftRanges": [[0, len(value)]], "rightRanges": [], "similarity": 0})
        if len(result) >= limit:
            return result
    for right_index, value in enumerate(right_pool):
        if right_index in used_right:
            continue
        result.append({"left": "", "right": value, "leftRanges": [], "rightRanges": [[0, len(value)]], "similarity": 0})
        if len(result) >= limit:
            break
    return result


def decode_bytes_with_label(data: bytes) -> tuple[str, str, str]:
    bom = "없음"
    candidates: list[tuple[str, str]] = []
    if data.startswith(b"\xef\xbb\xbf"):
        bom = "UTF-8 BOM"
        candidates.append(("utf-8-sig", "UTF-8"))
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        bom = "UTF-16 BOM"
        candidates.append(("utf-16", "UTF-16"))
    candidates.extend((("utf-8", "UTF-8"), ("cp949", "CP949"), ("euc-kr", "EUC-KR"), ("latin-1", "Latin-1")))
    for encoding, label in candidates:
        try:
            return data.decode(encoding), label, bom
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("utf-8", errors="replace"), "UTF-8(대체 문자 포함)", bom


def text_storage_profile(path: Path) -> dict:
    data = path.read_bytes()
    decoded, encoding, bom = decode_bytes_with_label(data)
    crlf = decoded.count("\r\n")
    lone_lf = decoded.count("\n") - crlf
    lone_cr = decoded.count("\r") - crlf
    invisible = Counter(f"U+{ord(char):04X}" for char in decoded if unicodedata.category(char) == "Cf")
    trailing_spaces = sum(len(line) - len(line.rstrip(" \t")) for line in decoded.splitlines())
    normalized = "".join(
        char
        for char in decoded
        if unicodedata.category(char) != "Cf"
        and not (0xFE00 <= ord(char) <= 0xFE0F or 0xE0100 <= ord(char) <= 0xE01EF)
    ).replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip(" \t") for line in normalized.split("\n"))
    return {
        "bytes": len(data),
        "encoding": encoding,
        "bom": bom,
        "crlf": crlf,
        "lf": lone_lf,
        "cr": lone_cr,
        "trailingSpaces": trailing_spaces,
        "invisible": dict(sorted(invisible.items())),
        "invisibleCount": sum(invisible.values()),
        "sha256": hashlib.sha256(data).hexdigest(),
        "rawSha256": hashlib.sha256(data).hexdigest(),
        "normalizedSha256": hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest(),
    }


def compare_zip_files(left: Path, right: Path) -> dict:
    archive_errors = []
    try:
        left_summary = zip_summary(left)
    except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        left_summary = None
        archive_errors.append(f"candidate archive open/read failed: {exc}")
    try:
        right_summary = zip_summary(right)
    except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        right_summary = None
        archive_errors.append(f"keep archive open/read failed: {exc}")

    if archive_errors:
        return {
            "ok": True,
            "kind": "archive",
            "summary": "ZIP \ud30c\uc77c \uc790\uccb4\ub97c \uc5ec\ub294 \ub2e8\uacc4\uc5d0\uc11c \uc2e4\ud328\ud588\uc2b5\ub2c8\ub2e4. \ud30c\uc77c \uc190\uc0c1, \uc554\ud638\ud654, \uc9c0\uc6d0\ud558\uc9c0 \uc54a\ub294 ZIP \ud615\uc2dd \uac00\ub2a5\uc131\uc774 \ud07d\ub2c8\ub2e4.",
            "details": [
                f"candidate archive size: {format_size(left.stat().st_size)}",
                f"keep archive size: {format_size(right.stat().st_size)}",
                *archive_errors,
            ],
            "metrics": [
                {"label": "\ud6c4\ubcf4 \uc555\ucd95 \ud06c\uae30", "value": format_size(left.stat().st_size)},
                {"label": "\uae30\uc900 \uc555\ucd95 \ud06c\uae30", "value": format_size(right.stat().st_size)},
            ],
            "sections": [{"title": "\uc624\ub958", "items": archive_errors}],
        }

    details = [
        f"candidate archive size: {format_size(left.stat().st_size)}",
        f"keep archive size: {format_size(right.stat().st_size)}",
        f"internal file count: {left_summary['count']} vs {right_summary['count']}",
        f"internal total size: {format_size(left_summary['totalSize'])} vs {format_size(right_summary['totalSize'])}",
        f"compressed payload: {format_size(left_summary['compressedSize'])} vs {format_size(right_summary['compressedSize'])}",
        f"candidate health: {left_summary['health']['status']} ({left_summary['health']['detail']})",
        f"keep health: {right_summary['health']['status']} ({right_summary['health']['detail']})",
    ]

    left_only = sorted(left_summary["names"] - right_summary["names"])
    right_only = sorted(right_summary["names"] - left_summary["names"])
    common = sorted(left_summary["names"] & right_summary["names"])
    changed = [
        name
        for name in common
        if (
            left_summary["entries"][name]["size"] != right_summary["entries"][name]["size"]
            or left_summary["entries"][name]["crc"] != right_summary["entries"][name]["crc"]
        )
    ]
    method_changed = [
        name
        for name in common
        if left_summary["entries"][name]["method"] != right_summary["entries"][name]["method"]
    ]

    if left_only:
        details.append(f"only in candidate: {short_list(left_only)}")
    if right_only:
        details.append(f"only in keep: {short_list(right_only)}")
    if changed:
        details.append(f"same internal name but different content: {short_list(changed)}")
    if method_changed:
        details.append(f"different compression method: {short_list(method_changed)}")

    if left_summary["health"]["broken"] or right_summary["health"]["broken"]:
        summary = "ZIP \ube60\ub978 \ud655\uc778\uc5d0\uc11c \uc190\uc0c1 \uc758\uc2ec \ud56d\ubaa9\uc774 \ubcf4\uc785\ub2c8\ub2e4. \uc0ad\uc81c \ud310\ub2e8 \uc804\uc5d0 \ud574\ub2f9 \ud30c\uc77c\uc744 \uba3c\uc800 \uc5f4\uc5b4\ubcf4\ub294 \ucabd\uc774 \uc548\uc804\ud569\ub2c8\ub2e4."
    elif left_only or right_only:
        summary = "ZIP \ub0b4\ubd80 \ud30c\uc77c \ubaa9\ub85d\uc774 \ub2ec\ub77c\uc11c \ud06c\uae30\uac00 \ub2ec\ub77c\uc9c4 \uac83\uc73c\ub85c \ubcf4\uc785\ub2c8\ub2e4."
    elif changed:
        summary = "ZIP \uc548\uc758 \uac19\uc740 \uc774\ub984 \ud30c\uc77c \ub0b4\uc6a9\uc774 \ub2ec\ub77c\uc11c \ud06c\uae30\uac00 \ub2ec\ub77c\uc9c4 \uac83\uc73c\ub85c \ubcf4\uc785\ub2c8\ub2e4."
    elif left_summary["compressedSize"] != right_summary["compressedSize"] or method_changed:
        summary = "ZIP \ub0b4\ubd80 \ub0b4\uc6a9\uc740 \uac70\uc758 \uac19\uc9c0\ub9cc \uc555\ucd95 \ubc29\uc2dd/\uc555\ucd95\ub960 \ucc28\uc774\ub85c \ud06c\uae30\uac00 \ub2ec\ub77c\uc9c4 \uac83\uc73c\ub85c \ubcf4\uc785\ub2c8\ub2e4."
    elif left_summary["totalSize"] == right_summary["totalSize"]:
        summary = "ZIP \ub0b4\ubd80 \uad6c\uc131\uc740 \uac19\uc544 \ubcf4\uc774\uba70, \ub0a0\uc9dc/\uba54\ud0c0\ub370\uc774\ud130/\uc555\ucd95 \ud5e4\ub354 \ucc28\uc774 \uac00\ub2a5\uc131\uc774 \ud07d\ub2c8\ub2e4."
    else:
        summary = "ZIP \ub0b4\ubd80 \ucd1d \uc6a9\ub7c9\uc774 \ub2ec\ub77c\uc11c \ud06c\uae30\uac00 \ub2ec\ub77c\uc9c4 \uac83\uc73c\ub85c \ubcf4\uc785\ub2c8\ub2e4."

    return {
        "ok": True,
        "kind": "archive",
        "summary": summary,
        "details": details,
        "metrics": [
            {"label": "\ud6c4\ubcf4 \uc555\ucd95 \ud06c\uae30", "value": format_size(left.stat().st_size)},
            {"label": "\uae30\uc900 \uc555\ucd95 \ud06c\uae30", "value": format_size(right.stat().st_size)},
            {"label": "\ub0b4\ubd80 \ud30c\uc77c \uc218", "value": f"{left_summary['count']} vs {right_summary['count']}"},
            {"label": "\ub0b4\ubd80 \ucd1d \uc6a9\ub7c9", "value": f"{format_size(left_summary['totalSize'])} vs {format_size(right_summary['totalSize'])}"},
            {"label": "\uc555\ucd95\ub41c \uc6a9\ub7c9", "value": f"{format_size(left_summary['compressedSize'])} vs {format_size(right_summary['compressedSize'])}"},
            {"label": "\ud6c4\ubcf4 \uc0c1\ud0dc", "value": left_summary["health"]["status"]},
            {"label": "\uae30\uc900 \uc0c1\ud0dc", "value": right_summary["health"]["status"]},
        ],
        "sections": [
            {"title": "\ud6c4\ubcf4\uc5d0\ub9cc \uc788\uc74c", "items": left_only[:20]},
            {"title": "\uae30\uc900\uc5d0\ub9cc \uc788\uc74c", "items": right_only[:20]},
            {"title": "\uac19\uc740 \uc774\ub984, \ub2e4\ub978 \ub0b4\uc6a9", "items": changed[:20]},
            {"title": "\uc555\ucd95 \ubc29\uc2dd \ub2e4\ub984", "items": method_changed[:20]},
        ],
    }

def compare_text_files(left: Path, right: Path, extension: str) -> dict:
    left_text = text_from_bytes(left.read_bytes(), extension)
    right_text = text_from_bytes(right.read_bytes(), extension)
    left_sentences = set(normalized_sentences(left_text))
    right_sentences = set(normalized_sentences(right_text))
    common_count = len(left_sentences & right_sentences)
    left_only = sorted(left_sentences - right_sentences)
    right_only = sorted(right_sentences - left_sentences)
    text_length_delta = len(left_text) - len(right_text)
    sentence_count_delta = len(left_sentences) - len(right_sentences)
    overlap = round(common_count / max(1, min(len(left_sentences), len(right_sentences))) * 100, 1)
    left_coverage = round(common_count / max(1, len(left_sentences)) * 100, 1)
    right_coverage = round(common_count / max(1, len(right_sentences)) * 100, 1)
    sentence_pairs = pair_similar_sentences(left_only, right_only)
    details = [
        f"candidate file size: {format_size(left.stat().st_size)}",
        f"keep file size: {format_size(right.stat().st_size)}",
        f"text length: {len(left_text):,} chars vs {len(right_text):,} chars",
        f"text length delta: {signed_number(text_length_delta, ' chars')}",
        f"sentence count: {len(left_sentences):,} vs {len(right_sentences):,}",
        f"sentence count delta: {signed_number(sentence_count_delta)}",
        f"sentence overlap: {common_count:,} common ({overlap}%)",
    ]
    if left_only:
        details.append(f"candidate-only sentence: {short_list([text[:90] for text in left_only], 3)}")
    if right_only:
        details.append(f"keep-only sentence: {short_list([text[:90] for text in right_only], 3)}")

    if not left_text and not right_text:
        summary = "\ubcf8\ubb38\uc744 \ucd94\ucd9c\ud558\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4. \ud14d\uc2a4\ud2b8\uac00 \uc544\ub2cc \ucee8\ud14c\uc774\ub108, \uc554\ud638\ud654, \uc190\uc0c1 \ud30c\uc77c\uc77c \uc218 \uc788\uc2b5\ub2c8\ub2e4."
    elif left_sentences == right_sentences:
        summary = "\ucd94\ucd9c\ub41c \ubb38\uc7a5\uc740 \uac19\uc2b5\ub2c8\ub2e4. \uc778\ucf54\ub529, \uc904\ubc14\uafc8, \ud45c\uc9c0/\uba54\ud0c0\ub370\uc774\ud130 \ucc28\uc774\ub85c \ud06c\uae30\uac00 \ub2ec\ub77c\uc9c4 \uac00\ub2a5\uc131\uc774 \ud07d\ub2c8\ub2e4."
    elif right_coverage >= 99.5 and left_coverage < 95:
        summary = "\ud6c4\ubcf4 \ud30c\uc77c\uc774 \uae30\uc900 \ubcf8\ubb38\uc744 \uac70\uc758 \ubaa8\ub450 \ud3ec\ud568\ud558\uc9c0\ub9cc, \ud6c4\ubcf4 \ucabd\uc5d0 \ucd94\uac00 \ub0b4\uc6a9\uc774 \ub354 \ub9ce\uc2b5\ub2c8\ub2e4."
    elif left_coverage >= 99.5 and right_coverage < 95:
        summary = "\uae30\uc900 \ud30c\uc77c\uc774 \ud6c4\ubcf4 \ubcf8\ubb38\uc744 \uac70\uc758 \ubaa8\ub450 \ud3ec\ud568\ud558\uc9c0\ub9cc, \uae30\uc900 \ucabd\uc5d0 \ucd94\uac00 \ub0b4\uc6a9\uc774 \ub354 \ub9ce\uc2b5\ub2c8\ub2e4."
    elif overlap >= 90:
        summary = "\ubcf8\ubb38 \ub300\ubd80\ubd84\uc740 \uac19\uc9c0\ub9cc \uc77c\ubd80 \ubb38\uc7a5/\ubd80\ub85d/\ucd94\uac00\uac00 \ub2ec\ub77c\uc11c \ud06c\uae30\uac00 \ub2ec\ub77c\uc9c4 \uac83\uc73c\ub85c \ubcf4\uc785\ub2c8\ub2e4."
    elif overlap >= 50:
        summary = "\uac19\uc740 \uc791\ud488 \uc77c\ubd80\uac00 \uacb9\uce58\uc9c0\ub9cc \ud3b8\uc9d1\ubcf8/\uad8c\ucc28/\ucd94\uac00 \ub0b4\uc6a9 \ucc28\uc774\uac00 \uc788\uc5b4 \ubcf4\uc785\ub2c8\ub2e4."
    else:
        summary = "\uc81c\ubaa9\uc740 \uac19\uc544\ub3c4 \ubcf8\ubb38 \ubb38\uc7a5\uc774 \ub9ce\uc774 \ub2ec\ub77c\uc11c \ub2e4\ub978 \ud30c\uc77c\uc77c \uac00\ub2a5\uc131\uc774 \ud07d\ub2c8\ub2e4."

    extra_metrics: list[dict] = []
    extra_sections: list[dict] = []
    extra_media: dict = {}
    if extension == ".epub":
        epub_payload = epub_compare_payload(left, right, len(left_text), len(right_text))
        extra_metrics = epub_payload["metrics"]
        extra_sections = epub_payload["sections"]
        extra_media = epub_payload.get("media", {})
        details.extend(epub_payload["details"])
        epub_summary = str(epub_payload.get("summary") or "").strip()
        if epub_summary:
            if left_sentences == right_sentences:
                summary = epub_summary
            else:
                summary = f"{summary} {epub_summary}"
    elif extension == ".txt":
        left_storage = text_storage_profile(left)
        right_storage = text_storage_profile(right)
        left_hidden = ", ".join(f"{code} {count:,}개" for code, count in left_storage["invisible"].items()) or "없음"
        right_hidden = ", ".join(f"{code} {count:,}개" for code, count in right_storage["invisible"].items()) or "없음"
        same_normalized_storage = left_storage["normalizedSha256"] == right_storage["normalizedSha256"]
        storage_only_difference = same_normalized_storage and left_storage["sha256"] != right_storage["sha256"]
        if storage_only_difference:
            summary = (
                "보이는 글 내용은 같습니다. SHA-256이 다른 이유는 인코딩, BOM, 줄바꿈, "
                "줄 끝 공백 또는 숨은 제어문자 같은 저장 형식 차이입니다."
            )
        extra_metrics = [
            {
                "label": "TXT 인코딩",
                "value": f"{left_storage['encoding']} vs {right_storage['encoding']}",
            },
            {
                "label": "TXT 줄바꿈",
                "value": (
                    f"CRLF {left_storage['crlf']:,} / LF {left_storage['lf']:,} / CR {left_storage['cr']:,}"
                    f" vs CRLF {right_storage['crlf']:,} / LF {right_storage['lf']:,} / CR {right_storage['cr']:,}"
                ),
            },
            {
                "label": "TXT 숨은 코드",
                "value": f"{left_storage['invisibleCount']:,} vs {right_storage['invisibleCount']:,}",
            },
        ]
        extra_sections = [
            {
                "title": "TXT 저장 형식 차이",
                "items": [
                    (
                        f"A: {left_storage['bytes']:,} bytes, {left_storage['encoding']}, "
                        f"BOM {left_storage['bom'] or '없음'}, CRLF {left_storage['crlf']:,}, "
                        f"LF {left_storage['lf']:,}, CR {left_storage['cr']:,}, "
                        f"줄 끝 공백 {left_storage['trailingSpaces']:,}, 숨은 코드 {left_hidden}"
                    ),
                    (
                        f"B: {right_storage['bytes']:,} bytes, {right_storage['encoding']}, "
                        f"BOM {right_storage['bom'] or '없음'}, CRLF {right_storage['crlf']:,}, "
                        f"LF {right_storage['lf']:,}, CR {right_storage['cr']:,}, "
                        f"줄 끝 공백 {right_storage['trailingSpaces']:,}, 숨은 코드 {right_hidden}"
                    ),
                    (
                        "저장 형식을 통일한 뒤의 텍스트 해시도 같습니다. 원문 차이가 아니라 저장 방식 차이입니다."
                        if same_normalized_storage
                        else "저장 형식을 통일한 뒤에도 텍스트 해시가 다릅니다. 실제 글 내용 차이가 남아 있습니다."
                    ),
                ],
            }
        ]
        details.extend(
            [
                f"candidate encoding/newline: {left_storage['encoding']}, CRLF {left_storage['crlf']}, LF {left_storage['lf']}",
                f"keep encoding/newline: {right_storage['encoding']}, CRLF {right_storage['crlf']}, LF {right_storage['lf']}",
                f"normalized storage hash equal: {same_normalized_storage}",
            ]
        )

    return {
        "ok": True,
        "kind": "text",
        "summary": summary,
        "details": details,
        "media": extra_media,
        "sentencePairs": sentence_pairs,
        "metrics": [
            {"label": "\ud6c4\ubcf4 \ud06c\uae30", "value": format_size(left.stat().st_size)},
            {"label": "\uae30\uc900 \ud06c\uae30", "value": format_size(right.stat().st_size)},
            {"label": "\ubcf8\ubb38 \uae38\uc774", "value": f"{len(left_text):,} vs {len(right_text):,}"},
            {"label": "\ubcf8\ubb38 \uae38\uc774 \ucc28\uc774", "value": signed_number(text_length_delta, "\uc790")},
            {"label": "\ubb38\uc7a5 \uc218", "value": f"{len(left_sentences):,} vs {len(right_sentences):,}"},
            {"label": "\ubb38\uc7a5 \uc218 \ucc28\uc774", "value": signed_number(sentence_count_delta, "\uac1c")},
            {"label": "\uacb9\uce68", "value": f"{common_count:,}\uac1c / {overlap}%"},
            {"label": "\ud6c4\ubcf4 \ud3ec\ud568\ub960", "value": f"{left_coverage}%"},
            {"label": "\uae30\uc900 \ud3ec\ud568\ub960", "value": f"{right_coverage}%"},
            *extra_metrics,
        ],
        "sections": [
            {"title": "\ud6c4\ubcf4\uc5d0\ub9cc \uc788\ub294 \ubb38\uc7a5", "items": [text[:220] for text in left_only[:12]]},
            {"title": "\uae30\uc900\uc5d0\ub9cc \uc788\ub294 \ubb38\uc7a5", "items": [text[:220] for text in right_only[:12]]},
            *extra_sections,
        ],
    }

def compare_binary_files(left: Path, right: Path) -> dict:
    left_hash = file_sha256(left)
    right_hash = file_sha256(right)
    details = [
        f"candidate file size: {format_size(left.stat().st_size)}",
        f"keep file size: {format_size(right.stat().st_size)}",
        f"candidate sha256: {left_hash[:16]}",
        f"keep sha256: {right_hash[:16]}",
    ]
    if left_hash == right_hash:
        summary = "파일 바이트가 완전히 같습니다. 목록에 남아 있다면 이름/위치만 다른 중복입니다."
    else:
        summary = "바이너리 내용이 다릅니다. 이 형식은 아직 내부 원인까지 자동 분석하지 못합니다."
    return {
        "ok": True,
        "kind": "binary",
        "summary": summary,
        "details": details,
        "metrics": [
            {"label": "후보 크기", "value": format_size(left.stat().st_size)},
            {"label": "기준 크기", "value": format_size(right.stat().st_size)},
            {"label": "후보 해시", "value": left_hash[:16]},
            {"label": "기준 해시", "value": right_hash[:16]},
        ],
        "sections": [],
    }


def materialize_compare_reference(raw_value: str, temp_root: Path, side: str) -> tuple[Path, str]:
    if "::" not in raw_value:
        path = Path(raw_value)
        return path, path.name

    archive_text, inner_text = (part.strip() for part in raw_value.split("::", 1))
    archive_path = Path(archive_text)
    if not archive_path.is_file():
        raise FileNotFoundError(f"ZIP 파일을 찾지 못했습니다: {archive_path}")
    matched_info = None
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            decoded_name = decode_zip_member_name(info.filename, info.flag_bits)
            if decoded_name == inner_text:
                matched_info = info
                break
        if matched_info is None:
            raise FileNotFoundError(f"ZIP 내부 파일을 찾지 못했습니다: {inner_text}")
        display_name = PurePosixPath(inner_text).name or f"{side}.bin"
        safe_name = re.sub(r'[<>:"/\\|?*]+', "_", display_name).strip(" .") or f"{side}.bin"
        side_dir = temp_root / side
        side_dir.mkdir(parents=True, exist_ok=True)
        materialized = side_dir / safe_name
        with archive.open(matched_info) as source, materialized.open("wb") as target:
            shutil.copyfileobj(source, target, CHUNK_SIZE)
    return materialized, display_name


def compare_items(args: argparse.Namespace) -> dict:
    left_raw = str(args.left)
    right_raw = str(args.right)
    try:
        with tempfile.TemporaryDirectory(prefix="file-tidier-compare-") as temp_dir:
            temp_root = Path(temp_dir)
            left, left_name = materialize_compare_reference(left_raw, temp_root, "left")
            right, right_name = materialize_compare_reference(right_raw, temp_root, "right")
            if not left.is_file() or not right.is_file():
                return {"ok": False, "error": "비교할 실제 파일을 찾지 못했습니다."}

            left_extension = left.suffix.lower()
            right_extension = right.suffix.lower()
            if left_extension != right_extension:
                return {"ok": False, "error": "확장자가 다른 파일은 내용 비교 대상이 아닙니다."}

            if left_extension in {".zip", ".cbz"}:
                payload = compare_zip_files(left, right)
            elif left_extension in TEXT_EXTENSIONS:
                payload = compare_text_files(left, right, left_extension)
            else:
                payload = compare_binary_files(left, right)
            payload.update(
                {
                    "leftName": left_name,
                    "rightName": right_name,
                    "leftPath": left_raw,
                    "rightPath": right_raw,
                }
            )
            return payload
    except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        return {"ok": False, "error": f"ZIP 내부 파일 비교 실패: {exc}"}


def unique_quarantine_path(target_dir: Path, name: str) -> Path:
    candidate = target_dir / name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(1, 10000):
        candidate = target_dir / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Cannot create unique quarantine name for {name}")


def parse_paths_argument(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        text = (value or "").strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        parsed = [part.strip().strip('"') for part in text.split(",") if part.strip()]
    if not isinstance(parsed, list):
        raise ValueError("paths must be a JSON list")
    return [str(item) for item in parsed]


def quarantine_files(args: argparse.Namespace) -> dict:
    root = Path(args.folder).resolve()
    quarantine_dir = root / "_FileTidier_Quarantine"
    moved: list[dict] = []
    skipped: list[dict] = []
    raw_paths = parse_paths_argument(args.paths)

    quarantine_dir.mkdir(exist_ok=True)
    for raw_path in raw_paths:
        if not isinstance(raw_path, str):
            skipped.append({"path": str(raw_path), "reason": "경로가 문자열이 아님"})
            continue
        if "::" in raw_path:
            skipped.append({"path": raw_path, "reason": "zip 내부 항목은 격리 이동 제외"})
            continue
        source = Path(raw_path)
        try:
            resolved = source.resolve()
        except OSError as exc:
            skipped.append({"path": raw_path, "reason": str(exc)})
            continue
        try:
            resolved.relative_to(root)
        except ValueError:
            skipped.append({"path": raw_path, "reason": "선택 폴더 밖의 파일"})
            continue
        if not resolved.is_file():
            skipped.append({"path": raw_path, "reason": "파일이 없거나 일반 파일이 아님"})
            continue
        try:
            target = unique_quarantine_path(quarantine_dir, resolved.name)
            shutil.move(str(resolved), str(target))
            moved.append({"from": str(resolved), "to": str(target)})
        except OSError as exc:
            skipped.append({"path": raw_path, "reason": str(exc)})

    return {
        "ok": True,
        "quarantine": str(quarantine_dir),
        "moved": moved,
        "skipped": skipped,
        "movedCount": len(moved),
        "skippedCount": len(skipped),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="File Tidier JSON backend")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("catalog", "titles"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--folder", required=True)
        sub.add_argument("--query", default="")
        sub.add_argument("--limit", type=int, default=2000)
        sub.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
        sub.add_argument("--include-zip", action=argparse.BooleanOptionalAction, default=False)
        sub.add_argument("--min-size-kb", type=float, default=1)
        sub.add_argument("--allowed-extensions", default="")
        sub.add_argument("--with-thumbnails", action=argparse.BooleanOptionalAction, default=False)
        sub.add_argument("--thumbnail-limit", type=int, default=0)

    for command in ("duplicates-size", "duplicates-content", "duplicates-comprehensive", "zip-internal-hashes"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--folder", required=True)
        sub.add_argument("--query", default="")
        sub.add_argument("--limit", type=int, default=2000)
        sub.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
        sub.add_argument("--include-zip", action=argparse.BooleanOptionalAction, default=False)
        sub.add_argument("--min-size-kb", type=float, default=1)
        sub.add_argument("--allowed-extensions", default="")

    text_dup = subparsers.add_parser("text-duplicates")
    text_dup.add_argument("--folder", default=".")
    text_dup.add_argument("--zip-file", default="")
    text_dup.add_argument("--reference-zip", default="")
    text_dup.add_argument("--query", default="")
    text_dup.add_argument("--limit", type=int, default=2000)
    text_dup.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
    text_dup.add_argument("--include-zip", action=argparse.BooleanOptionalAction, default=True)
    text_dup.add_argument("--min-size-kb", type=float, default=1)
    text_dup.add_argument("--allowed-extensions", default="")

    reference = subparsers.add_parser("reference-sentences")
    reference.add_argument("--folder", required=True)
    reference.add_argument("--reference-zip", required=True)
    reference.add_argument("--query", default="")
    reference.add_argument("--limit", type=int, default=2000)
    reference.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
    reference.add_argument("--include-zip", action=argparse.BooleanOptionalAction, default=True)
    reference.add_argument("--min-size-kb", type=float, default=1)
    reference.add_argument("--allowed-extensions", default="")

    rename = subparsers.add_parser("rename-preview")
    rename.add_argument("--folder", required=True)
    rename.add_argument("--query", default="")
    rename.add_argument("--limit", type=int, default=2000)
    rename.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
    rename.add_argument("--include-zip", action=argparse.BooleanOptionalAction, default=False)
    rename.add_argument("--min-size-kb", type=float, default=1)
    rename.add_argument("--allowed-extensions", default="")
    rename.add_argument("--find", default="")
    rename.add_argument("--replace", default="")
    rename.add_argument("--position", choices=("front", "back", "anywhere", "exact"), default="front")
    rename.add_argument("--regex", action=argparse.BooleanOptionalAction, default=False)
    rename.add_argument("--prefix", default="")
    rename.add_argument("--suffix", default="")
    rename.add_argument("--case", choices=("keep", "lower", "upper", "title"), default="keep")
    rename.add_argument("--start-number", type=int, default=-1)
    rename.add_argument("--padding", type=int, default=3)
    rename.add_argument("--author", default="")
    rename.add_argument("--author-pattern", choices=("prefix", "suffix"), default="prefix")
    rename.add_argument("--strip-copy-suffix", action=argparse.BooleanOptionalAction, default=False)
    rename.add_argument("--auto-author", action=argparse.BooleanOptionalAction, default=True)
    rename.add_argument("--normalize-title-format", action=argparse.BooleanOptionalAction, default=True)

    apply_rename_parser = subparsers.add_parser("apply-rename")
    apply_rename_parser.add_argument("--folder", required=True)
    apply_rename_parser.add_argument("--query", default="")
    apply_rename_parser.add_argument("--limit", type=int, default=2000)
    apply_rename_parser.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
    apply_rename_parser.add_argument("--include-zip", action=argparse.BooleanOptionalAction, default=False)
    apply_rename_parser.add_argument("--min-size-kb", type=float, default=1)
    apply_rename_parser.add_argument("--allowed-extensions", default="")
    apply_rename_parser.add_argument("--find", default="")
    apply_rename_parser.add_argument("--replace", default="")
    apply_rename_parser.add_argument("--position", choices=("front", "back", "anywhere", "exact"), default="front")
    apply_rename_parser.add_argument("--regex", action=argparse.BooleanOptionalAction, default=False)
    apply_rename_parser.add_argument("--prefix", default="")
    apply_rename_parser.add_argument("--suffix", default="")
    apply_rename_parser.add_argument("--case", choices=("keep", "lower", "upper", "title"), default="keep")
    apply_rename_parser.add_argument("--start-number", type=int, default=-1)
    apply_rename_parser.add_argument("--padding", type=int, default=3)
    apply_rename_parser.add_argument("--author", default="")
    apply_rename_parser.add_argument("--author-pattern", choices=("prefix", "suffix"), default="prefix")
    apply_rename_parser.add_argument("--strip-copy-suffix", action=argparse.BooleanOptionalAction, default=False)
    apply_rename_parser.add_argument("--auto-author", action=argparse.BooleanOptionalAction, default=True)
    apply_rename_parser.add_argument("--normalize-title-format", action=argparse.BooleanOptionalAction, default=True)

    quarantine = subparsers.add_parser("quarantine")
    quarantine.add_argument("--folder", required=True)
    quarantine.add_argument("--paths", default="[]")

    compare = subparsers.add_parser("compare-items")
    compare.add_argument("--left", required=True)
    compare.add_argument("--right", required=True)

    web_cover = subparsers.add_parser("web-covers")
    web_cover.add_argument("--folder", required=True)
    web_cover.add_argument("--query", default="")
    web_cover.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
    web_cover.add_argument("--max-items", type=int, default=20)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "catalog":
            write_json(scan_catalog(args))
        elif args.command == "titles":
            write_json(scan_titles(args))
        elif args.command == "duplicates-size":
            write_json(scan_size_duplicates(args))
        elif args.command == "duplicates-content":
            write_json(scan_content_duplicates(args))
        elif args.command == "duplicates-comprehensive":
            write_json(scan_comprehensive_duplicates(args))
        elif args.command == "zip-internal-hashes":
            write_json(scan_zip_internal_hashes(args))
        elif args.command == "text-duplicates":
            write_json(scan_text_duplicates(args))
        elif args.command == "reference-sentences":
            write_json(scan_reference_sentences(args))
        elif args.command == "rename-preview":
            write_json(preview_rename(args))
        elif args.command == "apply-rename":
            write_json(apply_rename(args))
        elif args.command == "quarantine":
            write_json(quarantine_files(args))
        elif args.command == "compare-items":
            write_json(compare_items(args))
        elif args.command == "web-covers":
            write_json(scan_web_covers(args))
        else:
            parser.error(f"Unknown command: {args.command}")
    except Exception as exc:
        write_json({"ok": False, "error": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
