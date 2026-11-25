"""상품 선택, 콘텐츠 조립, 품질 게이트."""
import hashlib
import json
import random
import re
from pathlib import Path
from urllib.parse import quote

from flask import current_app, g, render_template

import composer
from og import card_path
import products
from db import js, ops, queries as dbq

STATIC_PATHS = ["/운영사/", "/광고고지/", "/편집기준/", "/개인정보처리방침/", "/이용약관/", "/면책조항/"]
CARD_COUNT_HUB, CARD_COUNT = 8, 6     # 상품 카드 수: 허브 8, 키워드·질문 글 6
UNIQUENESS_MAX = 0.30
FORBIDDEN = [w for w in (Path(__file__).parent / "lint" / "forbidden.txt").read_text("utf-8").split("\n") if w.strip()]
# 가격·평점·리뷰 수를 숫자로 쓰지 않는다(가격대 수식어 '3만 원 이하'·'2만 원대'는 허용)
NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+\s*원|\d+\s*원(?!대| 이하| 이상| 안팎)|평점\s*\d|별점\s*\d|리뷰\s*[\d,]+\s*(?:개|건)|\d+\s*% 할인|할인율\s*\d")
HUB_TYPES = ("group_hub", "kind_hub")
# 조건 수식어(공간·대상·상황) → 상품명에서 볼 낱말. 같은 종류라도 조건마다 상품 조합이 달라진다(2026-11-25 사용자 요청)
HINTS = {x["term"]: x["hint"] for ax in ("space", "persona", "situation")
         for x in json.loads((Path(__file__).parent / "data" / "modifiers.json").read_text("utf-8"))[ax] if x.get("hint")}


def lint_text(text, extra=()):
    """금지 표현 목록에 걸린 단어들. 빈 리스트면 통과."""
    return [w for w in dict.fromkeys([*FORBIDDEN, *extra]) if w in (text or "")]


def lint(text):
    """린트 결과(page_contents.lint_result 에 저장되는 형태)."""
    forbidden, numbers = lint_text(text), NUMBER_RE.findall(text or "")
    return {"ok": not forbidden and not numbers, "forbidden": forbidden, "numbers": numbers}


def site_base():
    """현재 요청의 사이트 기준 주소. web.site() / jobs._sites() 가 g.base_url 을 채운다."""
    return g.get("base_url") or current_app.config["BASE_URL"]


def abs_url(path):
    return site_base() + quote(path)


# ── 상품 선택 ─────────────────────────────────────────────────────────────
def combo_hints(combo):
    """페이지 수식어 중 공간·대상·상황 → 상품명 낱말(HINTS)."""
    ids = [combo.get(k) for k in ("mod1_id", "mod2_id", "mod3_id") if combo.get(k)]
    return [w for t in map(dbq.term, ids) if t for w in HINTS.get(t["term"], [])]


def hint_in(word, name):
    """조건 낱말이 상품명 낱말의 앞부분에 있는가('킹' → '킹사이즈'는 맞고 '베이킹'은 아님)."""
    return any(tok.startswith(word) for tok in re.split(r"[\s,()\[\]/+·]+", name))


def combo_tags(combo):
    """페이지 수식어 중 상품 태그와 이어진 것(특징·가격대·브랜드)."""
    ids = [combo.get(k) for k in ("mod1_id", "mod2_id", "mod3_id") if combo.get(k)]
    return [t["tag"] for t in map(dbq.term, ids) if t and t["tag"]]


def select_offers(site, combo, slot, n, vertical=None):
    """(1) 같은 종류(+하위) (2) 페이지 특징 태그와 맞는 상품 먼저 (2-1) 조건 낱말(HINTS)이 상품명에 든 상품 (3) 점수 가중 셔플(페이지 시드) (4) 모자라면 같은 상품군 형제 종류로 채움."""
    vertical = vertical or dbq.vertical(combo["vertical_id"])
    tags = set(combo_tags(combo))
    rng = random.Random(hashlib.sha256(f"{site['id']}|{combo['id']}|{slot}|{site['ranking_version']}".encode()).hexdigest())

    def ranked(cands):  # 가중 셔플(Efraimidis-Spirakis). cands 가 id 순이라 결정적
        top = max((float(o["score"]) for o in cands), default=0) or 1.0
        return sorted(cands, key=lambda o: rng.random() ** (1.0 / max(float(o["score"]) / top * (o["manual_weight"] or 1), 0.01)), reverse=True)

    if combo["page_type"] == "group_hub":  # 상품군 허브: 종류마다 점수 1위 상품
        tops = [max(c, key=lambda o: float(o["score"])) for v in dbq.verticals(site["id"]) if not v["parent_id"] for c in [products.kind_offers(v)] if c]
        return ranked(tops)[:n]
    cands = products.kind_offers(vertical)
    match = [o for o in cands if tags and tags <= set(js(o["tags"], []))]
    if combo["page_type"] in ("versus", "search"):  # 방식 차이·검색 키워드: 상품명에 그 낱말이 들어간 상품 먼저
        words = {w for w in re.split(r"[\s·]+", combo["primary_keyword"].replace(vertical["name"], "")) if len(w) >= 2 and w != "차이"}
        match = [o for o in cands if any(w in o["name"] for w in words)]
    seen = {o["id"] for o in match}
    hints = combo_hints(combo)   # 조건 낱말이 상품명에 든 상품을 그다음에(원룸 → 슬림·미니·싱글, 신혼부부 → 퀸·킹·세트)
    near = [o for o in cands if o["id"] not in seen and any(hint_in(w, o["name"]) for w in hints)] if hints else []
    seen |= {o["id"] for o in near}
    out = ranked(match) + ranked(near) + ranked([o for o in cands if o["id"] not in seen])
    if len(out) < n:  # 모자라면 같은 상품군의 형제 종류(같은 부모)로 채운다 — 빈 페이지 없음
        root = vertical["parent_id"] or vertical["id"]
        sib = [v for v in dbq.verticals(site["id"]) if v["id"] != vertical["id"] and (v["parent_id"] or v["id"]) == root and not v["parent_id"]]
        have = {o["id"] for o in out}
        out += ranked([o for v in sib for o in products.kind_offers(v) if o["id"] not in have])
    return out[:n]


def offer_pick(offer, combo, card):
    """상품별 추천 글: 상품명에서 뽑은 특징·브랜드 태그 → 그 종류 카드의 수식어 문장(사실 문장, 이미 린트 통과).
    페이지 조건과 맞는 특징을 먼저. 페이지·상품마다 시드가 달라 같은 상품도 페이지마다 다른 문장이 나온다."""
    tags = js(offer["tags"], [])
    mods = (card or {}).get("mods", {})
    page = set(combo_tags(combo))
    feats = sorted((t for t in tags if t in mods.get("feature", {})), key=lambda t: t not in page)
    brand = next((t[1:] for t in tags if t.startswith("@") and t[1:] in mods.get("brand", {})), None)
    rng = random.Random(f"{combo['id']}|{offer['id']}|pick")
    say = [rng.choice(mods["feature"][f]) for f in feats[:2]]
    if brand:
        say.append(rng.choice(mods["brand"][brand]))
    if len(say) < 2 and card:   # 특징 문단이 없는 상품: 상품명에 방식 이름(양문형·접이식 등)이 있으면 그 방식의 장점·맞는 경우(2026-11-25)
        name = offer["name"].replace(" ", "")
        for m in card.get("methods", []):
            if len(say) < 2 and _method_in(m["name"], name, card.get("kind", "")):
                feats.append(m["name"])
                say += [s for s in (m["pros"], m["fit"]) if s][:2 - len(say)]
    return {"feats": feats[:4], "say": say[:3]}


def _method_in(method, name, kind=""):
    """방식 이름(예: '양문형 냉장고', '통돌이(일반) 세탁기')의 핵심 낱말이 상품명에 있는가. 종류 이름에 든 낱말(바디로션의 '로션')은 뜻이 없어 뺀다."""
    for w in re.split(r"[\s·/]+", re.sub(r"\(.*?\)", "", method))[:1] + re.split(r"·", method.split()[0])[1:2]:   # 첫 낱말(과 '소형·미니형'의 둘째)
        w = w[:-1] if w.endswith("형") and len(w) > 2 else w   # '양문형' → '양문', '스탠드형' → '스탠드'
        if len(w) >= 2 and w in name and w not in kind.replace(" ", ""):
            return True
    return False


def offer_reason(offer, combo, card=None):
    """상품 카드의 '이 조건과 맞는 점': ① 상품 태그 중 페이지 조건과 겹치는 것 → ② 없으면 상품명에 그대로 적힌
    카드 특징·기준 낱말(사실 — 상품명에 있는 말만). 둘 다 없으면 빈 값(2026-11-24: 쿠팡 상품 대부분이 비어 보완)."""
    name = offer["name"].replace(" ", "")
    hit = [t.lstrip("@") for t in combo_tags(combo) if t in js(offer["tags"], [])] + [w for w in dict.fromkeys(combo_hints(combo)) if hint_in(w, offer["name"])]   # 조건 낱말(원룸 → 슬림 등)이 상품명에 있으면 그것부터. 브랜드 태그 '@삼성' → '삼성'
    hit = list(dict.fromkeys(hit))[:3]
    if not hit and card:
        words = list((card.get("mods", {}).get("feature") or {})) + [c["item"] for c in card.get("criteria", [])]
        hit = [w for w in dict.fromkeys(words) if len(w) >= 2 and w.replace(" ", "") in name]
        hit += [m["name"] for m in card.get("methods", []) if _method_in(m["name"], name, card.get("kind", ""))]   # 방식(양문형 등)
        hit = [w for w in hit if not any(w != x and w in x for x in hit)][:3]   # '용량'·'대용량' 이면 긴 쪽만
    return " · ".join(hit)


# ── 조립 ──────────────────────────────────────────────────────────────────
def _crumbs(combo):
    chain, cur = [], combo
    while cur:
        chain.append({"name": cur["primary_keyword"], "path": cur["url_path"]})
        cur = dbq.combo(cur["parent_combo_id"]) if cur["parent_combo_id"] else None
    chain.append({"name": "홈", "path": "/"})
    return [{**c, "url": abs_url(c["path"])} for c in reversed(chain) if c["path"]]


def _links(combo):
    links = {"parent": [], "children": dbq.children(combo["id"]),
             "siblings": dbq.siblings(combo) if combo["parent_combo_id"] else [],
             "core": dbq.kind_core(combo) if combo["page_type"] != "group_hub" else []}
    parent = dbq.combo(combo["parent_combo_id"]) if combo["parent_combo_id"] else None
    if parent and parent["url_path"]:
        links["parent"] = [parent]
    targets = [l for group in links.values() for l in group]
    variants = ops.anchor_variants([l["id"] for l in targets])
    for l in targets:  # 앵커 변형: 출발 페이지마다 다른 앵커를 결정적으로
        opts = variants.get(l["id"])
        l["anchor"] = opts[int(hashlib.md5(f"{combo['id']}|{l['id']}".encode()).hexdigest(), 16) % len(opts)] if opts else l["primary_keyword"]
    return links


def assemble(site, combo):
    """렌더에 필요한 모든 것. 승인된 콘텐츠가 없으면 None."""
    content = dbq.content(combo["id"], combo["content_version"])
    if not content:
        return None
    vertical = dbq.vertical(combo["vertical_id"])
    built = composer.compose(combo, vertical, site, (js(content["blocks"], {}) or {}).get("v", 0) if content["model"] == "assembled" else 0)
    if not built:
        return None
    n = CARD_COUNT_HUB if combo["page_type"] in HUB_TYPES else CARD_COUNT
    offers = select_offers(site, combo, "cards", n, vertical)
    vs = dbq.verticals(site["id"])
    names = {v["id"]: v["name"] for v in vs}
    cards = {v["id"]: js(v["card"], {}) for v in vs}
    for o in offers:
        o["reason"] = offer_reason(o, combo, cards.get(o["vertical_id"]))
        o["kind"] = names.get(o["vertical_id"], "")
        o["pick"] = offer_pick(o, combo, cards.get(o["vertical_id"]))
    kinds, posts = [], []
    if combo["page_type"] == "group_hub":  # 메인(블로그형): 종류별 가이드(상품 사진 또는 OG 카드 + 설명 + A등급 하위 글) · 최근 글
        by_id = {v["id"]: v for v in vs}
        for k in dbq.published_with_text(site["id"], ("kind_hub",), 200):
            imgs = sorted((o for o in products.kind_offers(by_id[k["vertical_id"]]) if o["image"]), key=lambda o: -float(o["score"])) if k["vertical_id"] in by_id else []
            kinds.append({**k, "name": names.get(k["vertical_id"], k["primary_keyword"]), "image": imgs[0]["image"] if imgs else "",
                          "topics": dbq.kind_core(k, 8), "section": (by_id.get(k["vertical_id"]) or {}).get("section") or "", "vid": k["vertical_id"]})
        if site["id"] in dbq.solo_ids():   # 독립 홈페이지: 분야 순서(종류 행 순서)대로 묶어 한 페이지로
            kinds.sort(key=lambda k: k["vid"])
        posts = dbq.published_rec(site["id"], 12)  # 판매 의도 페이지(A등급 키워드 페이지, 종류마다 하나씩)
        for p in posts:
            p["label"] = names.get(p["vertical_id"], "") + " 추천"
    ctx = {"combo": combo, "content": content, "page": built, "blocks": built["sections"], "vertical": vertical, "kinds": kinds, "posts": posts,
           "offers_main": offers, "links": _links(combo), "crumbs": _crumbs(combo),
           "author": ops.default_author(site), "og_image": card_path(combo), "faqs": built["faqs"],
           "canonical": abs_url(combo["url_path"]), "title": built["title"], "description": built["meta"]}
    ctx["robots"] = "noindex,follow" if combo["status"] == "noindex" or not offers else None
    return ctx


def render_combo(site, combo):
    cached = dbq.page(combo["id"])
    ctx = assemble(site, combo)
    if ctx is None:
        return None
    html = render_template(f"pages/{combo['page_type']}.html", **ctx)
    # 상품 순서는 해시에 넣지 않는다 — 상품 교체로 lastmod 가 흔들리지 않게(본문이 바뀔 때만 갱신)
    content_hash = hashlib.md5(json.dumps([ctx["page"], ctx["faqs"]], ensure_ascii=False).encode()).hexdigest()
    noindex = int(ctx["robots"] is not None)
    if cached and cached["content_hash"] == content_hash and cached["noindex"] == noindex:
        return html
    dbq.upsert_page(combo["id"], site["cache_version"], site["ranking_version"], content_hash, 1 - noindex, noindex)
    if not cached or not cached["content_hash"]:
        dbq.touch_combo(combo["id"], only_if_null=True)
    elif cached["content_hash"] != content_hash:
        dbq.touch_combo(combo["id"])
        dbq.queue_indexnow(site["id"], ctx["canonical"], "update")
    return html


def shown_offers(site, combo):
    """노출 집계용: 이 콤보에 지금 표시되는 (slot, offer_id)."""
    n = CARD_COUNT_HUB if combo["page_type"] in HUB_TYPES else CARD_COUNT
    return [("cards", o["id"]) for o in select_offers(site, combo, "cards", n)]


# ── 품질 게이트 ───────────────────────────────────────────────────────────
def gate(site, combo):
    """publish 전 검사. 실패 사유 리스트(빈 리스트 = 통과). 우회 경로를 만들지 말 것."""
    ctx = assemble(site, combo)
    if ctx is None:
        return ["승인된 콘텐츠 없음"]
    fails, c = [], ctx["content"]
    if c["uniqueness_score"] is None:
        fails.append("uniqueness 미계산")
    elif float(c["uniqueness_score"]) > UNIQUENESS_MAX:
        fails.append(f"uniqueness {float(c['uniqueness_score']):.2f} > {UNIQUENESS_MAX}")
    lint_result = js(c["lint_result"])
    if not lint_result or not lint_result.get("ok"):
        fails.append("lint 미통과" if lint_result else "lint 미실행")
    if not ctx["offers_main"]:
        fails.append("상품 슬롯 0")
    urls = {x["path"] for x in ctx["crumbs"][:-1]} | {x["url_path"] for group in ctx["links"].values() for x in group} | {k["url_path"] for k in ctx["kinds"]}
    if len(urls) < 5:
        fails.append(f"내부 링크 {len(urls)}개")
    return fails
