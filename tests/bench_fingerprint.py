# -*- coding: utf-8 -*-
"""본문 지문 계산 속도를 잰다.  python tests/bench_fingerprint.py [폴더]

"2.7배 빨라졌다" 같은 말은 재현할 수 있어야 뜻이 있다. 폴더를 주지 않으면
고정 표본으로 재고, 실제 책 폴더를 주면 그것으로 잰다.

같이 확인하는 것: 빨라지면서 지문 값이 달라지지 않았는가. 지문이 바뀌면
캐시가 전부 어긋나고 중복 판정이 달라진다 - 속도보다 이쪽이 중요하다.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import file_tidier_backend as B  # noqa: E402

FOLDER = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "fixture")


def load_texts(folder, limit=60):
    texts = []
    for dirpath, _dirs, files in os.walk(folder):
        for name in sorted(files):
            extension = os.path.splitext(name)[1].lower()
            if extension not in (".txt", ".epub"):
                continue
            path = os.path.join(dirpath, name)
            try:
                texts.append((name, B.text_from_bytes(open(path, "rb").read(), extension)))
            except Exception:
                continue
            if len(texts) >= limit:
                return texts
    return texts


def main():
    if not os.path.isdir(FOLDER):
        print("폴더가 없습니다: %s" % FOLDER)
        return 1
    texts = load_texts(FOLDER)
    if not texts:
        print("잴 본문이 없습니다: %s" % FOLDER)
        return 1

    # 디스크 영향을 빼고 계산만 잰다. 가장 빠른 회차를 쓴다.
    best = float("inf")
    digests = []
    for round_index in range(3):
        start = time.perf_counter()
        current = [B.sentence_fingerprint(text)[0] for _name, text in texts]
        best = min(best, time.perf_counter() - start)
        if round_index == 0:
            digests = current

    total_chars = sum(len(text) for _name, text in texts)
    print("표본      : %s" % FOLDER)
    print("본문      : %d개, %s자" % (len(texts), format(total_chars, ",")))
    print("지문 계산 : %.2f초  ->  초당 %.1f개" % (best, len(texts) / best))
    print()
    print("지문 앞 3개 (바꾸기 전후 대조용):")
    for (name, _text), digest in list(zip(texts, digests))[:3]:
        print("  %-46s %s" % (name[:44], digest[:24]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
