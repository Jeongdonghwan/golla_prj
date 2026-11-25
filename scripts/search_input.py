"""키워드 풀(keywords/pool_<slug>.csv) → 세션 작성용 입력 content/search/_input/<slug>.json.

    python scripts/search_input.py [--top 40] [--min 1000] [--only 전기매트,가습기]   (--min = 월 검색수 하한 — 차수별로 자른다)

종류마다 '상품 있음' 키워드를 검색수 순으로, 이미 쓴 말(content/search/<slug>.json)·이미 있는 페이지 주소는 빼고 top 개.
각 항목: kw(띄어 쓴 페이지 키워드) · term(종류명을 뺀 말) · monthly · products(그 말이 이름에 든 우리 상품, 사실 근거).
세션은 이 입력을 보고 term 마다 3문장을 써서 content/search/<slug>.json 의 items 에 덧붙인다(prompts/search.md).
"""
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
import db  # noqa: E402
import products  # noqa: E402

FILLER = re.compile(r"추천|가성비|좋은|인기|순위|후기|가격|할인|특가|최저가|구매|제품")


def norm(s):
    return re.sub(r"[\s·\-]+", "", s or "").lower()


def term_of(kw, kind):
    """'보국전기매트' → '보국', '전기매트1인용' → '1인용', '가성비무선청소기추천' → ''(군말뿐)."""
    t = norm(kw).replace(norm(kind), " ")
    return re.sub(r"\s+", " ", FILLER.sub(" ", t)).strip()


def main():
    args = sys.argv[1:]
    top = int(args[args.index("--top") + 1]) if "--top" in args else 40
    only = set(args[args.index("--only") + 1].split(",")) if "--only" in args else None
    vmin = int(args[args.index("--min") + 1]) if "--min" in args else 0
    import cards
    out = ROOT / "content" / "search" / "_input"
    out.mkdir(parents=True, exist_ok=True)
    n_all = 0
    with create_app().app_context():
        for v in db.q("SELECT * FROM verticals WHERE is_active=1 AND card IS NOT NULL ORDER BY site_id, id"):
            key = cards.key(v)   # home 은 'home/냉장고' — 네이버 냉장고와 섞이지 않게(카드·보강과 같은 키)
            pool = ROOT / "keywords" / f"pool_{key.replace('/', '__')}.csv"
            if not pool.exists() or (only and v["name"] not in only):
                continue
            done_p = ROOT / "content" / "search" / f"{key}.json"
            done = {products.slugify(i["term"]) for i in json.loads(done_p.read_text("utf-8-sig"))["items"]} if done_p.exists() else set()
            urls = {r["url_path"] for r in db.q("SELECT url_path FROM keyword_combos WHERE vertical_id=%s", (v["id"],))}
            names = [o["name"] for o in products.kind_offers(v)]
            skip_p = ROOT / "content" / "search" / "_skip.json"   # 세션이 이미 보고 뺀 말(동의어·상품 불일치) — 다시 넣지 않는다
            skip = set(json.loads(skip_p.read_text("utf-8")).get(key, [])) if skip_p.exists() else set()
            items, seen = [], set(done) | skip
            for row in csv.DictReader(pool.open(encoding="utf-8-sig")):
                if row["servable"] != "1" or row["type"] not in ("브랜드·특징(상품 있음)", "조건", "비교") or int(row["monthly"]) < vmin:
                    continue
                term = term_of(row["keyword"], v["name"])
                s = products.slugify(term)
                if len(norm(term)) < 2 or s in seen or f"{v['url_base']}/{s}/" in urls:
                    continue
                seen.add(s)
                hits = [n[:80] for n in names if norm(term) in norm(n)][:8]
                if not hits:
                    continue
                items.append({"kw": f"{term} {v['name']}", "term": term, "monthly": int(row["monthly"]), "keyword_raw": row["keyword"], "products": hits})
                if len(items) >= top:
                    break
            if items:
                (out / f"{key}.json").parent.mkdir(parents=True, exist_ok=True)
                (out / f"{key}.json").write_text(json.dumps({"kind": v["name"], "slug": v["slug"], "key": key, "items": items}, ensure_ascii=False, indent=1), "utf-8")
                n_all += len(items)
                print(f"{v['name']}: {len(items)}")
    print("합계", n_all)


if __name__ == "__main__":
    main()
