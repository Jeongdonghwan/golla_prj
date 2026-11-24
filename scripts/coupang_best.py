"""쿠팡 카테고리 베스트 전체 받기 → data/coupang/best_<id>.json + 요약(DB 는 건드리지 않는다).

    python scripts/coupang_best.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coupang_test as c  # noqa: E402

CATS = {1001: "여성패션", 1002: "남성패션", 1010: "뷰티", 1011: "출산·유아동", 1012: "식품", 1013: "주방용품", 1014: "생활용품",
        1015: "홈인테리어", 1016: "가전디지털", 1017: "스포츠·레저", 1018: "자동차용품", 1019: "도서·음반", 1020: "완구·취미",
        1021: "문구·오피스", 1024: "헬스·건강식품", 1025: "국내여행", 1026: "해외여행", 1029: "반려동물용품", 1030: "유아동패션"}

if __name__ == "__main__":
    out = c.ROOT / "data" / "coupang"
    out.mkdir(parents=True, exist_ok=True)
    for cid, name in CATS.items():
        r = c.call(f"{c.BASE}/products/bestcategories/{cid}", "limit=100")
        if "data" not in r:
            print(cid, name, "실패:", json.dumps(r, ensure_ascii=False)[:300])
            break  # 한도에 걸리면 더 두드리지 않는다
        (out / f"best_{cid}.json").write_text(json.dumps(r["data"], ensure_ascii=False, indent=1), "utf-8")
        d = r["data"]
        print(f"{cid} {name}: {len(d)}개 · 로켓 {sum(p['isRocket'] for p in d)} · 예: " + " / ".join(p["productName"][:22] for p in d[:4]))
        time.sleep(2)
