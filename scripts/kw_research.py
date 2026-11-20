"""실제 검색 키워드 모으기(네이버 검색광고 키워드도구) → 종류별 후보 풀 keywords/pool_<slug>.csv + 요약.

    python scripts/kw_research.py [--only 전기매트,가습기] [--min 10]

종류마다 힌트(종류명 + 상품 많은 브랜드 4개)로 연관 키워드·월간 검색수를 받고,
종류명이 들어간 것만 남겨 이미 있는 페이지 키워드와 겹치는 것을 뺀 뒤 유형(브랜드/문제·사용법/비교/조건)으로 나눈다.
DB·페이지는 바꾸지 않는다(측정용). 키: NAVER_AD_API_KEY/SECRET/CUSTOMER_ID(.env).
"""
import base64
import csv
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
import db  # noqa: E402

PROBLEM = re.compile(r"전기세|전기요금|소음|냄새|곰팡이|세척|청소|사용법|방법|고장|에러|안\s?켜|안\s?됨|교체|필터|소모품|관리|수리|as|a/s|설치|보관|주기|하얀\s?가루|물때|석회", re.I)
VERSUS = re.compile(r"차이|비교|vs|장단점")


def norm(s):
    return re.sub(r"[\s·\-]+", "", s or "").lower()


def fetch(hints):
    key, secret, customer = (os.environ.get(k) for k in ("NAVER_AD_API_KEY", "NAVER_AD_SECRET", "NAVER_AD_CUSTOMER_ID"))
    if not (key and secret and customer):
        raise SystemExit("NAVER_AD_API_KEY / NAVER_AD_SECRET / NAVER_AD_CUSTOMER_ID 가 .env 에 없습니다")
    ts, uri = str(int(time.time() * 1000)), "/keywordstool"
    sig = base64.b64encode(hmac.new(secret.encode(), f"{ts}.GET.{uri}".encode(), hashlib.sha256).digest()).decode()
    url = f"https://api.searchad.naver.com{uri}?hintKeywords={quote(','.join(norm(h) for h in hints[:5]))}&showDetail=1"
    req = urllib.request.Request(url, headers={"X-Timestamp": ts, "X-API-KEY": key, "X-Customer": customer, "X-Signature": sig})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    num = lambda v: 5 if isinstance(v, str) else int(v or 0)  # noqa: E731  "< 10" → 5
    return [(k["relKeyword"], num(k.get("monthlyPcQcCnt")) + num(k.get("monthlyMobileQcCnt"))) for k in data.get("keywordList", [])]


FILLER = re.compile(r"추천|가성비|좋은|인기|순위|후기|가격|할인|특가|최저가|구매|제품|브랜드|용$")


def bucket(kw, kind, vocab, names):
    """유형 + 우리가 실제로 답할 수 있는지(servable): 종류명·군말을 뺀 나머지가 카드 수식어이거나 우리 상품명에 있는 말이면 OK."""
    rest = FILLER.sub("", norm(kw).replace(norm(kind), ""))
    if VERSUS.search(kw):
        return "비교", True
    if PROBLEM.search(rest):
        return "문제·사용법", True
    if not rest:
        return "조건", True
    if rest in vocab:
        return "조건", True
    if len(rest) >= 2 and rest in names:
        return "브랜드·특징(상품 있음)", True
    return "상품 없음·확인 필요", False


def main():
    args = sys.argv[1:]
    only = set(args[args.index("--only") + 1].split(",")) if "--only" in args else None
    vmin = int(args[args.index("--min") + 1]) if "--min" in args else 10
    out = ROOT / "keywords"
    out.mkdir(exist_ok=True)
    with create_app().app_context():
        kinds = db.q("SELECT id, name, slug FROM verticals WHERE is_active=1 AND card IS NOT NULL ORDER BY id")
        have = {norm(r["primary_keyword"]) for r in db.q("SELECT primary_keyword FROM keyword_combos")}
        total = Counter()
        for k in kinds:
            if only and k["name"] not in only:
                continue
            brands = [r["brand"] for r in db.q("SELECT brand, COUNT(*) n FROM offers WHERE status='active' AND vertical_id=%s AND brand IS NOT NULL "
                                                  "GROUP BY brand ORDER BY n DESC LIMIT 12", (k["id"],))]
            card = json.loads(db.q1("SELECT card FROM verticals WHERE id=%s", (k["id"],))["card"] or "{}")
            vocab = {norm(t) for ax in card.get("mods", {}).values() for t in ax}
            names = norm(" ".join(r["name"] for r in db.q("SELECT name FROM offers WHERE status='active' AND vertical_id=%s", (k["id"],))))
            rows = {}
            pool = out / f"pool_{k['slug']}.csv"
            seeds = [[k["name"]], [f"{b} {k['name']}" for b in brands[:4]]]
            if "--deep" in args:  # 카드의 모든 수식어·방식·브랜드를 힌트로(5개씩) + 기존 풀과 합침
                from keywords import method_label
                terms = [t for ax in card.get("mods", {}).values() for t in ax] + [method_label(m["name"], k["name"]) for m in card.get("methods", [])] + brands[4:]
                hs = [f"{t} {k['name']}" for t in dict.fromkeys(terms)]
                seeds = [hs[i:i + 5] for i in range(0, len(hs), 5)]
                if pool.exists():
                    rows = {r["keyword"]: int(r["monthly"]) for r in csv.DictReader(pool.open(encoding="utf-8-sig"))}
            for hints in seeds:
                if not hints:
                    continue
                try:
                    for kw, vol in fetch(hints):
                        rows[kw] = max(vol, rows.get(kw, 0))
                except Exception as e:  # 한 종류 실패는 건너뛴다
                    print(f"  {k['name']} 실패: {e}")
                time.sleep(1)
            kn = norm(k["name"])
            keep = sorted(((kw, v) for kw, v in rows.items() if v >= vmin and kn in norm(kw) and norm(kw) not in have), key=lambda x: -x[1])
            c = Counter()
            with open(pool, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["keyword", "monthly", "type", "servable"])
                for kw, v in keep:
                    t, ok = bucket(kw, k["name"], vocab, names)
                    c[t] += 1
                    c["_ok"] += ok
                    w.writerow([kw, v, t, int(ok)])
            total.update(c)
            total["받은 키워드"] += len(rows)
            print(f"{k['name']}: 받음 {len(rows)} · 새 후보 {len(keep)} {dict(c)} · 검색수 합 {sum(v for _, v in keep):,}")
        print("합계:", dict(total))


if __name__ == "__main__":
    main()
