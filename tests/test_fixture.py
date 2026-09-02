# -*- coding: utf-8 -*-
"""고정 표본으로 실제 스캔 결과를 검사한다.  python tests/test_fixture.py

test_scan.py 는 함수 하나하나를 본다. 여기서는 백엔드 명령을 실제로 돌려
결과 JSON 을 확인한다 - 실제 폴더를 훑는 경로가 통째로 돌아가야 잡히는
문제가 있었기 때문이다(깨진 수정시각에 스캔이 죽던 것이 그랬다).
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, "fixture")
BACKEND = os.path.join(ROOT, "file_tidier_backend.py")

PASS = 0
FAIL = 0


def check(label, condition, extra=""):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        print("  FAIL  %s %s" % (label, extra))


def run(command, cache_dir, *args):
    env = dict(os.environ)
    env["FILE_TIDIER_CACHE_DIR"] = cache_dir
    env["PYTHONIOENCODING"] = "utf-8"
    out = subprocess.run(
        [sys.executable, BACKEND, command, "--folder", FIXTURE, "--limit", "0", *args],
        capture_output=True, env=env, cwd=ROOT,
    )
    text = out.stdout.decode("utf-8", "replace").strip()
    try:
        return json.loads(text)
    except Exception:
        print("  (출력 파싱 실패) %s" % text[:200])
        return {}


def main():
    if not os.path.isdir(FIXTURE):
        subprocess.run([sys.executable, os.path.join(HERE, "make_fixture.py")], cwd=ROOT, check=True)

    cache = tempfile.mkdtemp(prefix="ft-fixture-")
    try:
        catalog = run("catalog", cache)
        check("카탈로그가 돈다", catalog.get("ok") is True, str(catalog.get("error"))[:80])
        items = {os.path.basename(i["location"]): i for i in catalog.get("items", [])}
        check("표본을 전부 읽었다", len(items) >= 20, "%d개" % len(items))

        # 화수는 표시가 분명할 때만
        expect_episode = {
            "3947_[톤냐] 피 위에 핀 꽃 34화 (연재본).txt": 34,
            "[제갈덕순] 보육원의 사범님 1-232화.zip": 232,
            "11161_[병호] 인외기혼자 1-61연재본 完.epub": 61,
            "gigafile-0615-6c62b1d04c0fe278594fd5757d255a9c.zip": 0,
            "버릇없는 놈들 외전_20260531_132906.txt": 0,
            "204556_한뼘 BL 컬렉션 858 감염컴.epub": 0,
        }
        for name, want in expect_episode.items():
            got = int(items.get(name, {}).get("episodeCount") or 0)
            check("화수 %s" % name[:34], got == want, "기대 %d, 실제 %d" % (want, got))

        check("권수 1-4권", int(items.get("67171_[삭각] 시스템은 사랑을 모른다1-4권 완.epub", {}).get("volumeCount") or 0) == 4)

        # 업로더 꼬리표로 서로 다른 작품이 묶이면 안 된다
        tagged = [i for n, i in items.items() if "@HH" in n]
        series = {i.get("seriesTitle") for i in tagged}
        check("꼬리표가 작품을 하나로 묶지 않는다", len(series) == len(tagged), str(series))

        # 같은 작품의 여러 권은 한 시리즈로
        volumes = [i for n, i in items.items() if "표본 시리즈" in n]
        check("같은 작품의 권들이 한 시리즈로", len({i.get("seriesTitle") for i in volumes}) == 1,
              str({i.get("seriesTitle") for i in volumes}))

        # 내용이 같은 것만 중복으로
        dup = run("duplicates-content", cache, "--min-size-kb", "1")
        check("중복 검사가 돈다", dup.get("ok") is True, str(dup.get("error"))[:80])
        names = [row["name"] for row in dup.get("items", [])]
        check("이름만 다른 같은 내용 3개를 잡는다",
              sum(1 for n in names if n in
                  ("이름만다름 A.txt", "전혀 다른 이름으로 저장된 것.txt", "또 같은 내용.txt")) == 3,
              str(names[:6]))
        check("크기만 같은 것은 중복이 아니다",
              not any("크기같고내용다름" in n for n in names), str(names[:6]))

        # 읽을 개수 제한
        capped = run("duplicates-content", cache + "-2", "--min-size-kb", "1", "--max-files", "2")
        check("읽을 최대가 지켜진다", (capped.get("cache") or {}).get("misses", 99) <= 2,
              str(capped.get("cache")))
    finally:
        shutil.rmtree(cache, ignore_errors=True)
        shutil.rmtree(cache + "-2", ignore_errors=True)

    print("PASS %d  FAIL %d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
