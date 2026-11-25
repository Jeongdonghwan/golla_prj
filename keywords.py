"""키워드 페이지(F3): 승인된 사실 카드의 수식어로 종류 × 수식어 조합 페이지를 만든다. 멱등(다시 돌리면 없는 것만 더한다).

등급  A  종류 허브 · 종류×의도 · 종류×대표 공간(카드의 앞 2개)
      B  종류×수식어 1개 · 질문 글
      C  수식어 2개(공간·대상·상황×특징, 공간·대상×가격대, 브랜드×특징·의도)
      D  수식어 3개(공간·대상×특징×가격대)
우선순위 = 등급 + 계절(난방·공기 상품군) + 종류 평균 수수료. 새로 만든 페이지는 기존 대기열 뒤에(같은 점수면 id 순).
차이  B  방식 두 개 비교(카드 methods 쌍): /{종류}/{방식}-{방식}-차이/
검색  A/B 실제 검색 키워드(card.search — 네이버 검색광고 연관 키워드, 월 1,000+ = A): /{종류}/{말}/  조합하지 않는다
주소: /{종류}/ · /{종류}/{수식어}/ · /{종류}/{수식어}-{수식어}/ · /{종류}/qna/{슬러그}/ · 상품군 허브는 메인(/).
"""
import json
import re
from collections import Counter

import products
from db import ex, q, q1

AXIS_DIM = {"space": 1, "persona": 2, "situation": 3, "feature": 4, "price": 5, "intent": 6, "brand": 8}
PRE = ("space", "persona", "situation", "brand")      # 키워드에서 종류 이름 앞
PAIRS = [("space", "feature"), ("persona", "feature"), ("situation", "feature"), ("space", "price"), ("persona", "price"),
         ("brand", "feature"), ("brand", "intent")]
TRIPLES = [("space", "feature", "price"), ("persona", "feature", "price")]
GRADE_SCORE = {"A": 300, "B": 200, "C": 100, "D": 0}
SEASON = {1: 40, 2: 40}   # 겨울: 난방·공기 상품군 먼저(계절 바뀌면 여기만 고친다)
MODS = json.loads((products.ROOT / "data" / "modifiers.json").read_text("utf-8"))


def term_id(axis, term):
    """공통 수식어 행(vertical_id NULL). 특징·가격대·브랜드는 상품 태그와 이어진다(브랜드 태그는 '@브랜드')."""
    dim = AXIS_DIM[axis]
    slug = products.slugify(term)
    r = q1("SELECT id FROM keyword_terms WHERE vertical_id IS NULL AND dimension_id=%s AND slug=%s", (dim, slug))
    if r:
        return r["id"]
    tag = {"feature": term, "price": term, "brand": "@" + term}.get(axis)
    if axis in ("space", "persona"):
        tag = next((m.get("tag") for m in MODS[axis] if m["term"] == term), None)
    return ex("INSERT INTO keyword_terms (vertical_id, dimension_id, term, slug, tag) VALUES (NULL,%s,%s,%s,%s)", (dim, term, slug, tag))


def keyword(kind, mods):
    """mods = [(axis, term)] → '원룸 무선 가습기 3만 원 이하'"""
    pre = [t for a, t in mods if a in PRE]
    feat = [t for a, t in mods if a == "feature"]
    post = [t for a, t in mods if a in ("price", "intent")]
    return " ".join(pre + feat + [kind] + post)


def method_label(name, kind):
    """카드 방식 이름 → 검색어용 짧은 이름: '콘센트형(교류) 전기방석' → '콘센트형'."""
    s = re.sub(r"\(.*?\)", "", name).replace(kind, "").replace(" 방식", "")
    return re.sub(r"\s+", " ", s).strip(" ·") or name


def vs_pairs(card, kind):
    """방식 차이 페이지 쌍: [(i, j, '전기방석 콘센트형 USB형 차이', 'url 조각')]."""
    ls = [method_label(m["name"], kind) for m in card.get("methods", [])]
    return [(i, j, f"{kind} {ls[i]} {ls[j]} 차이", f"{products.slugify(ls[i])}-{products.slugify(ls[j])}-차이")
            for i in range(len(ls)) for j in range(i + 1, len(ls)) if ls[i] != ls[j]]


def _insert(site_id, v, page_type, mods, url, parent, grade, prio, kw=None):
    ids = [term_id(a, t) for a, t in mods] + [None] * (3 - len(mods))
    have = q1("SELECT id FROM keyword_combos WHERE site_id=%s AND url_path=%s", (site_id, url))
    if have:
        return have["id"], 0
    cid = ex("""INSERT INTO keyword_combos (site_id, vertical_id, page_type, mod1_id, mod2_id, mod3_id, primary_keyword, url_path,
                parent_combo_id, grade, priority_score, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'draft')""",
             (site_id, v["id"], page_type, *ids, kw or keyword(v["name"], mods), url, parent, grade, prio))
    return cid, 1


def group_hub(site):
    cp = q1("SELECT 1 x FROM site_settings WHERE site_id=%s AND `key`='network' AND `value`='coupang'", (site["id"],))
    goods = q1("SELECT `value` FROM site_settings WHERE site_id=%s AND `key`='goods'", (site["id"],))
    cid, _n = _insert(site["id"], q1("SELECT * FROM verticals WHERE site_id=%s ORDER BY id LIMIT 1", (site["id"],)), "group_hub", [], "/",
                      None, "A", 1000, kw=site["group_name"] + " " + (goods["value"] if goods else ("용품" if cp else "가전")) + " 고르는 기준")
    return cid


def build_kind(v, hub_parent):
    """종류 하나의 페이지 전부. 만든 수를 등급별로."""
    card = json.loads(v["card"])
    mods = {ax: list((card["mods"].get(ax) or {}).keys()) for ax in AXIS_DIM if ax != "intent"}
    mods["intent"] = list(card["intents"].keys())
    fees = [float(o["fee"]) + float(o["fee_extra"]) for o in products.kind_offers(v)]
    base = SEASON.get(v["site_id"], 0) + (sum(fees) / len(fees) if fees else 0)
    made, root = Counter(), v["url_base"]
    seq = [0]

    def add(page_type, ms, url, parent, grade, kw=None):
        seq[0] += 1
        cid, n = _insert(v["site_id"], v, page_type, ms, url, parent, grade, round(GRADE_SCORE[grade] + base - seq[0] * 0.0001, 4), kw)
        made[grade] += n
        return cid

    hub = add("kind_hub", [], f"{root}/", hub_parent, "A", kw=v["name"])
    for t in mods["intent"]:
        add("kind_mod", [("intent", t)], f"{root}/{products.slugify(t)}/", hub, "A")
    single = {}
    for ax in ("space", "persona", "situation", "feature", "price", "brand"):
        for i, t in enumerate(mods[ax]):
            grade = "A" if ax == "space" and i < 2 else "B"
            single[(ax, t)] = add("kind_mod", [(ax, t)], f"{root}/{products.slugify(t)}/", hub, grade)
    pair_ids = {}
    for a, b in PAIRS:
        for ta in mods[a]:
            for tb in mods[b]:
                pair_ids[(a, ta, b, tb)] = add("kind_mod", [(a, ta), (b, tb)], f"{root}/{products.slugify(ta)}-{products.slugify(tb)}/",
                                               single[(a, ta)], "C")
    for a, b, c in TRIPLES:
        for ta in mods[a]:
            for tb in mods[b]:
                for tc in mods[c]:
                    add("kind_mod", [(a, ta), (b, tb), (c, tc)],
                        f"{root}/{products.slugify(ta)}-{products.slugify(tb)}-{products.slugify(tc)}/", pair_ids[(a, ta, b, tb)], "D")
    for p in card.get("posts", []):
        add("question", [], f"{root}/qna/{p['slug']}/", hub, "B", kw=p["title"])
    for it in card.get("search", []):
        add("search", [], f"{root}/{products.slugify(it['term'])}/", hub, "A" if it.get("monthly", 0) >= 1000 else "B", kw=it["kw"])
    for _i, _j, kw, slug in vs_pairs(card, v["name"]):  # 방식 차이(비교 검색어) — 판매 의도가 높아 B
        add("versus", [], f"{root}/{slug}/", hub, "B", kw=kw)
    return made


def rebuild():
    """카드가 있는 활성 종류 전부. {상품군: Counter(등급)}"""
    out = {}
    for site in q("SELECT * FROM sites ORDER BY id"):
        gh = group_hub(site)
        c = Counter()
        for v in q("SELECT * FROM verticals WHERE site_id=%s AND is_active=1 AND card IS NOT NULL ORDER BY id", (site["id"],)):
            c += build_kind(v, gh)
        out[site["host"].split(".")[0]] = c
    return out
