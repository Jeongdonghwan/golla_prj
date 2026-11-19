"""페이지 조립(F4): 사실 카드 → 페이지 내용. 렌더할 때마다 같은 결과(combo.id 시드). 런타임 AI 호출 없음.

허브(kind_hub)    카드 전부(소개·기준 전부·방식 전부·관리·실수·확인·질문 8개)
키워드(kind_mod)  첫 문장 6벌 중 1 · 수식어 문단(수식어 1개면 3문장, 여럿이면 각 2문장) · 기준 4행 · 방식 2개 · 관리·실수 3개 · 질문 3개
질문 글(question) 카드 posts 의 그 글
상품군 허브(group_hub) data/group_hubs.json
상품 카드 자리는 sections 의 {"type": "offers"} — 상품이 바뀌어도 글은 그대로다.
"""
import json
import random
import re
from pathlib import Path

from db import js, q

ROOT = Path(__file__).parent
GROUP_HUBS = json.loads((ROOT / "data" / "group_hubs.json").read_text("utf-8")) if (ROOT / "data" / "group_hubs.json").exists() else {}
AXIS_OF_DIM = {1: "space", 2: "persona", 3: "situation", 4: "feature", 5: "price", 6: "intent", 8: "brand"}
H2 = {  # 소제목 변형(같은 틀이 전 사이트에 깔리지 않게)
    "mods": ["{kw}에서 달라지는 점", "이 조건에서 먼저 볼 것", "조건별로 갈리는 지점", "{kw}, 무엇이 다른가"],
    "criteria": ["고르는 기준", "비교할 항목", "기준별로 보는 법", "따져 볼 항목"],
    "methods": ["방식별 장단점", "형태에 따른 차이", "어떤 방식이 맞나", "방식 비교"],
    "care": ["쓰면서 챙길 것", "관리와 흔한 실수", "사고 나서 할 일", "오래 쓰려면"],
    "faq": ["자주 묻는 질문", "많이 묻는 것", "궁금한 점"],
    "offers": ["조건에 맞는 상품", "지금 볼 수 있는 상품", "이 조건의 상품"],
}


def _rng(combo, part):
    """페이지 시드. v = 조립 변형 번호(assemble 이 형제와 가장 덜 겹치는 변형을 골라 page_contents.blocks 에 {"v": k} 로 저장)."""
    return random.Random(f"{combo['id']}|{combo.get('_v', 0)}|{part}")


def _pick(seq, n, rng):
    seq = list(seq or [])
    if len(seq) <= n:
        return seq
    keep = set(rng.sample(range(len(seq)), n))
    return [x for i, x in enumerate(seq) if i in keep]


def _h2(key, combo, kw):
    return _rng(combo, "h2" + key).choice(H2[key]).format(kw=kw)


def mods_of(combo):
    """[(axis, term)]"""
    ids = [combo[k] for k in ("mod1_id", "mod2_id", "mod3_id") if combo.get(k)]
    rows = {r["id"]: r for r in q("SELECT id, dimension_id, term FROM keyword_terms WHERE id IN (" + ",".join(["%s"] * len(ids)) + ")", ids)} if ids else {}
    return [(AXIS_OF_DIM[rows[i]["dimension_id"]], rows[i]["term"]) for i in ids if i in rows]


def _fit(s, n=155):
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= n:
        return s
    cut = s[:n]
    return cut[: max(cut.rfind(". "), cut.rfind("다."), 60) + 1].strip() or cut


def _faqs(items):
    return [{"question": f["q"], "answer": f["a"]} for f in items]


def compose(combo, vertical, site, v=0):
    """→ {h1, title, meta, answer[], points[], sections[], faqs[]}"""
    combo = {**combo, "_v": v or 0}
    t = combo["page_type"]
    card = js(vertical["card"], {})
    kw = combo["primary_keyword"]
    if t == "group_hub":
        out = _group(combo, site)
    elif not card:
        return None
    elif t == "kind_hub":
        out = _hub(combo, card, kw)
    elif t == "question":
        out = _post(combo, card)
    else:
        out = _mod(combo, card, kw)
    if out:
        out["title"] = f"{out['h1']}{site['title_sep']}{site['name']}"
    return out


def _criteria(rows):
    return {"type": "table", "head": ["기준", "확인할 것", "왜 달라지나"], "rows": [[r["item"], r["check"], r["why"]] for r in rows]}


def _hub(combo, card, kw):
    intro = card["hub_intro"]
    secs = [{"id": "intro", "h2": f"{kw}, 먼저 알아 둘 것", "type": "p", "items": intro[1:]},
            {"id": "criteria", "h2": f"{kw} 고르는 기준", **_criteria(card["criteria"])},
            {"id": "offers", "h2": _h2("offers", combo, kw), "type": "offers"},
            {"id": "methods", "h2": _h2("methods", combo, kw), "type": "methods", "items": card["methods"]},
            {"id": "order", "h2": "고르는 순서", "type": "p", "items": [card["intents"].get("고르는 법", "")]},
            {"id": "care", "h2": "관리와 보관", "type": "list", "items": card["care"]},
            {"id": "mistakes", "h2": "자주 하는 실수", "type": "list", "items": card["mistakes"]},
            {"id": "checks", "h2": "안전·설치 확인", "type": "list", "items": card["checks"]}]
    return {"h1": kw + " 고르는 기준과 방식 차이", "meta": _fit(card["summary"] + " " + intro[0]),
            "answer": [card["summary"], intro[0]], "points": card["points"], "sections": secs,
            "faqs": _faqs(_pick(card["faqs"], 8, _rng(combo, "faq")))}


def _mod(combo, card, kw):
    ms = mods_of(combo)
    if not ms:
        return None
    r = lambda part: _rng(combo, part)  # noqa: E731
    first = r("first").choice(card["first_sentences"]).format(kw=kw)
    paras, intent_p = [], None
    last = max(i for i, (ax, _t) in enumerate(ms) if ax != "intent") if any(ax != "intent" for ax, _t in ms) else -1
    for i, (ax, term) in enumerate(ms):
        if ax == "intent":
            intent_p = card["intents"].get(term)
            continue
        sents = card["mods"].get(ax, {}).get(term)
        if not sents:
            return None  # 카드에서 빠진 수식어 — 페이지를 만들지 않는다(keywords.rebuild 가 다시 정리)
        # 형제와 갈리는 마지막 수식어는 3문장 전부, 부모와 같은 앞 수식어는 2문장(형제끼리 겹치는 문장을 줄인다)
        chosen = sents if i == last else _pick(sents, 2, r("m" + term))
        paras.append({"term": term, "text": " ".join(chosen)})
    secs = []
    if paras:
        secs.append({"id": "mods", "h2": _h2("mods", combo, kw), "type": "terms", "items": paras})
    if intent_p:
        secs.append({"id": "intent", "h2": kw, "type": "p", "items": [intent_p]})
    compare = any(ax == "intent" and t in ("비교", "장단점") for ax, t in ms)
    methods = card["methods"] if compare else (_pick(card["methods"], 1, r("meth")) if len(card["methods"]) >= 3 else [])
    secs += [{"id": "criteria", "h2": _h2("criteria", combo, kw), **_criteria(_pick(card["criteria"], 4, r("crit")))},
             {"id": "offers", "h2": _h2("offers", combo, kw), "type": "offers"}]
    if methods:
        secs.append({"id": "methods", "h2": _h2("methods", combo, kw), "type": "methods", "items": methods})
    secs.append({"id": "care", "h2": _h2("care", combo, kw), "type": "list",
                 "items": _pick(card["care"], 1, r("care")) + _pick(card["mistakes"], 1, r("mis")) + _pick(card["checks"], 1, r("chk"))})
    lead = paras[0]["text"].split(". ")[0] if paras else (intent_p or "")
    return {"h1": r("title").choice(card["title_templates"]).format(kw=kw), "meta": _fit(first + " " + lead),
            "answer": [first], "points": _pick(card["points"], 3, r("pt")), "sections": secs,
            "faqs": _faqs(_pick(card["faqs"], 5, r("faq")))}


def _post(combo, card):
    slug = combo["url_path"].rstrip("/").rsplit("/", 1)[-1]
    p = next((x for x in card.get("posts", []) if x["slug"] == slug), None)
    if not p:
        return None
    secs = [{"id": f"s{i}", "h2": s["h2"], "type": "p", "items": [s["body"]]} for i, s in enumerate(p["sections"])]
    secs.insert(min(2, len(secs)), {"id": "offers", "h2": _h2("offers", combo, p["title"]), "type": "offers"})
    return {"h1": p["title"], "meta": _fit(p["answer"]), "answer": [p["answer"]], "points": [], "sections": secs,
            "faqs": _faqs(p.get("faqs", []))}


def _group(combo, site):
    g = GROUP_HUBS.get(site["host"].split(".")[0])
    if not g:
        return None
    secs = [{"id": "intro", "h2": g["h2"], "type": "p", "items": g["paras"]},
            {"id": "kinds", "h2": "종류별 안내", "type": "kinds"},
            {"id": "offers", "h2": "상품군 상품", "type": "offers"}]
    return {"h1": g["h1"], "meta": _fit(g["meta"]), "answer": [g["answer"]], "points": g.get("points", []), "sections": secs, "faqs": []}


def plain(page):
    """고유성·린트용 본문 텍스트(상품 카드 제외)."""
    parts = [page["h1"], *page["answer"], *page["points"]]
    for s in page["sections"]:
        for it in s.get("items") or []:
            parts.append(it["text"] if isinstance(it, dict) and "text" in it else " ".join(it.values()) if isinstance(it, dict) else it)
        for row in s.get("rows") or []:
            parts.append(" ".join(row))
    parts += [f["question"] + " " + f["answer"] for f in page["faqs"]]
    return re.sub(r"\s+", " ", " ".join(parts)).strip()
