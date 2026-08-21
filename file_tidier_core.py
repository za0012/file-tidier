from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from pathlib import PurePosixPath

CHUNK_SIZE = 1024 * 1024
DEFAULT_DISPLAY_LIMIT = 2000
MAX_ZIP_ITEMS_PER_ARCHIVE = 2000
ZIP_UTF8_FLAG = 0x800
BOOK_EXTENSIONS = {
    ".azw3",
    ".cbz",
    ".doc",
    ".docx",
    ".epub",
    ".htm",
    ".html",
    ".mobi",
    ".pdf",
    ".rtf",
    ".txt",
    ".zip",
}
ZIP_EXTENSIONS = {".zip", ".cbz"}
COPY_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")
SERIES_GROUPING_PATTERNS = (
    re.compile(r"\[.*?\]|\(.*?\)"),
    re.compile(r"(?i)[\s\-]*(lit|리디|리디북스|카카페|시리즈|공금|텍본|완결|완|조아라|노벨피아|문피아)\s*$"),
    re.compile(r"(?i)\s+(?:특별\s*|S\s*|IF\s*)?(?:외전|단편|특별편|합본|본편|연재|단행본|텍본|본)[\s\d_].*$"),
    re.compile(r"(?i)\s*\d+\s*(?:권|화|부|회|편|장)?\s*@.*$"),
    re.compile(r"(?i)[\s\-]*\d+\s*[~-]\s*\d+\s*(?:권|화|부|회|편|장|완결|완)?\s*$"),
    re.compile(r"(?i)[\s\-]*\d+\s*(?:권|화|부|회|편|장|권완결|완)\s*$"),
    re.compile(r"(?i)[\s\-]*(?:특별\s*|S\s*|IF\s*)?(?:외전|단편|특별편|합본|본편|연재|단행본|완결|완|텍본|본)\s*\d*\s*$"),
    re.compile(r"(?<=[가-힣a-zA-Z])\s*\d{1,3}\s*$"),
    re.compile(r"[\s\-]+\d{1,3}\s*$"),
)
AUTHOR_MARKER_PATTERNS = (
    re.compile(r"^\s*\[([^\]]{1,50})\]\s*"),
    re.compile(r"^\s*\u3010([^\u3011]{1,50})\u3011\s*"),
    re.compile(r"^\s*([^@]{1,50})@\s*"),
    re.compile(r"\s*@([^@]{1,50})\s*$"),
    re.compile(r"^\s*\u24d2\s*([^\s\[\](){}]{1,50})\s*"),
    re.compile(r"\s*\u24d2\s*([^\s\[\](){}]{1,50})\s*$"),
)
AUTHOR_REJECT_WORDS = {
    "완결",
    "미완",
    "미완결",
    "외전",
    "개정판",
    "본편",
    "텍본",
    "스캔",
    "판타지",
    "무협",
    "로맨스",
    "로판",
    "현판",
    "일반",
}
AUTHOR_AT_SOURCE_SUFFIX_RE = re.compile(r"^(?P<title>.+@.+?)\s+-\s*(?P<author>[^-@\[\](){}]{1,50})$")
AUTHOR_UNDERSCORE_PREFIX_RE = re.compile(r"^(?P<author>[^_@\[\](){}]{1,50})_(?P<title>.+)$")
LEADING_TAG_RE = re.compile(
    r"^(?:\s*(?:\[[^\]]+\]|\([^)]+\)|\{[^}]+\}|【[^】]+】|〔[^〕]+〕|（[^）]+）)\s*)+"
)
CHOSEONG = ["ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]
JUNGSEONG = ["ㅏ", "ㅐ", "ㅑ", "ㅒ", "ㅓ", "ㅔ", "ㅕ", "ㅖ", "ㅗ", "ㅘ", "ㅙ", "ㅚ", "ㅛ", "ㅜ", "ㅝ", "ㅞ", "ㅟ", "ㅠ", "ㅡ", "ㅢ", "ㅣ"]
JONGSEONG = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]


@dataclass(frozen=True)
class FileRecord:
    path: Path
    size: int
    modified: float
    # 캐시 키로 쓰는 나노초 수정시각. scandir 가 이미 읽어 온 값이라 공짜다.
    mtime_ns: int = 0


@dataclass(frozen=True)
class RenameEntry:
    source: Path
    target: Path
    status: str


@dataclass(frozen=True)
class TitleRecord:
    title: str
    name: str
    extension: str
    kind: str
    size: int
    modified: str
    location: str


@dataclass(frozen=True)
class CatalogRecord:
    name: str
    title: str
    extension: str
    kind: str
    size: int
    modified: str
    location: str


@dataclass(frozen=True)
class SkippedRecord:
    path: str
    reason: str


class ScanCancelled(Exception):
    pass


def check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ScanCancelled


def iter_files(root: Path, recursive: bool, cancel_event: threading.Event | None = None) -> list[FileRecord]:
    records: list[FileRecord] = []

    def visit(folder: Path) -> None:
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        for entry in entries:
            check_cancel(cancel_event)
            try:
                if entry.is_file(follow_symlinks=False):
                    stat = entry.stat(follow_symlinks=False)
                    records.append(
                        FileRecord(
                            path=Path(entry.path),
                            size=stat.st_size,
                            modified=stat.st_mtime,
                            mtime_ns=stat.st_mtime_ns,
                        )
                    )
                elif recursive and entry.is_dir(follow_symlinks=False):
                    visit(Path(entry.path))
            except OSError:
                continue

    visit(root)
    return sorted(records, key=lambda item: str(item.path).lower())


def format_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{size} B"
        value /= 1024
    return f"{size} B"


def format_mtime(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return "-"


def hash_file(path: Path, cancel_event: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            check_cancel(cancel_event)
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def group_by_size(
    records: list[FileRecord],
    min_size: int,
    cancel_event: threading.Event | None = None,
) -> dict[int, list[FileRecord]]:
    groups: dict[int, list[FileRecord]] = {}
    for record in records:
        check_cancel(cancel_event)
        if record.size < min_size:
            continue
        groups.setdefault(record.size, []).append(record)
    return {size: items for size, items in groups.items() if len(items) > 1}


def group_by_content(
    records: list[FileRecord],
    min_size: int,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
    hash_provider=None,
    error_callback=None,
) -> dict[str, list[FileRecord]]:
    """크기가 같은 파일만 골라 해시를 비교한다.

    hash_provider 를 넘기면 그것으로 해시를 구한다. 캐시를 끼워 넣어
    이미 읽어 본 파일을 다시 읽지 않게 하려는 용도다(디스크를 아끼는 핵심).
    """
    size_groups = group_by_size(records, min_size, cancel_event)
    hash_candidates = [record for items in size_groups.values() for record in items]
    hash_groups: dict[str, list[FileRecord]] = {}
    for index, record in enumerate(hash_candidates, start=1):
        check_cancel(cancel_event)
        if progress_callback:
            progress_callback(index, len(hash_candidates), record)
        try:
            if hash_provider is not None:
                file_hash = hash_provider(record)
            else:
                file_hash = hash_file(record.path, cancel_event)
        except OSError as exc:
            # 읽기 실패는 조용히 넘기지 않고 호출자에게 알린다.
            if error_callback is not None:
                error_callback(record, exc)
            continue
        if not file_hash:
            continue
        hash_groups.setdefault(file_hash, []).append(record)
    return {file_hash: items for file_hash, items in hash_groups.items() if len(items) > 1}


def compose_compat_jamo(text: str) -> str:
    result: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char in CHOSEONG and i + 1 < len(text) and text[i + 1] in JUNGSEONG:
            cho = CHOSEONG.index(char)
            jung = JUNGSEONG.index(text[i + 1])
            jong = 0
            i += 2
            if i < len(text) and text[i] in JONGSEONG[1:]:
                consonant = text[i]
                next_is_vowel = i + 1 < len(text) and text[i + 1] in JUNGSEONG
                if not next_is_vowel:
                    compound = consonant + text[i + 1] if i + 1 < len(text) else ""
                    if compound in JONGSEONG and not (i + 2 < len(text) and text[i + 2] in JUNGSEONG):
                        jong = JONGSEONG.index(compound)
                        i += 2
                    else:
                        jong = JONGSEONG.index(consonant)
                        i += 1
            result.append(chr(0xAC00 + (cho * 21 + jung) * 28 + jong))
        else:
            result.append(char)
            i += 1
    return "".join(result)


def clean_display_name(name: str) -> str:
    normalized = unicodedata.normalize("NFC", compose_compat_jamo(name))
    return "".join(char for char in normalized if unicodedata.category(char) != "Cf")


def normalize_book_title(name: str) -> str:
    filename = PurePosixPath(clean_display_name(name).replace("\\", "/")).name
    stem, _extension = os.path.splitext(filename)
    stem = LEADING_TAG_RE.sub("", stem)
    stem = remove_copy_suffix(stem)
    stem = re.sub(r"[_\-.]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem)
    return stem.strip(" ._-")


def book_volume_signature(name: str) -> str:
    """Return a semantic volume marker without treating copy suffixes as volumes."""
    title = normalize_book_title(name)
    extra = re.search(r"(?i)(?:^|\s)(?:외전|특별편)\s*(\d{1,3})?", title)
    if extra:
        return f"extra:{extra.group(1) or '0'}"

    volume_range = re.search(r"(?<!\d)(\d{1,3})\s*[~-]\s*(\d{1,3})\s*(?:권|卷)", title)
    if volume_range:
        return f"volume:{volume_range.group(1)}-{volume_range.group(2)}"

    volume = re.search(r"(?<!\d)(\d{1,3})\s*(?:권|卷)", title)
    if volume:
        return f"volume:{volume.group(1)}"

    part = re.search(r"(?<!\d)(\d{1,3})\s*부", title)
    if part:
        return f"part:{part.group(1)}"

    episode = re.search(r"(?<!\d)(\d{1,4})\s*(?:화|회)", title)
    if episode:
        return f"episode:{episode.group(1)}"

    section = re.search(r"(?:^|\s)(상|중|하)\s*(?:권|편)?\s*(?:완결|완)?\s*$", title)
    if section:
        return f"section:{section.group(1)}"

    trailing_title = re.sub(r"(?i)\s*(?:완결|완|수정|개정판)\s*$", "", title).strip()
    trailing = re.search(r"(?:^|\s)(\d{1,3})\s*$", trailing_title)
    if trailing:
        return f"volume:{trailing.group(1)}"
    return ""


def normalize_series_title(name: str) -> str:
    """Display-only series key based on the V6 smart grouping rules."""
    title = normalize_book_title(name)
    previous = ""
    while title and title != previous:
        previous = title
        for pattern in SERIES_GROUPING_PATTERNS:
            title = pattern.sub("", title)
        title = re.sub(r"[_\-.]+", " ", title)
        title = re.sub(r"\s+", " ", title).strip(" ._-")
    return title or normalize_book_title(name)


def title_key(title: str) -> str:
    return re.sub(r"\s+", "", title).casefold()


def extension_text(name: str) -> str:
    return PurePosixPath(name.replace("\\", "/")).suffix.lower()


def decode_zip_member_name(name: str, flag_bits: int) -> str:
    if flag_bits & ZIP_UTF8_FLAG:
        return clean_display_name(name)
    raw_name = name.encode("cp437", errors="replace")
    for encoding in ("cp949", "euc-kr", "utf-8"):
        try:
            decoded = raw_name.decode(encoding)
        except UnicodeDecodeError:
            continue
        if decoded:
            return clean_display_name(decoded)
    return clean_display_name(name)


def title_records_from_files(
    records: list[FileRecord],
    include_zip: bool = True,
    skipped: list[SkippedRecord] | None = None,
    cancel_event: threading.Event | None = None,
) -> list[TitleRecord]:
    title_records: list[TitleRecord] = []
    for record in records:
        check_cancel(cancel_event)
        suffix = record.path.suffix.lower()
        if suffix in BOOK_EXTENSIONS:
            try:
                title = normalize_book_title(record.path.name)
                if title:
                    title_records.append(
                        TitleRecord(
                            title=title,
                            name=record.path.name,
                            extension=extension_text(record.path.name),
                            kind="file",
                            size=record.size,
                            modified=format_mtime(record.modified),
                            location=str(record.path),
                        )
                    )
            except (OSError, UnicodeError, ValueError) as exc:
                if skipped is not None:
                    skipped.append(SkippedRecord(str(record.path), f"파일 제목 스킵: {exc}"))
        if include_zip and suffix in ZIP_EXTENSIONS:
            title_records.extend(title_records_from_zip(record.path, skipped=skipped, cancel_event=cancel_event))
    return title_records


def catalog_records_from_files(
    records: list[FileRecord],
    include_zip: bool = True,
    query: str = "",
    skipped: list[SkippedRecord] | None = None,
    cancel_event: threading.Event | None = None,
) -> list[CatalogRecord]:
    query_key = query.casefold().strip()
    catalog: list[CatalogRecord] = []
    for record in records:
        check_cancel(cancel_event)
        try:
            file_item = CatalogRecord(
                name=record.path.name,
                title=normalize_book_title(record.path.name),
                extension=extension_text(record.path.name),
                kind="file",
                size=record.size,
                modified=format_mtime(record.modified),
                location=str(record.path),
            )
            if record_matches_query(file_item, query_key):
                catalog.append(file_item)
        except (OSError, UnicodeError, ValueError) as exc:
            if skipped is not None:
                skipped.append(SkippedRecord(str(record.path), f"파일 항목 스킵: {exc}"))
        if include_zip and record.path.suffix.lower() in ZIP_EXTENSIONS:
            catalog.extend(catalog_records_from_zip(record.path, query_key, skipped=skipped, cancel_event=cancel_event))
    return catalog


def catalog_records_from_zip(
    path: Path,
    query_key: str = "",
    skipped: list[SkippedRecord] | None = None,
    cancel_event: threading.Event | None = None,
) -> list[CatalogRecord]:
    catalog: list[CatalogRecord] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for index, info in enumerate(archive.infolist()):
                check_cancel(cancel_event)
                if index >= MAX_ZIP_ITEMS_PER_ARCHIVE:
                    if skipped is not None:
                        skipped.append(SkippedRecord(str(path), f"내부 항목 {MAX_ZIP_ITEMS_PER_ARCHIVE}개까지만 확인"))
                    break
                if info.is_dir():
                    continue
                try:
                    decoded_filename = decode_zip_member_name(info.filename, info.flag_bits)
                    inner_path = PurePosixPath(decoded_filename)
                    modified = "%04d-%02d-%02d %02d:%02d" % info.date_time[:5]
                    item = CatalogRecord(
                        name=inner_path.name,
                        title=normalize_book_title(inner_path.name),
                        extension=extension_text(inner_path.name),
                        kind="zip item",
                        size=info.file_size,
                        modified=modified,
                        location=f"{path} :: {decoded_filename}",
                    )
                    if record_matches_query(item, query_key):
                        catalog.append(item)
                except (OSError, UnicodeError, ValueError) as exc:
                    if skipped is not None:
                        skipped.append(SkippedRecord(str(path), f"내부 항목 스킵: {exc}"))
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        if skipped is not None:
            skipped.append(SkippedRecord(str(path), str(exc)))
    return catalog


def record_matches_query(record: CatalogRecord, query_key: str) -> bool:
    if not query_key:
        return True
    return (
        query_key in record.name.casefold()
        or query_key in record.title.casefold()
        or query_key in record.extension.casefold()
        or query_key == record.extension.casefold().lstrip(".")
        or query_key in record.location.casefold()
    )


def title_records_from_zip(
    path: Path,
    skipped: list[SkippedRecord] | None = None,
    cancel_event: threading.Event | None = None,
) -> list[TitleRecord]:
    title_records: list[TitleRecord] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for index, info in enumerate(archive.infolist()):
                check_cancel(cancel_event)
                if index >= MAX_ZIP_ITEMS_PER_ARCHIVE:
                    if skipped is not None:
                        skipped.append(SkippedRecord(str(path), f"내부 항목 {MAX_ZIP_ITEMS_PER_ARCHIVE}개까지만 확인"))
                    break
                if info.is_dir():
                    continue
                try:
                    decoded_filename = decode_zip_member_name(info.filename, info.flag_bits)
                    inner_path = PurePosixPath(decoded_filename)
                    if inner_path.suffix.lower() not in BOOK_EXTENSIONS:
                        continue
                    title = normalize_book_title(inner_path.name)
                    if not title:
                        continue
                    modified = "%04d-%02d-%02d %02d:%02d" % info.date_time[:5]
                    title_records.append(
                        TitleRecord(
                            title=title,
                            name=inner_path.name,
                            extension=extension_text(inner_path.name),
                            kind="zip item",
                            size=info.file_size,
                            modified=modified,
                            location=f"{path} :: {decoded_filename}",
                        )
                    )
                except (OSError, UnicodeError, ValueError) as exc:
                    if skipped is not None:
                        skipped.append(SkippedRecord(str(path), f"내부 항목 스킵: {exc}"))
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        if skipped is not None:
            skipped.append(SkippedRecord(str(path), str(exc)))
    return title_records


def group_title_records(records: list[TitleRecord]) -> dict[str, list[TitleRecord]]:
    groups: dict[str, list[TitleRecord]] = {}
    for record in records:
        groups.setdefault(f"{title_key(record.title)}\0{record.extension.casefold()}", []).append(record)
    return {key: items for key, items in groups.items() if len(items) > 1}


def choose_keep_candidate(items: list[TitleRecord]) -> TitleRecord:
    return max(items, key=lambda item: (item.kind == "file", item.size, -len(item.location)))


def filter_title_groups(groups: dict[str, list[TitleRecord]], query: str) -> dict[str, list[TitleRecord]]:
    query_key = query.casefold().strip()
    if not query_key:
        return groups
    filtered: dict[str, list[TitleRecord]] = {}
    for key, items in groups.items():
        if any(
            query_key in item.title.casefold()
            or query_key in item.name.casefold()
            or query_key in item.extension.casefold()
            or query_key == item.extension.casefold().lstrip(".")
            or query_key in item.location.casefold()
            for item in items
        ):
            filtered[key] = items
    return filtered


def split_name(name: str, keep_extension: bool) -> tuple[str, str]:
    if not keep_extension:
        return name, ""
    stem, extension = os.path.splitext(name)
    return stem, extension


def remove_copy_suffix(stem: str) -> str:
    return COPY_SUFFIX_RE.sub("", stem).strip()


def is_probable_author(value: str) -> bool:
    candidate = re.sub(r"\s+", " ", value).strip().strip("[](){}")
    if not candidate:
        return False
    if len(candidate) > 50:
        return False
    if candidate in AUTHOR_REJECT_WORDS:
        return False
    if re.fullmatch(r"\d+", candidate):
        return False
    return True


def looks_like_underscore_author_title(text: str, title_part: str) -> bool:
    parts = [part for part in text.split("_") if part.strip()]
    if len(parts) < 4:
        return False
    if not re.search(r"\d|권|화|완", title_part):
        return False
    return True


def normalize_rename_title_format(stem: str) -> str:
    numeric_join = "\u0000"
    text = re.sub(r"(?<=\d)_(?=\d)", numeric_join, stem)
    text = text.replace("_", " ")
    text = text.replace(numeric_join, "_")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_author_from_stem(stem: str) -> tuple[str, str]:
    text = unicodedata.normalize("NFC", stem).strip()
    at_source_match = AUTHOR_AT_SOURCE_SUFFIX_RE.match(text)
    if at_source_match:
        author = re.sub(r"\s+", " ", at_source_match.group("author")).strip()
        if is_probable_author(author):
            cleaned = re.sub(r"\s+", " ", at_source_match.group("title")).strip()
            return author, cleaned
    underscore_match = AUTHOR_UNDERSCORE_PREFIX_RE.match(text)
    if underscore_match and looks_like_underscore_author_title(text, underscore_match.group("title")):
        author = re.sub(r"\s+", " ", underscore_match.group("author")).strip()
        if is_probable_author(author):
            return author, underscore_match.group("title").strip()
    for pattern in AUTHOR_MARKER_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        author = re.sub(r"\s+", " ", match.group(1)).strip()
        if not is_probable_author(author):
            continue
        cleaned = (text[: match.start()] + text[match.end() :]).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return author, cleaned
    return "", text


def rename_title_key_from_stem(stem: str, strip_suffix: bool = True) -> str:
    author, cleaned = extract_author_from_stem(stem)
    del author
    if strip_suffix:
        cleaned = remove_copy_suffix(cleaned)
    title = normalize_book_title(cleaned)
    return title_key(title)


def build_author_map(files: list[FileRecord], strip_suffix: bool = True) -> dict[str, str]:
    authors: dict[str, dict[str, int]] = {}
    for record in files:
        stem, _extension = split_name(record.path.name, True)
        author, cleaned = extract_author_from_stem(stem)
        if not author:
            continue
        if strip_suffix:
            cleaned = remove_copy_suffix(cleaned)
        key = title_key(normalize_book_title(cleaned))
        if not key:
            continue
        authors.setdefault(key, {})
        authors[key][author] = authors[key].get(author, 0) + 1
    return {
        key: sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))[0][0]
        for key, counts in authors.items()
    }


def replace_by_position(
    text: str,
    find_text: str,
    replace_text: str,
    use_regex: bool,
    find_position: str,
) -> str:
    if not find_text:
        return text
    if use_regex:
        if find_position == "front":
            pattern = f"^(?:{find_text})"
        elif find_position == "back":
            pattern = f"(?:{find_text})$"
        elif find_position == "exact":
            pattern = f"^(?:{find_text})$"
        else:
            pattern = find_text
        return re.sub(pattern, replace_text, text)

    if find_position == "front":
        return f"{replace_text}{text[len(find_text):]}" if text.startswith(find_text) else text
    if find_position == "back":
        return f"{text[:-len(find_text)]}{replace_text}" if text.endswith(find_text) else text
    if find_position == "exact":
        return replace_text if text == find_text else text
    return text.replace(find_text, replace_text)


def build_new_name(
    original_name: str,
    find_text: str,
    replace_text: str,
    use_regex: bool,
    prefix: str,
    suffix: str,
    case_mode: str,
    start_number: int,
    padding: int,
    index: int,
    keep_extension: bool,
    find_position: str = "anywhere",
    author: str = "",
    strip_copy_suffix: bool = False,
    auto_author: bool = False,
    author_hint: str = "",
    normalize_title_format: bool = True,
    author_pattern: str = "prefix",
) -> str:
    stem, extension = split_name(original_name, keep_extension)
    stem = replace_by_position(stem, find_text, replace_text, use_regex, find_position)
    extracted_author = ""
    if author.strip() or auto_author:
        extracted_author, cleaned_stem = extract_author_from_stem(stem)
        if extracted_author:
            stem = cleaned_stem
    if strip_copy_suffix:
        stem = remove_copy_suffix(stem)
    if normalize_title_format:
        stem = normalize_rename_title_format(stem)
    clean_author = (author.strip() or extracted_author or author_hint).strip().strip("[]")
    if clean_author:
        author_prefix = f"[{clean_author}] "
        author_suffix = f" [{clean_author}]"
        if author_pattern == "suffix":
            if not stem.endswith(author_suffix):
                stem = f"{stem}{author_suffix}"
        elif not stem.startswith(author_prefix):
            stem = f"{author_prefix}{stem}"

    number = start_number + index
    number_text = str(number).zfill(max(1, padding)) if padding > 0 else str(number)
    stem = f"{prefix}{stem}{suffix}"
    if start_number >= 0:
        stem = f"{stem}{number_text}"

    if case_mode == "lower":
        stem = stem.lower()
    elif case_mode == "upper":
        stem = stem.upper()
    elif case_mode == "title":
        stem = stem.title()

    return f"{stem}{extension}"


def generate_rename_plan(
    files: list[FileRecord],
    find_text: str,
    replace_text: str,
    use_regex: bool,
    prefix: str,
    suffix: str,
    case_mode: str,
    start_number: int,
    padding: int,
    keep_extension: bool,
    find_position: str = "anywhere",
    author: str = "",
    strip_copy_suffix: bool = False,
    auto_author: bool = False,
    normalize_title_format: bool = True,
    author_pattern: str = "prefix",
) -> list[RenameEntry]:
    plan: list[RenameEntry] = []
    targets: dict[Path, int] = {}
    author_map = build_author_map(files, strip_copy_suffix) if auto_author else {}

    for index, record in enumerate(files):
        stem, _extension = split_name(record.path.name, True)
        author_hint = ""
        if auto_author and not author.strip():
            author_hint = author_map.get(rename_title_key_from_stem(stem, strip_copy_suffix), "")
        try:
            new_name = build_new_name(
                record.path.name,
                find_text,
                replace_text,
                use_regex,
                prefix,
                suffix,
                case_mode,
                start_number,
                padding,
                index,
                keep_extension,
                find_position,
                author,
                strip_copy_suffix,
                auto_author,
                author_hint,
                normalize_title_format,
                author_pattern,
            )
        except re.error as exc:
            target = record.path
            status = f"regex error: {exc}"
        else:
            target = record.path.with_name(new_name)
            status = "ready" if target != record.path else "unchanged"
        targets[target] = targets.get(target, 0) + 1
        plan.append(RenameEntry(source=record.path, target=target, status=status))

    selected_sources = {entry.source for entry in plan}
    final_plan: list[RenameEntry] = []
    for entry in plan:
        status = entry.status
        if status == "ready":
            if targets[entry.target] > 1:
                status = "target duplicated"
            elif entry.target.exists() and entry.target not in selected_sources:
                status = "target exists"
        final_plan.append(RenameEntry(entry.source, entry.target, status))
    return final_plan


def apply_rename_plan(plan: list[RenameEntry]) -> tuple[int, list[str]]:
    ready_entries = [entry for entry in plan if entry.status == "ready"]
    errors: list[str] = []
    temp_pairs: list[tuple[Path, Path, Path]] = []

    for entry in ready_entries:
        temp_path = unique_temp_path(entry.source)
        try:
            entry.source.rename(temp_path)
            temp_pairs.append((temp_path, entry.target, entry.source))
        except OSError as exc:
            errors.append(f"{entry.source.name}: {exc}")

    applied = 0
    for temp_path, target, original in temp_pairs:
        try:
            temp_path.rename(target)
            applied += 1
        except OSError as exc:
            errors.append(f"{original.name}: {exc}")
            try:
                temp_path.rename(original)
            except OSError:
                errors.append(f"{original.name}: rollback failed")

    return applied, errors


def unique_temp_path(source: Path) -> Path:
    for _ in range(100):
        temp_name = f".rename-tmp-{next(tempfile._get_candidate_names())}{source.suffix}"
        temp_path = source.with_name(temp_name)
        if not temp_path.exists():
            return temp_path
    raise FileExistsError(f"Could not create a temporary name near {source}")
