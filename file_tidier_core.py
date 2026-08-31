from __future__ import annotations

import errno
import hashlib
import os
import re
import tempfile
import threading
import unicodedata
import zipfile
import zlib
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
# 훑을 이유가 없는 폴더들. 여기 들어가면 파일을 열게 되고, 그때마다
# 백신 검사와 클라우드 동기화가 따라 붙어 부하가 몇 배로 뛴다.
# _FileTidier_Quarantine 은 이 프로그램이 만든 격리 폴더다. 빼지 않으면
# 방금 격리한 파일을 다음 스캔에서 다시 집어 온다.
DEFAULT_SKIP_FOLDERS = frozenset({
    "_filetidier_quarantine",
    "$recycle.bin",
    "system volume information",
    ".dropbox.cache",
    ".git",
    "__pycache__",
    "node_modules",
    ".thumb-cache",
})
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


class DiskTroubleError(Exception):
    """읽기 실패가 장치 고장처럼 보일 때 스캔을 멈추려고 던진다."""

    def __init__(self, message: str, failures: list[tuple[str, str]], consecutive: int) -> None:
        super().__init__(message)
        self.failures = failures
        self.consecutive = consecutive


# 윈도우가 장치 이상에 쓰는 코드. 권한 없음·파일 잠김 같은 흔한 실패와
# 반드시 구분해야 한다 - 안 그러면 멀쩡한 디스크에서 스캔이 멈춘다.
DEVICE_WINERROR = {
    21,    # ERROR_NOT_READY            장치가 준비되지 않음
    23,    # ERROR_CRC                  읽기 검사 오류 - 배드 섹터의 전형
    27,    # ERROR_SECTOR_NOT_FOUND     섹터를 찾을 수 없음
    121,   # ERROR_SEM_TIMEOUT          장치가 제때 응답하지 않음
    433,   # ERROR_NO_SUCH_DEVICE       장치가 사라짐
    1117,  # ERROR_IO_DEVICE            I/O 장치 오류
    1127,  # ERROR_DISK_OPERATION_FAILED  재시도 후에도 실패
    1167,  # ERROR_DEVICE_NOT_CONNECTED  장치 연결 끊김
}
DEVICE_ERRNO = {errno.EIO, errno.ENODEV, errno.ENXIO}


def classify_io_error(exc: BaseException) -> str:
    """읽기 실패를 '장치 이상(device)'과 '흔한 실패(benign)'로 나눈다.

    권한 없음, 스캔 도중 파일이 사라짐, 다른 프로그램이 잠금 - 이런 것은
    큰 폴더를 훑으면 늘 몇 건씩 나온다. 이것까지 세면 멀쩡한 디스크에서
    스캔이 멈춘다.
    """
    if not isinstance(exc, OSError):
        return "benign"
    winerror = getattr(exc, "winerror", None)
    if winerror is not None and int(winerror) in DEVICE_WINERROR:
        return "device"
    if exc.errno in DEVICE_ERRNO:
        return "device"
    return "benign"


class IOHealthMonitor:
    """장치 읽기 오류가 쌓이면 스캔을 멈춘다.

    주로 보는 것은 '연속' 실패다. 죽어 가는 디스크는 한 자리에서 계속
    실패하지 드문드문 실패하지 않는다. 총합 한도는 오류가 넓게 흩어진
    경우를 위한 보조 장치다.

    한도를 0으로 주면 그 판정은 꺼진다.
    """

    def __init__(self, consecutive_limit: int = 3, total_limit: int = 12) -> None:
        self.consecutive_limit = max(0, int(consecutive_limit))
        self.total_limit = max(0, int(total_limit))
        self.device_failures: list[tuple[str, str]] = []
        self.benign_failures = 0
        self.consecutive = 0
        self.tripped = False

    @property
    def enabled(self) -> bool:
        return bool(self.consecutive_limit or self.total_limit)

    def record_success(self) -> None:
        self.consecutive = 0

    def record_failure(self, path, exc: BaseException) -> str:
        """실패를 세고, 장치 이상으로 보이면 DiskTroubleError 를 던진다."""
        kind = classify_io_error(exc)
        if kind != "device":
            self.benign_failures += 1
            # 흔한 실패는 연속 횟수를 건드리지 않는다. 배드 섹터 사이에
            # 권한 오류가 한 번 끼었다고 해서 흐름이 끊긴 것은 아니다.
            return kind
        self.consecutive += 1
        self.device_failures.append((str(path), str(exc)))
        if self.consecutive_limit and self.consecutive >= self.consecutive_limit:
            self._trip(f"장치 읽기 오류가 연속 {self.consecutive}번 났습니다")
        if self.total_limit and len(self.device_failures) >= self.total_limit:
            self._trip(f"장치 읽기 오류가 모두 {len(self.device_failures)}번 났습니다")
        return kind

    def _trip(self, reason: str) -> None:
        self.tripped = True
        raise DiskTroubleError(
            f"{reason}. 디스크에 문제가 있을 수 있어 스캔을 멈췄습니다.",
            list(self.device_failures),
            self.consecutive,
        )

    def summary(self) -> dict:
        return {
            "deviceErrors": len(self.device_failures),
            "otherErrors": self.benign_failures,
            "consecutive": self.consecutive,
            "stopped": self.tripped,
            "samples": [{"path": p, "error": e} for p, e in self.device_failures[:10]],
        }


def check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ScanCancelled


def parse_exclude_folders(text: str) -> tuple[set[str], list[str]]:
    """제외 폴더 지정을 이름 목록과 경로 목록으로 나눈다.

    `Dropbox` 처럼 이름만 주면 어디에 있든 그 이름의 폴더를 건너뛴다.
    `D:\\Documents\\Dropbox` 처럼 경로를 주면 그 폴더만 건너뛴다.
    """
    names: set[str] = set()
    paths: list[str] = []
    for chunk in re.split(r"[,;\n]", text or ""):
        item = chunk.strip().strip('"')
        if not item:
            continue
        if os.sep in item or (os.altsep and os.altsep in item) or ":" in item:
            paths.append(os.path.normcase(os.path.normpath(item)))
        else:
            names.add(item.casefold())
    return names, paths


def iter_files(
    root: Path,
    recursive: bool,
    cancel_event: threading.Event | None = None,
    exclude_names: set[str] | None = None,
    exclude_paths: list[str] | None = None,
    skipped_folders: list[str] | None = None,
) -> list[FileRecord]:
    records: list[FileRecord] = []
    names = set(DEFAULT_SKIP_FOLDERS) | {name.casefold() for name in (exclude_names or set())}
    paths = list(exclude_paths or [])

    def is_excluded(folder_path: str, folder_name: str) -> bool:
        if folder_name.casefold() in names:
            return True
        normalized = os.path.normcase(os.path.normpath(folder_path))
        return any(normalized == item or normalized.startswith(item + os.sep) for item in paths)

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
                            mtime_ns=safe_mtime_ns(stat.st_mtime_ns),
                        )
                    )
                elif recursive and entry.is_dir(follow_symlinks=False):
                    if is_excluded(entry.path, entry.name):
                        if skipped_folders is not None:
                            skipped_folders.append(entry.path)
                        continue
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


def hash_file_with_crc(path: Path, cancel_event: threading.Event | None = None) -> tuple[str, int]:
    """sha256 과 CRC32 를 한 번 읽으면서 같이 낸다.

    CRC32 를 따로 챙기는 이유: zip 은 멤버마다 CRC32 를 중앙 디렉터리에
    적어 둔다. 즉 압축을 풀지 않고 목록만 읽어도 알 수 있다. 풀어놓은
    파일의 CRC32 를 미리 적어 두면, 나중에 그 파일들을 zip 으로 묶은 뒤에도
    압축을 풀지 않고 "이미 갖고 있는 것"인지 걸러낼 수 있다.
    """
    digest = hashlib.sha256()
    crc = 0
    with path.open("rb") as handle:
        while True:
            check_cancel(cancel_event)
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            crc = zlib.crc32(chunk, crc)
    return digest.hexdigest(), crc & 0xFFFFFFFF


# sqlite 가 담을 수 있는 정수 범위. 복구된 파일 중에 수정시각이 깨진 것이
# 있어서(43017417213000000000 = 서기 3333년) 그대로 넣으면 OverflowError 가
# 난다. sqlite3 는 이걸 sqlite3.Error 가 아니라 OverflowError 로 던지기
# 때문에 캐시의 예외 처리를 그냥 통과해 스캔 전체가 죽었다.
SQLITE_INT_MAX = 2 ** 63 - 1
SQLITE_INT_MIN = -(2 ** 63)


def safe_mtime_ns(value: int) -> int:
    """캐시 키로 쓸 수 있는 수정시각. 범위를 벗어나면 0 으로 둔다.

    0 이면 캐시가 안 맞아 그 파일만 다시 읽을 뿐이고, 스캔은 계속된다.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    if number < SQLITE_INT_MIN or number > SQLITE_INT_MAX:
        return 0
    return number


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
    io_monitor: IOHealthMonitor | None = None,
    should_stop=None,
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
        # 정해 둔 만큼 읽었으면 여기서 끊는다. 진행률을 알리기 전에 판단해야
        # 하지도 않은 파일을 했다고 보고하지 않는다.
        if should_stop is not None and should_stop():
            break
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
            if io_monitor is not None:
                # 장치 이상으로 보이면 여기서 DiskTroubleError 가 올라간다.
                io_monitor.record_failure(record.path, exc)
            continue
        if io_monitor is not None:
            io_monitor.record_success()
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


# 복구 과정에서 파일명 앞에 붙은 레코드 번호(`3947_[톤냐] ...`). 이 라이브러리는
# 99%가 이걸 달고 있고, 안 떼면 뒤의 모든 숫자 판정이 이 번호를 먼저 잡는다.
# 숫자 뒤에 구분자가 있을 때만 뗀다 - `2111이일일일` 같은 진짜 제목은 남는다.
RECOVERY_ID_PREFIX_RE = re.compile(r"^\d{2,7}[_\-. ]+")
_LEADING_TAG_ONLY_RE = re.compile(r"^\s*(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|[@#])+\s*")

# 앞뒤로 다른 숫자가 붙어 있으면 날짜·해시·크기지 화수가 아니다.
_NUM = r"(?<!\d)(\d{1,4})(?!\d)"
_EPISODE_PATTERNS = (
    re.compile(_NUM + r"\s*[-~]\s*" + _NUM + r"\s*(?:화|회)"),           # 1-130화
    re.compile(_NUM + r"\s*(?:화|회)"),                                     # 34화
    re.compile(_NUM + r"\s*[-~]\s*" + _NUM + r"\s*(?=연재|완|본|完|$)"),  # 1-61연재본
)
_VOLUME_PATTERNS = (
    re.compile(_NUM + r"\s*[-~]\s*" + _NUM + r"\s*(?:권|卷)"),            # 1-4권
    re.compile(_NUM + r"\s*(?:권|卷)"),                                      # 2권
    re.compile(_NUM + r"\s*부(?!\w)"),                                      # 2부
)


def strip_recovery_id(name: str) -> str:
    """파일명 앞의 복구 레코드 번호만 뗀다.

    앞머리 `[작가]` 태그는 일부러 남긴다. 작가 추출기가 그 대괄호를 보고
    작가를 찾기 때문에, 여기서 떼면 작가를 못 찾고 엉뚱한 조각(업로더 태그)
    을 제목으로 삼는다. 실제로 그렇게 만들었다가 서로 다른 작품 88개가
    `HH #` 하나로 묶였다.
    """
    stem = os.path.splitext(name)[0]
    stem = RECOVERY_ID_PREFIX_RE.sub("", stem)
    return stem.strip()


def _episode_scan_text(name: str) -> str:
    """화수/권수를 찾을 때 쓰는 형태. 여기서는 앞머리 태그를 떼도 안전하다."""
    stem = strip_recovery_id(name)
    previous = None
    while stem != previous:
        previous = stem
        stem = _LEADING_TAG_ONLY_RE.sub("", stem)
    stem = re.sub(r"[_.]+", " ", stem)
    return re.sub(r"\s+", " ", stem).strip()


def _first_number(patterns, text: str) -> int:
    """앞선 규칙이 잡으면 거기서 멈춘다. 범위면 큰 쪽을 쓴다."""
    for pattern in patterns:
        found = pattern.findall(text)
        if not found:
            continue
        numbers: list[int] = []
        for item in found:
            if isinstance(item, tuple):
                numbers.extend(int(x) for x in item if x)
            else:
                numbers.append(int(item))
        if numbers:
            return max(numbers)
    return 0


def episode_number(name: str) -> int:
    """화/회 표시가 분명할 때만 화수를 돌려준다. 없으면 0.

    표시를 요구하는 이유: 표시가 없으면 파일명 속 아무 숫자나 화수로 읽힌다.
    실제로 그렇게 동작하던 때 해시값에서 5757화, 날짜에서 2026화가 나왔다.
    """
    return _first_number(_EPISODE_PATTERNS, _episode_scan_text(name))


def volume_number(name: str) -> int:
    """권/부 표시가 분명할 때만 권수를 돌려준다. 없으면 0."""
    return _first_number(_VOLUME_PATTERNS, _episode_scan_text(name))


# 파일 끝에 붙는 출처/업로더 표시(`@HH #연재본`, `@꼬북`, `#ㅇㅅㄱㅇ`).
# 작품과 무관하므로 묶음 기준에서 빼야 한다. 안 빼면 같은 곳에서 받은 서로
# 다른 작품들이 한 시리즈로 묶인다.
# 태그 안에 공백·쉼표가 들어가기도 한다(`@휴개소 in톢,공금@`). 그래서 첫 @ 나 #
# 부터 끝까지를 태그로 본다. 다만 대괄호가 들어 있으면 작가 표기일 수 있어 둔다.
_SOURCE_TAG_TAIL_RE = re.compile(r"\s*[@#][^\[\]]*$")


def strip_source_tags(title: str) -> str:
    stripped = _SOURCE_TAG_TAIL_RE.sub("", title).strip(" ._-")
    # 통째로 태그뿐이면 원래 값을 둔다 - 빈 키로 몰리는 것이 더 나쁘다
    return stripped or title


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


# 권수 표시. 조각이 적을 때는 이만큼 확실한 단서가 있어야 작가로 본다.
VOLUME_MARKER_RE = re.compile(r"\d\s*(?:권|화|부|회|편|장)|(?:완결|완)$")


def looks_like_underscore_author_title(text: str, title_part: str) -> bool:
    """`김작가_별빛_소설_1권` 처럼 밑줄로 이어 붙인 이름인지 본다.

    조각이 넷 이상이면 숫자만 있어도 인정한다. 셋뿐일 때는 우연히 걸리기
    쉬우므로 `1권`, `3화` 같은 권수 표시가 있을 때만 인정한다.
    (`김작가_별빛소설_1권` 이 여기 해당한다.)
    """
    parts = [part for part in text.split("_") if part.strip()]
    if len(parts) >= 4:
        return bool(re.search(r"\d|권|화|완", title_part))
    if len(parts) == 3:
        return bool(VOLUME_MARKER_RE.search(title_part))
    return False


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
    # `_김작가_별빛_소설_1권` 처럼 앞에 구분자가 하나 붙어 오는 경우가 잦다.
    # 떼고 나서 봐야 작가 자리가 드러난다.
    underscore_text = text.lstrip("_-. ")
    underscore_match = AUTHOR_UNDERSCORE_PREFIX_RE.match(underscore_text)
    if underscore_match and looks_like_underscore_author_title(
        underscore_text, underscore_match.group("title")
    ):
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


def _collect_author_counts(
    files: list[FileRecord],
    strip_suffix: bool,
    series: bool,
    metadata_authors: dict[str, str] | None = None,
) -> dict[str, str]:
    metadata_authors = metadata_authors or {}
    authors: dict[str, dict[str, int]] = {}
    for record in files:
        stem, _extension = split_name(record.path.name, True)
        author, cleaned = extract_author_from_stem(stem)
        # 파일 안에 적힌 작가가 있으면 그쪽이 확실하다. 제목은 파일명에서 뽑은
        # 것을 그대로 써야 같은 제목의 다른 파일과 짝이 맞는다.
        author = metadata_authors.get(str(record.path), "") or author
        if not author:
            continue
        if strip_suffix:
            cleaned = remove_copy_suffix(cleaned)
        key = title_key(normalize_series_title(cleaned) if series else normalize_book_title(cleaned))
        if not key:
            continue
        authors.setdefault(key, {})
        authors[key][author] = authors[key].get(author, 0) + 1
    return {
        # 한 제목에 여러 작가가 잡히면 가장 많이 나온 쪽을 쓴다
        key: sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))[0][0]
        for key, counts in authors.items()
    }


def build_author_map(
    files: list[FileRecord],
    strip_suffix: bool = True,
    metadata_authors: dict[str, str] | None = None,
) -> dict[str, str]:
    """제목이 완전히 같은 파일끼리 작가명을 나눠 쓰기 위한 표."""
    return _collect_author_counts(files, strip_suffix, series=False, metadata_authors=metadata_authors)


def build_series_author_map(
    files: list[FileRecord],
    strip_suffix: bool = True,
    metadata_authors: dict[str, str] | None = None,
) -> dict[str, str]:
    """권수를 뗀 시리즈 제목으로 묶은 표.

    `[김작가] 별빛 소설 1권` 이 있으면 `별빛 소설 2권` 에도 작가명을 채울 수
    있다. 다만 서로 다른 작품인데 시리즈 제목이 우연히 같으면 엉뚱한 작가가
    붙으므로, 이 표는 켠 경우에만 쓴다.
    """
    return _collect_author_counts(files, strip_suffix, series=True, metadata_authors=metadata_authors)


def series_title_key_from_stem(stem: str, strip_suffix: bool = True) -> str:
    author, cleaned = extract_author_from_stem(stem)
    del author
    if strip_suffix:
        cleaned = remove_copy_suffix(cleaned)
    return title_key(normalize_series_title(cleaned))


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
    number_separator: str = "",
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
        stem = f"{stem}{number_separator}{number_text}"

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
    number_separator: str = "",
    series_author: bool = False,
    metadata_authors: dict[str, str] | None = None,
) -> list[RenameEntry]:
    plan: list[RenameEntry] = []
    targets: dict[Path, int] = {}
    metadata_authors = metadata_authors or {}
    author_map = build_author_map(files, strip_copy_suffix, metadata_authors) if auto_author else {}
    series_map = (
        build_series_author_map(files, strip_copy_suffix, metadata_authors)
        if auto_author and series_author
        else {}
    )

    for index, record in enumerate(files):
        stem, _extension = split_name(record.path.name, True)
        author_hint = ""
        if auto_author and not author.strip():
            # ① 이 파일 안에 적힌 작가 ② 제목이 같은 파일 ③ (켰다면) 같은 시리즈
            author_hint = metadata_authors.get(str(record.path), "")
            if not author_hint:
                author_hint = author_map.get(rename_title_key_from_stem(stem, strip_copy_suffix), "")
            if not author_hint and series_map:
                author_hint = series_map.get(series_title_key_from_stem(stem, strip_copy_suffix), "")
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
                number_separator,
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
