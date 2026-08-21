from __future__ import annotations

import hashlib
import os
import queue
import re
import tempfile
import threading
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from pathlib import PurePosixPath
from tkinter import (
    BOTH,
    END,
    LEFT,
    RIGHT,
    X,
    BooleanVar,
    Button,
    Checkbutton,
    Entry,
    Frame,
    Label,
    LabelFrame,
    StringVar,
    Tk,
    filedialog,
    messagebox,
    ttk,
)


CHUNK_SIZE = 1024 * 1024
DEFAULT_DISPLAY_LIMIT = 2000
MAX_ZIP_ITEMS_PER_ARCHIVE = 2000
ZIP_UTF8_FLAG = 0x800
APP_BG = "#f7f9fc"
PANEL_BG = "#ffffff"
TEXT_COLOR = "#191f28"
MUTED_COLOR = "#6b7684"
BLUE = "#3182f6"
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
                    records.append(FileRecord(path=Path(entry.path), size=stat.st_size, modified=stat.st_mtime))
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
) -> dict[str, list[FileRecord]]:
    size_groups = group_by_size(records, min_size, cancel_event)
    hash_groups: dict[str, list[FileRecord]] = {}
    for items in size_groups.values():
        for record in items:
            check_cancel(cancel_event)
            try:
                file_hash = hash_file(record.path, cancel_event)
            except OSError:
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
    stem = re.sub(r"[_\-.]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem)
    return stem.strip(" ._-")


def title_key(title: str) -> str:
    return re.sub(r"\s+", " ", title).casefold()


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
                    title_records.append(
                        TitleRecord(
                            title=title,
                            name=inner_path.name,
                            extension=extension_text(inner_path.name),
                            kind="zip item",
                            size=info.file_size,
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
        groups.setdefault(title_key(record.title), []).append(record)
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
) -> str:
    stem, extension = split_name(original_name, keep_extension)
    stem = replace_by_position(stem, find_text, replace_text, use_regex, find_position)

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
) -> list[RenameEntry]:
    plan: list[RenameEntry] = []
    targets: dict[Path, int] = {}

    for index, record in enumerate(files):
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


class FileTidierApp(Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("File Tidier - Duplicate Finder & Renamer")
        self.geometry("1180x760")
        self.minsize(980, 620)
        self.configure(bg=APP_BG)

        self.folder_var = StringVar()
        self.recursive_var = BooleanVar(value=True)
        self.include_zip_var = BooleanVar(value=False)
        self.min_size_var = StringVar(value="1")
        self.display_limit_var = StringVar(value=str(DEFAULT_DISPLAY_LIMIT))
        self.catalog_search_var = StringVar()
        self.title_filter_var = StringVar()
        self.preview_name_var = StringVar(value="[작가](아이)aaa-01.zip")
        self.preview_title_var = StringVar(value="")
        self.status_var = StringVar(value="폴더를 선택하세요.")
        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.records: list[FileRecord] = []
        self.last_title_groups: dict[str, list[TitleRecord]] = {}
        self.last_skipped_records: list[SkippedRecord] = []
        self.cancel_event: threading.Event | None = None
        self.loading_jobs = 0

        self._configure_style()
        self._build_ui()
        self.after(100, self._poll_queue)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        preferred_theme = "vista" if "vista" in style.theme_names() else "clam"
        style.theme_use(preferred_theme)
        self.option_add("*Font", ("Malgun Gothic", 10))
        style.configure(".", font=("Malgun Gothic", 10), background=APP_BG, foreground=TEXT_COLOR)
        style.configure("TNotebook", background=APP_BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(22, 11), background=APP_BG, foreground=MUTED_COLOR)
        style.map(
            "TNotebook.Tab",
            background=[("selected", PANEL_BG)],
            foreground=[("selected", TEXT_COLOR)],
        )
        style.configure(
            "Treeview",
            background=PANEL_BG,
            fieldbackground=PANEL_BG,
            foreground=TEXT_COLOR,
            rowheight=34,
            borderwidth=0,
            relief="flat",
        )
        style.configure(
            "Treeview.Heading",
            background="#f2f4f6",
            foreground=MUTED_COLOR,
            font=("Malgun Gothic", 10, "bold"),
            relief="flat",
        )
        style.map("Treeview", background=[("selected", "#e8f2ff")], foreground=[("selected", TEXT_COLOR)])
        style.configure("TButton", padding=(14, 9), background=PANEL_BG, foreground=TEXT_COLOR, borderwidth=0)
        style.configure("Accent.TButton", padding=(16, 9), background=BLUE, foreground="#ffffff", borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#1c6fe8")])
        style.configure("TCheckbutton", background=APP_BG, foreground=TEXT_COLOR)
        style.configure("TLabelframe", background=PANEL_BG, borderwidth=0, relief="flat")
        style.configure("TLabelframe.Label", background=PANEL_BG, foreground=MUTED_COLOR)
        style.configure("Horizontal.TProgressbar", background=BLUE, troughcolor="#e5e8eb", bordercolor="#e5e8eb")

    def _build_ui(self) -> None:
        top = Frame(self, padx=14, pady=12, bg=APP_BG)
        top.pack(fill=X)

        Label(top, text="폴더", bg=APP_BG, fg=MUTED_COLOR).pack(side=LEFT)
        Entry(top, textvariable=self.folder_var).pack(side=LEFT, fill=X, expand=True, padx=8)
        ttk.Button(top, text="찾기", command=self.choose_folder, style="Accent.TButton").pack(side=LEFT)

        options = Frame(self, padx=14, bg=APP_BG)
        options.pack(fill=X)
        ttk.Checkbutton(options, text="하위 폴더 포함", variable=self.recursive_var).pack(side=LEFT)
        ttk.Checkbutton(options, text="zip 내부 포함", variable=self.include_zip_var).pack(side=LEFT, padx=(12, 0))
        Label(options, text="최소 크기 KB", bg=APP_BG, fg=MUTED_COLOR).pack(side=LEFT, padx=(18, 6))
        Entry(options, textvariable=self.min_size_var, width=8).pack(side=LEFT)
        Label(options, text="표시 최대", bg=APP_BG, fg=MUTED_COLOR).pack(side=LEFT, padx=(18, 6))
        Entry(options, textvariable=self.display_limit_var, width=8).pack(side=LEFT)
        ttk.Button(options, text="중지", command=self.cancel_scan).pack(side=LEFT, padx=(12, 0))
        Label(options, textvariable=self.status_var, anchor="w", bg=APP_BG, fg=MUTED_COLOR).pack(side=RIGHT)
        self.loading_bar = ttk.Progressbar(options, mode="indeterminate", length=110)
        self.loading_bar.pack(side=RIGHT, padx=(0, 12))
        self.loading_bar.pack_forget()

        notebook = ttk.Notebook(self)
        notebook.pack(fill=BOTH, expand=True, padx=14, pady=12)

        catalog_tab = Frame(notebook)
        duplicate_tab = Frame(notebook)
        title_tab = Frame(notebook)
        skipped_tab = Frame(notebook)
        rename_tab = Frame(notebook)
        notebook.add(catalog_tab, text="파일 목록")
        notebook.add(duplicate_tab, text="중복 찾기")
        notebook.add(title_tab, text="제목 중복 확인")
        notebook.add(skipped_tab, text="스킵됨")
        notebook.add(rename_tab, text="이름 바꾸기")

        self._build_catalog_tab(catalog_tab)
        self._build_duplicate_tab(duplicate_tab)
        self._build_title_tab(title_tab)
        self._build_skipped_tab(skipped_tab)
        self._build_rename_tab(rename_tab)

    def _build_catalog_tab(self, parent: Frame) -> None:
        parent.configure(bg=APP_BG)
        actions = Frame(parent, pady=8, bg=APP_BG)
        actions.pack(fill=X)
        Label(actions, text="검색", bg=APP_BG, fg=MUTED_COLOR).pack(side=LEFT)
        Entry(actions, textvariable=self.catalog_search_var, width=28).pack(side=LEFT, padx=6)
        ttk.Button(actions, text="목록/제목 뽑기", command=self.scan_catalog, style="Accent.TButton").pack(side=LEFT)
        ttk.Button(actions, text="선택 경로 복사", command=self.copy_selected_catalog_path).pack(side=LEFT, padx=8)

        preview = Frame(parent, pady=6, bg=APP_BG)
        preview.pack(fill=X)
        Label(preview, text="제목 규칙 미리보기", bg=APP_BG, fg=MUTED_COLOR).pack(side=LEFT)
        preview_entry = Entry(preview, textvariable=self.preview_name_var, width=34)
        preview_entry.pack(side=LEFT, padx=6)
        Label(preview, textvariable=self.preview_title_var, bg=APP_BG, fg=TEXT_COLOR).pack(side=LEFT, padx=8)
        preview_entry.bind("<KeyRelease>", lambda _event: self.update_title_preview())
        self.update_title_preview()

        columns = ("name", "title", "extension", "kind", "size", "modified", "location")
        self.catalog_tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="extended")
        headings = {
            "name": "이름",
            "title": "뽑은 제목",
            "extension": "확장자",
            "kind": "종류",
            "size": "크기",
            "modified": "수정일",
            "location": "위치",
        }
        widths = {"name": 220, "title": 200, "extension": 80, "kind": 90, "size": 90, "modified": 130, "location": 430}
        for column in columns:
            self.catalog_tree.heading(column, text=headings[column])
            self.catalog_tree.column(column, width=widths[column], anchor="w")
        self.attach_tree_scrollbars(parent, self.catalog_tree)

    def _build_duplicate_tab(self, parent: Frame) -> None:
        parent.configure(bg=APP_BG)
        actions = Frame(parent, pady=8, bg=APP_BG)
        actions.pack(fill=X)
        ttk.Button(actions, text="크기 같은 후보 찾기", command=self.scan_size_candidates, style="Accent.TButton").pack(side=LEFT)
        ttk.Button(actions, text="내용까지 같은 중복 찾기", command=self.scan_exact_duplicates).pack(side=LEFT, padx=8)
        ttk.Button(actions, text="선택 경로 복사", command=self.copy_selected_duplicate_path).pack(side=LEFT)

        columns = ("group", "name", "extension", "size", "modified", "hash", "path")
        self.duplicate_tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="extended")
        headings = {
            "group": "그룹",
            "name": "파일명",
            "extension": "확장자",
            "size": "크기",
            "modified": "수정일",
            "hash": "SHA-256",
            "path": "경로",
        }
        widths = {"group": 70, "name": 220, "extension": 80, "size": 90, "modified": 130, "hash": 170, "path": 410}
        for column in columns:
            self.duplicate_tree.heading(column, text=headings[column])
            self.duplicate_tree.column(column, width=widths[column], anchor="w")
        self.attach_tree_scrollbars(parent, self.duplicate_tree)

    def _build_title_tab(self, parent: Frame) -> None:
        parent.configure(bg=APP_BG)
        actions = Frame(parent, pady=8, bg=APP_BG)
        actions.pack(fill=X)
        ttk.Button(actions, text="제목 중복 찾기", command=self.scan_title_groups, style="Accent.TButton").pack(side=LEFT)
        ttk.Button(actions, text="선택 경로 복사", command=self.copy_selected_title_path).pack(side=LEFT, padx=8)
        Label(actions, text="필터", bg=APP_BG, fg=MUTED_COLOR).pack(side=LEFT, padx=(18, 6))
        Entry(actions, textvariable=self.title_filter_var, width=24).pack(side=LEFT)
        ttk.Button(actions, text="필터 적용", command=self.refresh_title_groups).pack(side=LEFT, padx=8)

        columns = ("group", "keep", "title", "name", "extension", "kind", "size", "location")
        self.title_tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="extended")
        headings = {
            "group": "그룹",
            "keep": "남길 후보",
            "title": "뽑은 제목",
            "name": "원래 이름",
            "extension": "확장자",
            "kind": "종류",
            "size": "크기",
            "location": "위치",
        }
        widths = {"group": 70, "keep": 90, "title": 220, "name": 250, "extension": 80, "kind": 90, "size": 90, "location": 470}
        for column in columns:
            self.title_tree.heading(column, text=headings[column])
            self.title_tree.column(column, width=widths[column], anchor="w")
        self.attach_tree_scrollbars(parent, self.title_tree)

    def _build_skipped_tab(self, parent: Frame) -> None:
        parent.configure(bg=APP_BG)
        actions = Frame(parent, pady=8, bg=APP_BG)
        actions.pack(fill=X)
        ttk.Button(actions, text="선택 경로 복사", command=self.copy_selected_skipped_path).pack(side=LEFT)

        columns = ("path", "reason")
        self.skipped_tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="extended")
        headings = {"path": "압축파일", "reason": "스킵 이유"}
        widths = {"path": 540, "reason": 520}
        for column in columns:
            self.skipped_tree.heading(column, text=headings[column])
            self.skipped_tree.column(column, width=widths[column], anchor="w")
        self.attach_tree_scrollbars(parent, self.skipped_tree)

    def _build_rename_tab(self, parent: Frame) -> None:
        parent.configure(bg=APP_BG)
        controls = ttk.LabelFrame(parent, text="규칙", padding=(14, 12))
        controls.pack(fill=X, pady=(6, 8))

        self.find_var = StringVar()
        self.replace_var = StringVar()
        self.find_position_var = StringVar(value="앞")
        self.regex_var = BooleanVar(value=False)
        self.prefix_var = StringVar()
        self.suffix_var = StringVar()
        self.case_var = StringVar(value="keep")
        self.start_var = StringVar(value="-1")
        self.padding_var = StringVar(value="3")
        self.keep_ext_var = BooleanVar(value=True)

        row1 = Frame(controls)
        row1.pack(fill=X, pady=2)
        Label(row1, text="찾기").pack(side=LEFT)
        Entry(row1, textvariable=self.find_var, width=20).pack(side=LEFT, padx=6)
        Label(row1, text="바꾸기").pack(side=LEFT)
        Entry(row1, textvariable=self.replace_var, width=20).pack(side=LEFT, padx=6)
        Label(row1, text="위치").pack(side=LEFT)
        ttk.Combobox(
            row1,
            textvariable=self.find_position_var,
            values=("앞", "뒤", "포함", "전체"),
            width=7,
            state="readonly",
        ).pack(side=LEFT, padx=6)
        ttk.Checkbutton(row1, text="정규식", variable=self.regex_var).pack(side=LEFT, padx=8)
        ttk.Checkbutton(row1, text="확장자 유지", variable=self.keep_ext_var).pack(side=LEFT)

        row2 = Frame(controls)
        row2.pack(fill=X, pady=2)
        Label(row2, text="앞에 붙임").pack(side=LEFT)
        Entry(row2, textvariable=self.prefix_var, width=18).pack(side=LEFT, padx=6)
        Label(row2, text="뒤에 붙임").pack(side=LEFT)
        Entry(row2, textvariable=self.suffix_var, width=18).pack(side=LEFT, padx=6)
        Label(row2, text="번호 시작(-1이면 끔)").pack(side=LEFT)
        Entry(row2, textvariable=self.start_var, width=8).pack(side=LEFT, padx=6)
        Label(row2, text="자릿수").pack(side=LEFT)
        Entry(row2, textvariable=self.padding_var, width=6).pack(side=LEFT, padx=6)
        ttk.Combobox(
            row2,
            textvariable=self.case_var,
            values=("keep", "lower", "upper", "title"),
            width=8,
            state="readonly",
        ).pack(side=LEFT, padx=8)

        actions = Frame(parent, bg=APP_BG)
        actions.pack(fill=X)
        ttk.Button(actions, text="파일 불러오기", command=self.load_files_for_rename, style="Accent.TButton").pack(side=LEFT)
        ttk.Button(actions, text="미리보기", command=self.preview_rename).pack(side=LEFT, padx=8)
        ttk.Button(actions, text="적용", command=self.apply_rename).pack(side=LEFT)

        columns = ("old", "new", "status", "path")
        self.rename_tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="extended")
        headings = {"old": "현재 이름", "new": "새 이름", "status": "상태", "path": "폴더"}
        widths = {"old": 260, "new": 260, "status": 140, "path": 430}
        for column in columns:
            self.rename_tree.heading(column, text=headings[column])
            self.rename_tree.column(column, width=widths[column], anchor="w")
        self.attach_tree_scrollbars(parent, self.rename_tree, pady=(8, 0))

    @staticmethod
    def attach_tree_scrollbars(parent: Frame, tree: ttk.Treeview, pady: tuple[int, int] | int = 0) -> None:
        for column in tree["columns"]:
            text = tree.heading(column).get("text", column)
            tree.heading(column, text=text, command=lambda col=column: FileTidierApp.sort_treeview(tree, col, False))
        y_scroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        x_scroll = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        x_scroll.pack(side="bottom", fill=X)
        y_scroll.pack(side=RIGHT, fill="y", pady=pady)
        tree.pack(side=LEFT, fill=BOTH, expand=True, pady=pady)

    @staticmethod
    def sort_treeview(tree: ttk.Treeview, column: str, reverse: bool) -> None:
        rows = [(tree.set(item, column), item) for item in tree.get_children("")]
        rows.sort(key=lambda pair: FileTidierApp.sort_value(pair[0]), reverse=reverse)
        for index, (_value, item) in enumerate(rows):
            tree.move(item, "", index)
        text = tree.heading(column).get("text", column)
        tree.heading(column, text=text, command=lambda: FileTidierApp.sort_treeview(tree, column, not reverse))

    @staticmethod
    def sort_value(value: str) -> tuple[int, object]:
        size_match = re.fullmatch(r"([\d.]+)\s*(B|KB|MB|GB|TB)", value)
        if size_match:
            multiplier = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}[size_match.group(2)]
            return (0, float(size_match.group(1)) * multiplier)
        try:
            return (0, float(value))
        except ValueError:
            return (1, value.casefold())

    def choose_folder(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self.folder_var.set(folder)
            self.status_var.set("폴더가 선택되었습니다.")

    def selected_folder(self) -> Path | None:
        folder_text = self.folder_var.get().strip()
        if not folder_text:
            messagebox.showwarning("폴더 필요", "먼저 폴더를 선택하세요.")
            return None
        folder = Path(folder_text)
        if not folder.exists() or not folder.is_dir():
            messagebox.showerror("폴더 오류", "선택한 경로가 폴더가 아닙니다.")
            return None
        return folder

    def min_size_bytes(self) -> int:
        try:
            return max(0, int(float(self.min_size_var.get()) * 1024))
        except ValueError:
            return 0

    def display_limit(self) -> int:
        try:
            return max(0, int(self.display_limit_var.get()))
        except ValueError:
            return DEFAULT_DISPLAY_LIMIT

    def start_loading(self, message: str) -> None:
        self.loading_jobs += 1
        self.status_var.set(message)
        self.loading_bar.pack(side=RIGHT, padx=(0, 12))
        self.loading_bar.start(12)

    def begin_scan(self, message: str) -> threading.Event:
        self.cancel_event = threading.Event()
        self.start_loading(message)
        return self.cancel_event

    def stop_loading(self) -> None:
        self.loading_jobs = max(0, self.loading_jobs - 1)
        if self.loading_jobs == 0:
            self.loading_bar.stop()
            self.loading_bar.pack_forget()

    def cancel_scan(self) -> None:
        if self.cancel_event is not None:
            self.cancel_event.set()
            self.status_var.set("중지 요청됨...")

    def load_records(self) -> list[FileRecord] | None:
        folder = self.selected_folder()
        if folder is None:
            return None
        self.records = iter_files(folder, self.recursive_var.get())
        return self.records

    def scan_catalog(self) -> None:
        folder = self.selected_folder()
        if folder is None:
            return
        zip_text = "zip 내부까지" if self.include_zip_var.get() else "바깥 파일만"
        cancel_event = self.begin_scan(f"파일 목록을 읽는 중... ({zip_text})")
        self.clear_tree(self.catalog_tree)
        threading.Thread(
            target=self._scan_catalog_worker,
            args=(folder, self.recursive_var.get(), self.catalog_search_var.get(), self.include_zip_var.get(), cancel_event),
            daemon=True,
        ).start()

    def scan_size_candidates(self) -> None:
        folder = self.selected_folder()
        if folder is None:
            return
        min_size = self.min_size_bytes()
        cancel_event = self.begin_scan("크기 기준으로 후보를 찾는 중...")
        self.clear_tree(self.duplicate_tree)
        threading.Thread(
            target=self._scan_size_worker,
            args=(folder, self.recursive_var.get(), min_size, cancel_event),
            daemon=True,
        ).start()

    def scan_exact_duplicates(self) -> None:
        folder = self.selected_folder()
        if folder is None:
            return
        min_size = self.min_size_bytes()
        cancel_event = self.begin_scan("내용 해시를 계산하는 중...")
        self.clear_tree(self.duplicate_tree)
        threading.Thread(
            target=self._scan_hash_worker,
            args=(folder, self.recursive_var.get(), min_size, cancel_event),
            daemon=True,
        ).start()

    def scan_title_groups(self) -> None:
        folder = self.selected_folder()
        if folder is None:
            return
        zip_text = "zip 내부까지" if self.include_zip_var.get() else "바깥 파일만"
        cancel_event = self.begin_scan(f"제목 중복을 찾는 중... ({zip_text})")
        self.clear_tree(self.title_tree)
        threading.Thread(
            target=self._scan_title_worker,
            args=(folder, self.recursive_var.get(), self.include_zip_var.get(), cancel_event),
            daemon=True,
        ).start()

    def _scan_size_worker(self, folder: Path, recursive: bool, min_size: int, cancel_event: threading.Event) -> None:
        try:
            records = iter_files(folder, recursive, cancel_event)
            groups = group_by_size(records, min_size, cancel_event)
        except ScanCancelled:
            self.queue.put(("cancelled", "크기 비교를 중지했습니다."))
        except Exception as exc:
            self.queue.put(("error", f"크기 비교 실패: {exc}"))
        else:
            self.queue.put(("size_groups", groups))

    def _scan_catalog_worker(
        self,
        folder: Path,
        recursive: bool,
        query: str,
        include_zip: bool,
        cancel_event: threading.Event,
    ) -> None:
        skipped: list[SkippedRecord] = []
        try:
            records = iter_files(folder, recursive, cancel_event)
            self.queue.put(("status", f"파일 {len(records)}개 확인 중..."))
            catalog = catalog_records_from_files(
                records,
                include_zip=include_zip,
                query=query,
                skipped=skipped,
                cancel_event=cancel_event,
            )
        except ScanCancelled:
            self.queue.put(("cancelled", "파일 목록 작업을 중지했습니다."))
        except Exception as exc:
            self.queue.put(("error", f"파일 목록 읽기 실패: {exc}"))
        else:
            self.queue.put(("catalog", (catalog, skipped)))

    def _scan_hash_worker(self, folder: Path, recursive: bool, min_size: int, cancel_event: threading.Event) -> None:
        try:
            records = iter_files(folder, recursive, cancel_event)
            groups = group_by_content(records, min_size, cancel_event)
        except ScanCancelled:
            self.queue.put(("cancelled", "내용 비교를 중지했습니다."))
        except Exception as exc:
            self.queue.put(("error", f"내용 비교 실패: {exc}"))
        else:
            self.queue.put(("hash_groups", groups))

    def _scan_title_worker(self, folder: Path, recursive: bool, include_zip: bool, cancel_event: threading.Event) -> None:
        skipped: list[SkippedRecord] = []
        try:
            records = iter_files(folder, recursive, cancel_event)
            self.queue.put(("status", f"파일 {len(records)}개에서 제목 확인 중..."))
            title_records = title_records_from_files(
                records,
                include_zip=include_zip,
                skipped=skipped,
                cancel_event=cancel_event,
            )
            groups = group_title_records(title_records)
        except ScanCancelled:
            self.queue.put(("cancelled", "제목 중복 확인을 중지했습니다."))
        except Exception as exc:
            self.queue.put(("error", f"제목 중복 확인 실패: {exc}"))
        else:
            self.queue.put(("title_groups", (groups, skipped)))

    def _poll_queue(self) -> None:
        try:
            while True:
                event, payload = self.queue.get_nowait()
                if event == "catalog":
                    catalog, skipped = payload  # type: ignore[misc]
                    self.show_catalog(catalog)
                    self.show_skipped(skipped)
                    self.stop_loading()
                elif event == "status":
                    self.status_var.set(str(payload))
                elif event == "error":
                    self.status_var.set(str(payload))
                    self.stop_loading()
                elif event == "cancelled":
                    self.status_var.set(str(payload))
                    self.stop_loading()
                elif event == "size_groups":
                    self.show_size_groups(payload)  # type: ignore[arg-type]
                    self.stop_loading()
                elif event == "hash_groups":
                    self.show_hash_groups(payload)  # type: ignore[arg-type]
                    self.stop_loading()
                elif event == "title_groups":
                    groups, skipped = payload  # type: ignore[misc]
                    self.last_title_groups = groups
                    self.refresh_title_groups()
                    self.show_skipped(skipped)
                    self.stop_loading()
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def show_catalog(self, catalog: list[CatalogRecord]) -> None:
        self.clear_tree(self.catalog_tree)
        limit = self.display_limit()
        sorted_catalog = sorted(catalog, key=lambda item: item.location.lower())
        visible_catalog = sorted_catalog[:limit] if limit else sorted_catalog
        for record in visible_catalog:
            self.catalog_tree.insert(
                "",
                END,
                values=(
                    record.name,
                    record.title,
                    record.extension,
                    record.kind,
                    format_size(record.size),
                    record.modified,
                    record.location,
                ),
            )
        query = self.catalog_search_var.get().strip()
        query_text = f" 검색어 '{query}'" if query else ""
        limit_text = f" / 표시 {len(visible_catalog)}개" if len(visible_catalog) < len(catalog) else ""
        self.status_var.set(f"파일 목록{query_text}: 전체 {len(catalog)}개{limit_text}")

    def show_skipped(self, skipped: list[SkippedRecord]) -> None:
        self.last_skipped_records = skipped
        self.clear_tree(self.skipped_tree)
        for record in skipped:
            self.skipped_tree.insert("", END, values=(record.path, record.reason))

    def show_size_groups(self, groups: dict[int, list[FileRecord]]) -> None:
        self.clear_tree(self.duplicate_tree)
        count = 0
        shown = 0
        limit = self.display_limit()
        for group_index, (size, items) in enumerate(sorted(groups.items()), start=1):
            for record in items:
                if limit and shown >= limit:
                    count += 1
                    continue
                self.duplicate_tree.insert(
                    "",
                    END,
                    values=(
                        group_index,
                        record.path.name,
                        extension_text(record.path.name),
                        format_size(size),
                        format_mtime(record.modified),
                        "",
                        str(record.path),
                    ),
                )
                count += 1
                shown += 1
        limit_text = f" / 표시 {shown}개" if shown < count else ""
        self.status_var.set(f"크기 후보 {len(groups)}그룹, 파일 {count}개{limit_text}")

    def show_hash_groups(self, groups: dict[str, list[FileRecord]]) -> None:
        self.clear_tree(self.duplicate_tree)
        count = 0
        shown = 0
        limit = self.display_limit()
        for group_index, (file_hash, items) in enumerate(sorted(groups.items()), start=1):
            for record in items:
                if limit and shown >= limit:
                    count += 1
                    continue
                self.duplicate_tree.insert(
                    "",
                    END,
                    values=(
                        group_index,
                        record.path.name,
                        extension_text(record.path.name),
                        format_size(record.size),
                        format_mtime(record.modified),
                        file_hash[:16],
                        str(record.path),
                    ),
                )
                count += 1
                shown += 1
        limit_text = f" / 표시 {shown}개" if shown < count else ""
        self.status_var.set(f"확정 중복 {len(groups)}그룹, 파일 {count}개{limit_text}")

    def refresh_title_groups(self) -> None:
        self.show_title_groups(filter_title_groups(self.last_title_groups, self.title_filter_var.get()))

    def show_title_groups(self, groups: dict[str, list[TitleRecord]]) -> None:
        self.clear_tree(self.title_tree)
        count = 0
        shown = 0
        limit = self.display_limit()
        sorted_groups = sorted(groups.values(), key=lambda items: title_key(items[0].title))
        for group_index, items in enumerate(sorted_groups, start=1):
            keep_candidate = choose_keep_candidate(items)
            sorted_items = sorted(items, key=lambda item: (item.location != keep_candidate.location, item.location.lower()))
            for record in sorted_items:
                if limit and shown >= limit:
                    count += 1
                    continue
                self.title_tree.insert(
                    "",
                    END,
                    values=(
                        group_index,
                        "남김" if record.location == keep_candidate.location else "중복",
                        record.title,
                        record.name,
                        record.extension,
                        record.kind,
                        format_size(record.size),
                        record.location,
                    ),
                )
                count += 1
                shown += 1
        limit_text = f" / 표시 {shown}개" if shown < count else ""
        self.status_var.set(f"제목 중복 의심 {len(groups)}건, 항목 {count}개{limit_text}")

    def copy_selected_duplicate_path(self) -> None:
        selected = self.duplicate_tree.selection()
        if not selected:
            return
        paths = [self.duplicate_tree.item(item, "values")[-1] for item in selected]
        self.clipboard_clear()
        self.clipboard_append("\n".join(paths))
        self.status_var.set(f"경로 {len(paths)}개를 복사했습니다.")

    def copy_selected_catalog_path(self) -> None:
        selected = self.catalog_tree.selection()
        if not selected:
            return
        locations = [self.catalog_tree.item(item, "values")[-1] for item in selected]
        self.clipboard_clear()
        self.clipboard_append("\n".join(locations))
        self.status_var.set(f"위치 {len(locations)}개를 복사했습니다.")

    def copy_selected_title_path(self) -> None:
        selected = self.title_tree.selection()
        if not selected:
            return
        locations = [self.title_tree.item(item, "values")[-1] for item in selected]
        self.clipboard_clear()
        self.clipboard_append("\n".join(locations))
        self.status_var.set(f"위치 {len(locations)}개를 복사했습니다.")

    def copy_selected_skipped_path(self) -> None:
        selected = self.skipped_tree.selection()
        if not selected:
            return
        paths = [self.skipped_tree.item(item, "values")[0] for item in selected]
        self.clipboard_clear()
        self.clipboard_append("\n".join(paths))
        self.status_var.set(f"스킵된 압축파일 {len(paths)}개 경로를 복사했습니다.")

    def update_title_preview(self) -> None:
        title = normalize_book_title(self.preview_name_var.get())
        self.preview_title_var.set(f"→ {title or '(제목 없음)'}")

    def load_files_for_rename(self) -> None:
        records = self.load_records()
        if records is None:
            return
        self.records = records
        self.preview_rename()

    def find_position_key(self) -> str:
        return {
            "앞": "front",
            "뒤": "back",
            "포함": "anywhere",
            "전체": "exact",
        }.get(self.find_position_var.get(), "anywhere")

    def rename_options(self) -> tuple[str, str, bool, str, str, str, int, int, bool, str] | None:
        try:
            start_number = int(self.start_var.get())
            padding = int(self.padding_var.get())
        except ValueError:
            messagebox.showerror("입력 오류", "번호 시작과 자릿수는 숫자로 입력하세요.")
            return None
        return (
            self.find_var.get(),
            self.replace_var.get(),
            self.regex_var.get(),
            self.prefix_var.get(),
            self.suffix_var.get(),
            self.case_var.get(),
            start_number,
            max(0, padding),
            self.keep_ext_var.get(),
            self.find_position_key(),
        )

    def current_plan(self) -> list[RenameEntry]:
        options = self.rename_options()
        if options is None:
            return []
        return generate_rename_plan(self.records, *options)

    def preview_rename(self) -> None:
        if not self.records:
            records = self.load_records()
            if records is None:
                return
        plan = self.current_plan()
        self.clear_tree(self.rename_tree)
        for entry in plan:
            self.rename_tree.insert(
                "",
                END,
                values=(entry.source.name, entry.target.name, entry.status, str(entry.source.parent)),
            )
        ready_count = sum(1 for entry in plan if entry.status == "ready")
        self.status_var.set(f"미리보기 완료: 적용 가능 {ready_count}개 / 전체 {len(plan)}개")

    def apply_rename(self) -> None:
        plan = self.current_plan()
        ready_count = sum(1 for entry in plan if entry.status == "ready")
        if ready_count == 0:
            messagebox.showinfo("적용할 항목 없음", "ready 상태인 파일이 없습니다.")
            return
        if not messagebox.askyesno("이름 변경 확인", f"{ready_count}개 파일 이름을 변경할까요?"):
            return
        applied, errors = apply_rename_plan(plan)
        self.records = []
        self.load_files_for_rename()
        if errors:
            messagebox.showwarning("일부 실패", "\n".join(errors[:10]))
        self.status_var.set(f"이름 변경 완료: {applied}개")

    @staticmethod
    def clear_tree(tree: ttk.Treeview) -> None:
        for item in tree.get_children():
            tree.delete(item)


if __name__ == "__main__":
    app = FileTidierApp()
    app.mainloop()
