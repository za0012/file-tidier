# -*- coding: utf-8 -*-
"""스캔 쪽 회귀 시험.  실행:  python tests/test_scan.py

스크래치패드에 두었다가 두 번 잃어버려서 저장소 안으로 옮겼다.
디스크나 UI 없이 도는 것만 넣는다.
"""
import os
import sys
import errno
import argparse
import sqlite3
import tempfile
import io
import zipfile
import pathlib
import unicodedata
import shutil

# 출력이 한글이다. 윈도우 기본 출력 인코딩에서는 그대로 찍으면
# UnicodeEncodeError 로 죽는다. 어디서 돌든 utf-8 로 찍는다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from file_tidier_core import (  # noqa: E402

    DiskTroubleError,
    FileRecord,
    IOHealthMonitor,
    classify_io_error,
    episode_number,
    safe_mtime_ns,
    group_by_content,
    strip_recovery_id,
    strip_source_tags,
    volume_number,
)

PASS = 0
FAIL = 0


def check(label, condition, extra=""):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        print("  FAIL  %s %s" % (label, extra))


def oserr(winerror=None, err=None):
    exc = OSError(err or errno.EIO, "boom")
    if winerror is not None:
        exc.winerror = winerror
        exc.errno = errno.EACCES      # 윈도우는 errno 를 엉뚱하게 채우기도 한다
    return exc


# ---------------------------------------------------------------- 오류 분류
def test_classify():
    check("CRC(23) 는 장치", classify_io_error(oserr(winerror=23)) == "device")
    check("I/O 장치(1117)", classify_io_error(oserr(winerror=1117)) == "device")
    check("EIO", classify_io_error(OSError(errno.EIO, "io")) == "device")
    check("권한없음(5) 은 흔한 실패", classify_io_error(oserr(winerror=5)) == "benign")
    check("파일없음", classify_io_error(OSError(errno.ENOENT, "no")) == "benign")
    check("공유위반(32)", classify_io_error(oserr(winerror=32)) == "benign")
    check("OSError 아닌 것", classify_io_error(ValueError("x")) == "benign")


# ------------------------------------------------------------ 디스크 감시기
def test_monitor():
    m = IOHealthMonitor(consecutive_limit=3, total_limit=0)
    m.record_failure("a", oserr(winerror=23))
    m.record_failure("b", oserr(winerror=23))
    check("2번은 안 멈춤", not m.tripped)
    try:
        m.record_failure("c", oserr(winerror=23))
        check("3번째에 멈춤", False)
    except DiskTroubleError as exc:
        check("3번째에 멈춤", True)
        check("실패 목록 3건", len(exc.failures) == 3)

    m = IOHealthMonitor(consecutive_limit=3, total_limit=0)
    m.record_failure("a", oserr(winerror=23))
    m.record_failure("b", oserr(winerror=23))
    m.record_success()
    m.record_failure("c", oserr(winerror=23))
    m.record_failure("d", oserr(winerror=23))
    check("성공하면 연속 초기화", not m.tripped)

    # 배드 섹터 사이에 권한 오류가 껴도 흐름은 이어진 것이다
    m = IOHealthMonitor(consecutive_limit=3, total_limit=0)
    m.record_failure("a", oserr(winerror=23))
    m.record_failure("perm", oserr(winerror=5))
    m.record_failure("b", oserr(winerror=23))
    try:
        m.record_failure("c", oserr(winerror=23))
        check("흔한 실패는 연속을 안 끊는다", False)
    except DiskTroubleError:
        check("흔한 실패는 연속을 안 끊는다", True)

    m = IOHealthMonitor(consecutive_limit=3, total_limit=5)
    for i in range(40):
        m.record_failure("p%d" % i, oserr(winerror=5))
    check("권한오류만으로는 안 멈춤", not m.tripped)
    check("흔한 실패 40건 셈", m.benign_failures == 40)

    m = IOHealthMonitor(consecutive_limit=0, total_limit=4)
    tripped = False
    try:
        for i in range(10):
            m.record_failure("x%d" % i, oserr(winerror=1117))
            m.record_success()
    except DiskTroubleError:
        tripped = True
    check("연속이 끊겨도 총합으로 멈춤", tripped)

    m = IOHealthMonitor(consecutive_limit=0, total_limit=0)
    for i in range(50):
        m.record_failure("y%d" % i, oserr(winerror=23))
    check("한도 0 이면 꺼짐", not m.tripped)


# ------------------------------------------------ group_by_content 중단/예산
def test_group_by_content():
    recs = [FileRecord(path=__import__("pathlib").Path("C:/fake/f%d.txt" % i),
                       size=4096, modified=0.0, mtime_ns=0) for i in range(10)]

    m = IOHealthMonitor(consecutive_limit=3, total_limit=0)
    seen = []

    def bad(record):
        raise oserr(winerror=23)

    try:
        group_by_content(recs, 0, hash_provider=bad,
                         error_callback=lambda r, e: seen.append(str(r.path)), io_monitor=m)
        check("장치 오류로 중단", False)
    except DiskTroubleError:
        check("장치 오류로 중단", True)
        check("중단 전 3개만 시도", len(seen) == 3, "(%d개)" % len(seen))

    seen = []
    out = group_by_content(recs, 0, hash_provider=bad,
                           error_callback=lambda r, e: seen.append(str(r.path)))
    check("감시기 없으면 예전처럼 전부 시도", len(seen) == 10 and out == {})

    calls = []
    group_by_content(recs, 0, hash_provider=lambda r: (calls.append(r), "h")[1],
                     should_stop=lambda: len(calls) >= 4)
    check("should_stop 이 예산을 지킨다", len(calls) == 4, "(%d개)" % len(calls))


# ------------------------------------------------------------- 화수/권수
def test_episode():
    # 예전 로직이 틀렸던 실제 파일명들
    wrong_before = [
        ("gigafile-0615-6c62b1d04c0fe278594fd5757d255a9c.zip", 0, 0),   # 해시
        ("[손태옥] 버릇없는 놈들 외전_20260531_132906.txt", 0, 0),        # 날짜
        ("204556_한뼘_BL_컬렉션_858_감염컴_렌탈_남친.epub", 0, 0),        # 레코드 번호
        ("[프라이버시] 빌런님 주인공 꼬신다 외포완 CSS 2500보다.epub", 0, 0),
        ("3947_[톤냐] 피 위에 핀 꽃 34화 (연재본).txt", 34, 0),
        ("[제갈덕순] 보육원의 사범님 1-232화.zip", 232, 0),
        ("11161_[병호] 인외기혼자 1-61연재본 完@꼬북.epub", 61, 0),
        ("67171_[삭각] 시스템은 사랑을 모른다1-4권 완.epub", 0, 4),
        ("[돌체] 2111이일일일 1권 E 265KB.txt", 0, 1),
        ("6909_호박김치_이거_귀농_게임이라며_1_11권_완결.epub", 0, 11),
        ("-gujo- 구구구구 999.9 5권 (완결).epub", 0, 5),
    ]
    for name, ep, vol in wrong_before:
        check("화수 %s" % name[:34], episode_number(name) == ep,
              "기대 %d, 실제 %d" % (ep, episode_number(name)))
        check("권수 %s" % name[:34], volume_number(name) == vol,
              "기대 %d, 실제 %d" % (vol, volume_number(name)))

    # 레코드 번호는 구분자가 있을 때만 뗀다 - 진짜 제목은 지키기
    # 레코드 번호만 뗀다. 앞머리 [작가] 태그는 남겨야 한다 - 작가 추출기가
    # 그 대괄호를 보고 작가를 찾는다. 떼었다가 서로 다른 작품 88개가 업로더
    # 태그 하나로 묶인 적이 있다.
    check("레코드 번호만 제거",
          strip_recovery_id("3947_[톤냐] 피 위에 핀 꽃.txt") == "[톤냐] 피 위에 핀 꽃")
    check("숫자 제목은 안 건드림", strip_recovery_id("2111이일일일 1권.txt").startswith("2111"))
    check("출처 태그 제거", strip_source_tags("파로스 @HH #연재본") == "파로스")
    check("태그뿐이면 원래대로", strip_source_tags("@HH #연재본") == "@HH #연재본")


# ------------------------------------------------------- 체크포인트 sqlite
def test_checkpoint():
    import file_tidier_backend as B
    tmp = tempfile.mkdtemp()
    os.environ["FILE_TIDIER_CACHE_DIR"] = tmp
    try:
        conn = B.open_index_cache()
        check("캐시 열림", conn is not None)
        if conn is None:
            return
        cp = B.ScanCheckpoint(conn, "job1", "duplicates-content", "C:/x")
        cp.begin(100)
        cp.advance(50, "C:/x/a.txt")
        cp.finish("partial")
        row = B.read_checkpoint(conn, "job1")
        check("상태가 남는다", row and row["status"] == "partial")
        check("중단 지점이 남는다", row and row["lastPath"].endswith("a.txt"), str(row))
        cp.note_bad("C:/x/bad.txt", "CRC")
        conn.commit()
        check("불량 파일 기록", "C:/x/bad.txt" in B.read_bad_paths(conn))
        cleared = B.clear_scan_state(conn)
        check("지우기", cleared["checkpoints"] >= 1)
        check("지운 뒤 비어 있음", B.read_checkpoint(conn, "job1") is None)
        B.close_index_cache(conn)
    finally:
        os.environ.pop("FILE_TIDIER_CACHE_DIR", None)
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------- 이상한 수정시각
def test_mtime():
    """복구된 파일 중에 수정시각이 int64 를 넘는 것이 있다(서기 3333년).

    sqlite3 는 이걸 sqlite3.Error 가 아니라 OverflowError 로 던져서, 캐시의
    `except sqlite3.Error` 를 그냥 통과해 스캔 전체가 죽었다. 실제로
    D:\Downloads 에서 29개가 그랬다.
    """
    huge = 43017417213000000000          # 실제로 나온 값
    check("범위를 넘으면 0", safe_mtime_ns(huge) == 0)
    check("음수 극단도 0", safe_mtime_ns(-(2 ** 64)) == 0)
    check("정상 값은 그대로", safe_mtime_ns(1735689600000000000) == 1735689600000000000)
    check("숫자가 아니면 0", safe_mtime_ns("x") == 0)

    import file_tidier_backend as B
    tmp = tempfile.mkdtemp()
    os.environ["FILE_TIDIER_CACHE_DIR"] = tmp
    try:
        conn = B.open_index_cache()
        if conn is None:
            check("캐시 열림", False)
            return
        cache = B.FileHashCache(conn)
        # 걸러지지 않은 값이 들어와도 스캔을 죽이면 안 된다
        raised = False
        try:
            cache.remember(__import__("pathlib").Path("C:/x/a.txt"), 10, huge, "deadbeef")
            cache.flush()
        except Exception:
            raised = True
        check("이상한 값이 들어와도 예외가 새지 않는다", not raised)
        B.close_index_cache(conn)
    finally:
        os.environ.pop("FILE_TIDIER_CACHE_DIR", None)
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------- 본문 지문 고정
def test_fingerprint_pinned():
    """지문 값 자체를 못박아 둔다.

    지문이 바뀌면 캐시가 전부 어긋나고 중복 판정이 달라진다. 속도를 위해
    문장 정리 쪽을 손댔을 때(초당 2.0 -> 5.4개) 값이 그대로인지 실제 파일
    60개로 확인했고, 그 확인을 여기 남긴다. 규칙을 고치려면 이 값이 왜
    바뀌어도 되는지 먼저 설명할 수 있어야 한다.
    """
    import file_tidier_backend as B
    nl = chr(10)
    sample = nl.join([
        "첫 문장입니다. 두 번째 문장이고요.",
        "c12 앞에 붙은 장 번호는 지워져야 한다.",
        "짧음.",
        "* * * 별표로 시작하는 줄도 정리된다.",
        "숫자 3권 12화 표시가 들어간 문장입니다.",
        "보이지 않는" + chr(0x200b) + "문자가 낀 문장도 같은 값이 나와야 한다.",
        "",
    ])
    fingerprint, count, _preview = B.sentence_fingerprint(sample)
    check("지문 값이 그대로",
          fingerprint == "f0b2e2aebfe9498cff221c4a52bf621afb6266ce237eec87f1805ad332e4619a",
          fingerprint[:20])
    check("문장 수가 그대로", count == 4, str(count))

    # 보이지 않는 서식 문자는 지우는 게 아니라 공백 한 칸으로 바꾼다.
    # (지워 버리면 앞뒤 낱말이 붙어 다른 문장이 된다.) 빠른 경로를 넣어도
    # 이 규칙이 그대로인지 확인한다.
    spaced = sample.replace(chr(0x200b), " ")
    check("보이지 않는 문자는 공백 한 칸과 같다",
          B.sentence_fingerprint(spaced)[0] == fingerprint)
    removed = sample.replace(chr(0x200b), "")
    check("지워 버린 것과는 달라야 한다",
          B.sentence_fingerprint(removed)[0] != fingerprint)




def test_title_recovery():
    """복구로 이름을 잃은 파일은 안을 열어 제목을 찾는다."""
    import file_tidier_backend as B
    check("한글 제목은 멀쉬하지 않다", not B.title_is_meaningless("도깨비 1"))
    check("영단어 제목도", not B.title_is_meaningless("Forever Stranded"))
    check("해시 같은 이름은 멀쉬하다", B.title_is_meaningless("278a02"))
    check("숫자만 있어도", B.title_is_meaningless("00001"))
    check("자모만 있어도", B.title_is_meaningless("ㄷㄱㅂ"))
    check("빈 것도", B.title_is_meaningless(""))
    # 조합형(NFD) 한글은 자모 영역에 있어 그대로 보면 멀쉬해 보인다.
    # 제목은 이미 NFC 로 고쳐졌으므로 여기에 걸리면 안 된다.
    check("NFC 한글은 멀쉬하지 않다",
          not B.title_is_meaningless(unicodedata.normalize("NFC", "오수")))

    with tempfile.TemporaryDirectory() as folder:
        root = pathlib.Path(folder)

        # txt: 첫 의미있는 줄. 장식줄은 건너뛴다.
        text = root / "00001.txt"
        text.write_text("=====\n\n친구니까 삼각관계\n본문 시작\n", encoding="utf-8")
        check("txt 제목", B.recover_title_from_content(text, ".txt") == "친구니까 삼각관계")

        # zip: 내부 항목명
        archive = root / "278a02.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("__MACOSX/", "")
            zf.writestr("오, 마이 슈팅스타! 1권.txt", "본문")
        check("zip 제목", B.recover_title_from_content(archive, ".zip") == "오, 마이 슈팅스타! 1권")

        # 깨진 zip 은 조용히 빈 문자열
        broken = root / "broken.zip"
        broken.write_bytes(b"not a zip at all")
        check("깨진 zip 은 빈 값", B.recover_title_from_content(broken, ".zip") == "")

        # 없는 파일도 터지지 않는다
        check("없는 파일", B.recover_title_from_content(root / "없음.txt", ".txt") == "")
        check("모르는 확장자", B.recover_title_from_content(text, ".hwp") == "")

        # 제목이 멀쉬할 때만 파일을 열어야 한다
        named = root / "도깨비 1권.txt"
        named.write_text("전혀 다른 첫 줄\n", encoding="utf-8")
        item = B.enrich_catalog_item(
            {"name": named.name, "location": str(named), "extension": ".txt", "title": named.name},
            with_thumbnails=False)
        check("멀쉬하지 않은 제목은 그대로",
              item["displayTitle"] == "도깨비 1권" and not item["titleFromContent"],
              item["displayTitle"])
        item = B.enrich_catalog_item(
            {"name": text.name, "location": str(text), "extension": ".txt", "title": text.name},
            with_thumbnails=False)
        check("멀쉬한 제목은 내용에서",
              item["displayTitle"] == "친구니까 삼각관계" and item["titleFromContent"],
              item["displayTitle"])




def test_title_extension():
    """제목 가운데 마침표를 확장자로 착각해 뒤를 잘라먹지 않는다."""
    from file_tidier_core import normalize_book_title as norm
    # 이미 확장자를 뗀 제목을 다시 넣어도 살아남아야 한다
    check("점 뒤 한글은 확장자가 아니다",
          norm("Q. 공략대로 했는데 안 되던데요 1권") == "Q 공략대로 했는데 안 되던데요 1권",
          norm("Q. 공략대로 했는데 안 되던데요 1권"))
    check("한 글자 뒤도 안 자른다", norm("+P.B") == "+P B", norm("+P.B"))
    check("느낌표로 끝나면 안 자른다", norm("+Mr.+Vampire!") == "+Mr +Vampire!")
    # 진짜 확장자는 그대로 뗀다
    check("epub 은 뗀다", norm("[디삼] Q. 공략대로 했는데 안 되던데요 1권.epub")
          == "Q 공략대로 했는데 안 되던데요 1권")
    check("zip 은 뗀다", norm("도깨비 1권.zip") == "도깨비 1권")
    check("md 도 뗀다", norm("책.md") == "책")
    check("경로가 붙어도", norm("E:/폴더/이름.epub") == "이름")




def test_decode_bytes():
    """인코딩을 되는 대로 고르지 않고, 나온 글자가 글자다운지로 고른다."""
    import file_tidier_backend as B
    cases = [
        ("utf-8", "한글 문서입니다".encode("utf-8"), "한글 문서입니다"),
        ("utf-8 BOM", "제목입니다".encode("utf-8-sig"), "제목입니다"),
        ("cp949", "한글 문서입니다 여러 줄".encode("cp949"), "한글 문서입니다 여러 줄"),
        ("euc-kr", "한글".encode("euc-kr"), "한글"),
        ("utf-16 BOM", "안녕 세상".encode("utf-16"), "안녕 세상"),
        # 표식 없는 utf-16 은 한글만 들면 널 바이트조차 없다. 예전에는 cp949 가
        # 먼저 "성공"해 깨진 글자가 나왔다.
        ("utf-16le", "안녕 세상 반갑습니다 오늘도".encode("utf-16-le"), "안녕 세상 반갑습니다 오늘도"),
        ("utf-16be", "안녕 세상 반갑습니다 오늘도".encode("utf-16-be"), "안녕 세상 반갑습니다 오늘도"),
        ("ascii", b"hello world", "hello world"),
    ]
    for label, data, want in cases:
        check("디코딩 " + label, B.decode_bytes(data) == want, repr(B.decode_bytes(data)[:24]))

    # 앞부분만 잘라 읽으면 마지막 글자가 끊긴다. 그것 때문에 다른 인코딩으로
    # 넘어가면 안 된다.
    whole = ("들이닥치다 10권" + " 본문" * 400).encode("utf-8-sig")
    for size in (300, 4097, 8193):
        head = whole[:size]
        check("잘린 %d 바이트" % size,
              B.decode_bytes(head).startswith("들이닥치다 10권"),
              repr(B.decode_bytes(head)[:20]))


def test_jamo_title():
    """초성 약칭은 뜻이 통하는 이름이 아니다."""
    import file_tidier_backend as B
    check("초성에 권 하나 붙어도 약칭", B.title_is_meaningless("ㅌㅅㄹ ㅇㅂ ㄷ ㄱㅇㄷ 2권"))
    check("초성에 완결 붙어도 약칭", B.title_is_meaningless("ㅁㅊㅍㅇㅌ3(완결)"))
    check("멀쩡한 제목은 그대로", not B.title_is_meaningless("들이닥치다 10권"))
    check("앞머리만 초성인 것도 약칭", B.title_is_meaningless("ㅋㄷㄹ 1 120 추가외전포함 완 ABCX"))
    check("낱말이 안 갈려도 약칭", B.title_is_meaningless("대ㅁㅂ사 1"))
    check("숫자만 붙은 초성도 약칭", B.title_is_meaningless("ㅈㅅ 200914 004235"))
    # + _ - 로 이어 붙인 이름은 공백만으로 가르면 낱말이 안 갈린다
    check("+ 로 이어붙인 것도 약칭", B.title_is_meaningless("ㅋㄷㄹ+1 120+추가외전포함+완+ABCX"))
    # ㅋㅋㅋ·ㅠㅠ 는 약칭이 아니라 표현이다. 낱자가 한 종류뿐이면 넘긴다.
    check("ㅋㅋㅋ 정도는 제목", not B.title_is_meaningless("ㅋㅋㅋ 웃긴 이야기 모음집"))
    check("ㅎㅎ 도 제목", not B.title_is_meaningless("ㅎㅎ 그냥 일상 이야기"))
    check("멀쩡한 제목 여럿", not any(B.title_is_meaningless(x) for x in
          ["워커맨의 남자들 1권", "대마법사 완전정복 1", "잠식", "犬", "Forever Stranded"]))

    with tempfile.TemporaryDirectory() as folder:
        root = pathlib.Path(folder)
        # 안쪽 이름까지 초성인 압축은 그 안의 epub 을 열어 책 정보를 본다.
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as epub:
            epub.writestr("content.opf",
                          '<package><metadata><dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">'
                          '매치포인트</dc:title></metadata></package>')
        archive = root / "ㅁㅊㅍㅇㅌ.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("ㅁㅊㅍㅇㅌ 1권.epub", inner.getvalue())
        check("압축 안 epub 까지 본다",
              B.recover_title_from_content(archive, ".zip") == "매치포인트",
              B.recover_title_from_content(archive, ".zip"))

        # 압축 안에 든 항목은 위치가 `바깥.zip :: 안쪽.epub` 꼴이다.
        # 경로로는 못 여니 바깥을 열어 안쪽 바이트를 꺼내야 한다.
        holder = root / "새 폴더.zip"
        with zipfile.ZipFile(holder, "w") as zf:
            zf.writestr("ㅇㅋㅁㅇ+ㄴㅈㄷ+1권@토끼.epub", inner.getvalue())
        check("압축 안 항목도 연다",
              B.recover_title_from_content(
                  pathlib.Path(str(holder) + " :: ㅇㅋㅁㅇ+ㄴㅈㄷ+1권@토끼.epub"), ".epub") == "매치포인트")
        check("없는 안쪽 이름은 빈 값",
              B.recover_title_from_content(
                  pathlib.Path(str(holder) + " :: 없는것.epub"), ".epub") == "")

        # `잠식` 처럼 두 글자 제목이 흔하다. 주소 다음 줄에 있어도 찾아야 한다.
        short = root / "ㅈㅅ_200914.txt"
        short.write_text("https://mega.nz/file/abc" + chr(10) + "잠식" + chr(10), encoding="utf-8")
        check("주소 다음의 두 글자 제목",
              B.recover_title_from_content(short, ".txt") == "잠식",
              B.recover_title_from_content(short, ".txt"))

        # 내려받기 주소만 든 껍데기는 제목이 아니다
        link = root / "ㅂㅈ.txt"
        link.write_text("https://file2.me/d/27iv8c", encoding="utf-8")
        check("주소는 제목이 아니다", B.recover_title_from_content(link, ".txt") == "")


for fn in (test_classify, test_monitor, test_group_by_content, test_episode, test_checkpoint, test_mtime, test_fingerprint_pinned, test_title_recovery, test_title_extension, test_decode_bytes, test_jamo_title):
    fn()

print("PASS %d  FAIL %d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
