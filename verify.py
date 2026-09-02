# -*- coding: utf-8 -*-
"""전부 검증한다.  python verify.py [--quick]

이 프로젝트는 AI 와 함께 만들었고, AI 는 "다 됐습니다" 를 자주 틀리게 말한다.
그래서 완료 보고 대신 이 명령의 결과를 믿는다. 한 번에 돌려서 하나라도
실패하면 0 이 아닌 값으로 끝난다.

  단위 시험      함수 하나하나. 빠르고, 대부분의 판정 규칙을 여기서 고정한다.
  표본 시험      백엔드 명령을 실제로 돌려 결과 JSON 을 본다.
  연기 시험      앱을 띄워 사람이 쓰는 경로 그대로 돌린다(--quick 이면 건너뜀).
  벤치마크       성능 주장을 재현한다.
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
QUICK = "--quick" in sys.argv


def run(label, command, optional=False):
    print("\n" + "=" * 62)
    print("  " + label)
    print("=" * 62)
    started = time.perf_counter()
    result = subprocess.run(command, cwd=ROOT)
    elapsed = time.perf_counter() - started
    ok = result.returncode == 0
    print("  -> %s (%.1f초)" % ("통과" if ok else "실패", elapsed))
    return True if (optional and not ok) else ok


def main():
    steps = [
        ("표본 만들기", [sys.executable, os.path.join("tests", "make_fixture.py")]),
        ("단위 시험", [sys.executable, os.path.join("tests", "test_scan.py")]),
        ("표본 시험", [sys.executable, os.path.join("tests", "test_fixture.py")]),
    ]
    if not QUICK:
        steps.append(("연기 시험 (앱 구동)", ["node", os.path.join("tests", "smoke_app.js")]))
    steps.append(("벤치마크", [sys.executable, os.path.join("tests", "bench_fingerprint.py")]))

    failed = []
    for label, command in steps:
        if not run(label, command):
            failed.append(label)

    print("\n" + "=" * 62)
    if failed:
        print("  실패: %s" % ", ".join(failed))
        return 1
    print("  전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
