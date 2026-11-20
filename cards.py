"""사실 카드(F4): 종류마다 세션(Claude Code)이 쓰는 JSON. 입력 만들기 → 세션 작성 → 점검 → verticals.card 에 적재.

  content/cards/_input/<slug>.json   종류 정보(상품명·특징 후보·가격대 후보·브랜드 후보·공통 수식어) — jobs cards-input
  content/cards/<slug>.json          세션이 쓴 카드(작성 규칙 prompts/card.md)                      — jobs cards-load
카드에 문단이 있는 수식어만 키워드 페이지가 된다(keywords.py). 질문 글(posts)은 카드 안에 함께 쓴다.
"""
import json
import re
from collections import Counter
from pathlib import Path

import products
from db import ex, q
from engine import lint

ROOT = Path(__file__).parent
CARD_DIR = ROOT / "content" / "cards"
MODS = json.loads((ROOT / "data" / "modifiers.json").read_text("utf-8"))
AXES = ("space", "persona", "situation", "feature", "price", "brand")
NEED = {"summary": str, "hub_intro": list, "points": list, "first_sentences": list, "title_templates": list, "criteria": list,
        "methods": list, "care": list, "mistakes": list, "checks": list, "faqs": list, "mods": dict, "intents": dict, "posts": list}
MIN_LEN = {"hub_intro": 3, "points": 4, "first_sentences": 6, "title_templates": 10, "criteria": 7, "methods": 2, "care": 4,
           "mistakes": 3, "checks": 3, "faqs": 10, "posts": 2}


def build_input(v):
    offers = products.kind_offers(v)
    tags = Counter(t for o in offers for t in json.loads(o["tags"] or "[]"))
    own = set(json.loads(v["match_tags"] or "[]")) | {t for _p, k, ts in products.SUBKINDS if _p == v["name"] for t in ts}
    skip = set(MODS["feature_skip"]) | set(products.PRICE_TAGS) | own
    features = [t for t, n in tags.most_common() if n >= 3 and t not in skip and not t.startswith("@")][:10]
    prices = [t for t in products.PRICE_TAGS if 3 <= tags[t] < len(offers)][:3]
    brands = sorted({o["brand"] for o in offers if o["brand"]}, key=lambda b: (-sum(o["brand"] == b for o in offers), b))
    brands = [b for b in brands if sum(o["brand"] == b for o in offers) >= 3][:5]
    parent = v["parent_id"] and q("SELECT name FROM verticals WHERE id=%s", (v["parent_id"],))[0]["name"]
    site = q("SELECT group_name FROM sites WHERE id=%s", (v["site_id"],))[0]["group_name"]
    return {"kind": v["name"], "slug": v["slug"], "group": site, "parent_kind": parent or None,
            "sub_kinds": [r["name"] for r in q("SELECT name FROM verticals WHERE parent_id=%s AND is_active=1", (v["id"],))],
            "product_names": [o["name"][:90] for o in sorted(offers, key=lambda o: -float(o["score"]))[:50]],
            "feature_candidates": features, "price_candidates": prices, "brand_candidates": brands,
            "space_options": [m["term"] for m in MODS["space"]], "persona_options": [m["term"] for m in MODS["persona"]],
            "situation_options": [m["term"] for m in MODS["situation"]], "intents": [m["term"] for m in MODS["intent"]]}


def _texts(card):
    """카드 안의 모든 글(린트 대상)."""
    out = []

    def walk(x):
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, list):
            for y in x:
                walk(y)
        elif isinstance(x, dict):
            for k, y in x.items():
                if k not in ("slug",):
                    walk(y)
    walk(card)
    return out


def validate(card, inp):
    """카드 점검. 문제 목록(빈 목록 = 통과)."""
    errs = []
    for k, t in NEED.items():
        if not isinstance(card.get(k), t):
            errs.append(f"{k} 없음/형식")
    if errs:
        return errs
    for k, n in MIN_LEN.items():
        if len(card[k]) < n:
            errs.append(f"{k} {len(card[k])}개 < {n}")
    if any("{kw}" not in s for s in card["first_sentences"] + card["title_templates"]):
        errs.append("first_sentences·title_templates 에 {kw} 없음")
    allowed = {"space": inp["space_options"], "persona": inp["persona_options"], "situation": inp["situation_options"],
               "feature": inp["feature_candidates"], "price": inp["price_candidates"], "brand": inp["brand_candidates"]}
    for ax, d in card["mods"].items():
        if ax not in allowed:
            errs.append(f"mods.{ax}: 모르는 축")
            continue
        for term, sents in d.items():
            if term not in allowed[ax] and ax in ("space", "persona", "situation"):  # 특징·가격대·브랜드 후보는 CSV 에 따라 바뀐다 — 써 둔 문단은 유지
                errs.append(f"mods.{ax}.{term}: 후보에 없음")
            if not (isinstance(sents, list) and len(sents) >= 3):
                errs.append(f"mods.{ax}.{term}: 문장 3개 필요")
    if set(card["intents"]) != set(inp["intents"]):
        errs.append("intents 키가 다름")
    for p in card["posts"]:
        if not all(p.get(k) for k in ("slug", "title", "question", "answer", "sections")) or not re.fullmatch(r"[0-9a-z가-힣-]+", p["slug"]):
            errs.append(f"posts 형식: {p.get('slug')}")
    lr = lint(" ".join(_texts(card)))
    if not lr["ok"]:
        errs.append(f"린트: {lr}")
    return errs


def load(v, card):
    ex("UPDATE verticals SET card=%s WHERE id=%s", (json.dumps(card, ensure_ascii=False), v["id"]))
