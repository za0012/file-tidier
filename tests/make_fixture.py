# -*- coding: utf-8 -*-
"""검증용 고정 표본을 만든다.  python tests/make_fixture.py [경로]

시험이 내 PC 의 실제 책 폴더에 묶여 있으면 남이 clone 해서 확인할 수 없다.
그래서 실제로 문제를 일으켰던 파일 모양만 골라 작은 표본으로 재현한다.
내용은 고정 씨앗으로 만들어 매번 같은 해시가 나온다.

여기 담긴 것은 전부 이 프로젝트에서 실제로 오작동을 냈던 경우다:
  - 복구 레코드 번호 접두사(`3947_...`)      화수 판정을 망가뜨렸다
  - 해시·날짜가 든 이름                      5757화 / 2026화 로 읽혔다
  - `1-232화` 범위 표기                      1화 로 읽혔다
  - 이름만 다른 같은 내용                    중복 판정의 기본
  - 같은 작품의 여러 권                      묶기 대상
  - 업로더 꼬리표(`@HH #연재본`)             작품 88개를 하나로 묶었다
  - epub 메타데이터                          깨진 이름을 되살리는 근거
"""
import os
import sys
import zipfile

# 출력이 한글이다. 윈도우 기본 출력 인코딩에서는 그대로 찍으면
# UnicodeEncodeError 로 죽는다. 어디서 돌든 utf-8 로 찍는다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixture")

BODY = "이것은 검증용 본문입니다. 문장이 충분히 길어야 지문 계산에 들어갑니다. "


def body(seed: int, sentences: int = 40) -> bytes:
    lines = [f"{BODY}{seed}번째 표본의 {i}번째 문장입니다." for i in range(sentences)]
    return "\n".join(lines).encode("utf-8")


def write(path: str, data: bytes) -> None:
    full = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as handle:
        handle.write(data)


def write_epub(path: str, title: str, creator: str, seed: int) -> None:
    full = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{title}</dc:title><dc:creator>{creator}</dc:creator>"
        "</metadata></package>"
    )
    text = body(seed).decode("utf-8")
    with zipfile.ZipFile(full, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml",
                         '<?xml version="1.0"?><container><rootfiles>'
                         '<rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/text.xhtml", f"<html><body><p>{text}</p></body></html>")


def build() -> dict:
    made = {"files": 0}

    # 이름만 다른 같은 내용 - 중복 판정의 기본
    same = body(1)
    write("중복/이름만다름 A.txt", same)
    write("중복/전혀 다른 이름으로 저장된 것.txt", same)
    write("중복/하위/또 같은 내용.txt", same)

    # 크기는 같은데 내용이 다른 것 - 크기만 보면 안 된다는 반례
    write("중복/크기같고내용다름 1.txt", b"A" * 5000)
    write("중복/크기같고내용다름 2.txt", b"B" * 5000)

    # 화수 판정: 표시가 분명한 것만 세야 한다
    write("화수/3947_[톤냐] 피 위에 핀 꽃 34화 (연재본).txt", body(2))
    write("화수/[제갈덕순] 보육원의 사범님 1-232화.zip", body(3))
    write("화수/11161_[마작가] 넷째 작품 1-61연재본 完.epub", body(4))
    write("화수/67171_[삭각] 시스템은 사랑을 모른다1-4권 완.epub", body(5))
    # 아래 셋은 화수가 0 이어야 한다
    write("화수/gigafile-0615-6c62b1d04c0fe278594fd5757d255a9c.zip", body(6))
    write("화수/버릇없는 놈들 외전_20260531_132906.txt", body(7))
    write("화수/204556_한뼘 BL 컬렉션 858 감염컴.epub", body(8))

    # 같은 작품의 여러 권 - 묶여야 한다
    for volume in range(1, 6):
        write_epub(f"시리즈/-검증- 표본 시리즈 {volume}권.epub", f"표본 시리즈 {volume}권", "검증", 10 + volume)

    # 업로더 꼬리표 - 서로 다른 작품이 하나로 묶이면 안 된다
    write("꼬리표/100027_[가작가] 첫째 작품 (연재完) @HH #연재본.txt", body(20))
    write("꼬리표/101853_[나작가] 둘째 작품 (연재完) @HH #연재본.txt", body(21))
    write("꼬리표/102205_[다작가] 셋째 작품 (연재完) @HH #연재본.txt", body(22))

    # 깨진 이름 - 파일 안 제목으로 되살릴 수 있어야 한다
    write_epub("깨진이름/21734_[yoyo] ��� 1�.epub", "너희, 포사들 1권", "yoyo", 30)

    for _dirpath, _dirs, files in os.walk(ROOT):
        made["files"] += len(files)
    return made


if __name__ == "__main__":
    if os.path.isdir(ROOT):
        import shutil
        shutil.rmtree(ROOT, ignore_errors=True)
    info = build()
    print("표본 생성: %s" % ROOT)
    print("  파일 %d개" % info["files"])
