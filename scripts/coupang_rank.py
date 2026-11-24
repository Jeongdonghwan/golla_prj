"""쿠팡 종류(data/coupang_kinds.json)의 네이버 월 검색수 → keywords/coupang_kind_volume.csv (카드 쓰는 순서용, DB 는 안 바꾼다).

    python scripts/coupang_rank.py
"""
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app import create_app  # noqa: E402
import kw_research as kr  # noqa: E402

if __name__ == "__main__":
    cfg = json.loads((ROOT / "data" / "coupang_kinds.json").read_text("utf-8"))
    kinds = [(label, sec, k) for label, s in cfg.items() for sec, ks in s["sections"].items() for k in ks]
    vol = {}
    with create_app().app_context():
        for i in range(0, len(kinds), 5):
            names = [k for _l, _s, k in kinds[i:i + 5]]
            try:
                got = {kr.norm(kw): v for kw, v in kr.fetch(names)}
                vol.update({n: got.get(kr.norm(n), 0) for n in names})
            except Exception as e:  # 한 묶음 실패는 건너뛴다
                print("실패", names, e)
            time.sleep(1)
    with open(ROOT / "keywords" / "coupang_kind_volume.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["site", "section", "kind", "monthly"])
        for label, sec, k in sorted(kinds, key=lambda x: -vol.get(x[2], 0)):
            w.writerow([label, sec, k, vol.get(k, 0)])
    print("종류", len(kinds), "· 검색수 받은 것", sum(1 for v in vol.values() if v), "· 합", sum(vol.values()))
