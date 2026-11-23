"""페이지 조립(F4): 사실 카드 → 페이지 내용. 렌더할 때마다 같은 결과(combo.id 시드). 런타임 AI 호출 없음.

허브(kind_hub)    카드 전부(소개·기준 전부·방식 전부·관리·실수·확인·질문 8개)
키워드(kind_mod)  첫 문장 6벌 중 1 · 수식어 문단(수식어 1개면 3문장, 여럿이면 각 2문장) · 기준 4행 · 방식 2개 · 관리 2·실수 1·확인 1 · 질문 5개
                  → 레이 구조(_ray): h1 수식어 · 핵심 요약 상자(결론+dl 4) · 번호 절 6개(한 문단씩) · "{kw} 자주 묻는 질문" (search·versus 도 같음)
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
H2 = {  # 소제목 변형(같은 틀이 전 사이트에 깔리지 않게). 블로그 말투(2026-11-19)
    "mods": ["{kw}, 여기서 달라져요", "이 조건이면 먼저 보세요", "조건마다 갈리는 지점", "{kw}는 무엇이 다를까"],
    "criteria": ["고를 때 이것만 보세요", "비교할 항목 정리", "기준별로 보는 법", "사기 전에 따져 볼 것"],
    "methods": ["방식별 장단점 한눈에", "형태마다 이렇게 달라요", "나한테 맞는 방식은", "방식 비교"],
    "care": ["쓰면서 챙길 것", "많이들 하는 실수", "사고 나서 할 일", "오래 쓰려면"],
    "faq": ["자주 묻는 질문", "많이 묻는 것", "궁금한 점"],
    "offers": ["{kw} 추천 상품", "조건에 맞춰 고른 {kw}", "{kw}, 이 상품들부터 보세요"],
}


# 판매형 제목(상품 추천 의도). 최상급·순위 없이. {kw} = 페이지 키워드
REC_TITLES = ["{kw} 추천, 조건에 맞는 것만 골랐어요", "가성비로 고른 {kw} 추천", "{kw} 추천 상품, 사기 전에 비교할 것까지",
              "{kw} 추천, 어디서 갈리는지 한눈에", "{kw} 뭐 살지 고민이라면, 추천 상품부터", "{kw} 추천 모음, 고르는 기준까지 정리",
              "지금 사기 좋은 {kw} 추천", "{kw} 추천, 특징별로 바로 비교해 보세요"]


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
    if key == "offers" and kw.endswith(" 추천"):  # '… 추천 추천 상품' 방지
        kw = kw[:-3]
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


# ── 레이 구조(2026-11-24): 키워드 페이지(kind_mod·search·versus) = h1 수식어 + 핵심 요약 상자 + 번호 절 6개(한 문단씩) + FAQ ──
# 카드 문장을 다시 쓰지 않고, 같은 카드 조각을 문단으로 묶는다. h2·수식어는 2~4벌 중 시드 고정.
RAY_H2 = [
    ["{kw}: 이 조건에서 달라지는 점", "{kw}, 조건별로 갈리는 지점", "{kw}: 먼저 알아 둘 차이"],
    ["{kw}: {a}·{b}부터 확인", "{kw} — {a}·{b} 비교하는 법", "{kw}: {a}·{bj} 이렇게 보세요"],
    ["{a}·{b}도 함께 보세요", "{a}·{b} 확인하는 법", "남은 기준, {a}·{b}"],
    ["{kw}: 방식별 장단점", "{kw} — {a} 쪽이 맞는 경우", "{kw}: 형태에 따라 달라지는 점"],
    ["쓰면서 챙길 것", "오래 쓰려면 이렇게", "사고 나서 할 관리"],
    ["{kw}: 사기 전에 확인할 것", "{kw} — 많이들 하는 실수와 안전", "{kw}: 실수 줄이는 법"],
]


_JOSA = {"은": ("은", "는"), "는": ("은", "는"), "이": ("이", "가"), "가": ("이", "가"), "을": ("을", "를"), "를": ("을", "를"), "과": ("과", "와"), "와": ("과", "와")}


def _kwjosa(text, kw):
    """카드 틀 문장의 '{kw}를' 이 키워드 받침과 어긋나면 고친다(예: '정수기 필터 추천를' → '추천을'). 화면 문장만 — title·설명은 저장값 그대로."""
    return re.sub(re.escape(kw) + r"(은|는|이|가|을|를|과|와)(?=[\s,.])", lambda m: _jo(kw, *_JOSA[m.group(1)]), text)


def _sent(t):
    t = (t or "").strip()
    return t if not t or t.endswith((".", "?", "!")) else t + "."


def _crit_p(rows):
    return " ".join(f"{_jo(r['item'], '은', '는')} {_sent(r['check'])} {_sent(r['why'])}" for r in rows)


def _meth_p(ms):
    return " ".join(f"{_jo(m['name'], '은', '는')} {_sent(m['pros'])} 다만 {_sent(m['cons'])} {_fit_end(m['fit'])}" for m in ms)


def _fresh(words, kw):
    """수식어 낱말이 키워드에 이미 있으면 False(h1 에서 같은 말 반복 금지)."""
    toks = [t for t in re.split(r"[\s·,]+", kw) if len(t) >= 2]
    return not any(t in w or w in t for w in words for t in toks if len(w) >= 2)


def h1sub(combo, kw, crit, methods):
    """h1 아래 줄 수식어: 카드의 기준·방식 이름에서 3~4벌, 키워드와 겹치는 벌은 뺀다."""
    items = [r["item"] for r in crit]
    names = [m["name"] for m in methods]
    cands = []
    if len(names) >= 2:
        cands.append(([names[0], names[1]], f"{names[0]}·{names[1]} 중 고르기"))
    if len(items) >= 2:
        cands.append(([items[0], items[1]], f"{items[0]}·{items[1]}부터 비교"))
    if len(items) >= 3:
        cands.append(([items[2]], f"{items[2]} 확인부터 관리까지"))
    if len(items) >= 4:
        cands.append(([items[1], items[3]], f"{items[1]}·{_jo(items[3], '으로', '로')} 좁히기"))
    ok = [t for w, t in cands if _fresh(w, kw)] or [t for _w, t in cands]
    return _rng(combo, "h1sub").choice(ok) if ok else ""


def _ray(combo, kw, h1, meta, lead, summary, dl, first, crit, methods, care, faqs, h1_crit=None):
    """first = (h2 변형 목록 or None, 문단). crit 4행 → 절 2·3, methods → 절 4, care(관리 2·실수 1·안전 1) → 절 5·6."""
    r = lambda part: _rng(combo, part)  # noqa: E731
    secs = [{"id": "offers", "h2": _h2("offers", combo, kw), "type": "offers"}]
    body = []
    if first[1]:
        body.append((first[0] or RAY_H2[0], {}, first[1]))
    a, b = crit[:2], crit[2:4]
    if a:
        x, y = a[0]["item"], a[-1]["item"]
        body.append((RAY_H2[1], {"a": x, "b": y, "bj": _jo(y, "은", "는")}, _crit_p(a)))
    if b:
        body.append((RAY_H2[2], {"a": b[0]["item"], "b": b[-1]["item"]}, _crit_p(b)))
    if methods:
        body.append((RAY_H2[3], {"a": methods[0]["name"]}, _meth_p(methods)))
    if care[:2]:
        body.append((RAY_H2[4], {}, " ".join(_sent(t) for t in care[:2])))
    if care[2:]:
        body.append((RAY_H2[5], {}, " ".join(_sent(t) for t in care[2:])))
    for i, (opts, f, text) in enumerate(body):
        h = r(f"rh{i}").choice(opts).format(kw=kw, **f)
        if h.startswith(kw) and h[len(kw):len(kw) + 1] == ",":  # "{kw}, …" 꼴은 쉼표 대신 쌍점으로 통일하지 않는다(벌 그대로)
            pass
        secs.append({"id": f"s{i + 1}", "h2": h, "type": "p", "num": i + 1, "items": [text]})
    return {"h1": h1, "h1kw": kw, "h1sub": h1sub(combo, kw, h1_crit or crit, methods or []), "meta": meta,
            "answer": [_kwjosa(lead, kw)], "points": [], "ray": True,
            "summary": {"h2": f"{kw} 핵심 요약", "text": " ".join(_sent(t) for t in summary), "dl": [d for d in dl if d[1]]},
            "faq_h2": f"{kw} 자주 묻는 질문", "sections": secs, "faqs": faqs}


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
    elif t == "versus":
        out = _vs(combo, card, kw, vertical["name"])
    elif t == "search":
        out = _search(combo, card, kw)
    else:
        out = _mod(combo, card, kw)
    if out:
        out["title"] = f"{out['h1']}{site['title_sep']}{site['name']}"
    return out


def _criteria(rows):
    return {"type": "table", "head": ["기준", "확인할 것", "왜 달라지나"], "rows": [[r["item"], r["check"], r["why"]] for r in rows]}


def _hub(combo, card, kw):
    intro = card["hub_intro"]
    secs = [{"id": "offers", "h2": _h2("offers", combo, kw), "type": "offers"},
            {"id": "intro", "h2": f"{kw}, 이것부터 알고 고르세요", "type": "p", "items": intro[1:]},
            {"id": "criteria", "h2": f"{kw} 고를 때 볼 기준", **_criteria(card["criteria"])},
            {"id": "methods", "h2": _h2("methods", combo, kw), "type": "methods", "items": card["methods"]},
            {"id": "order", "h2": "이 순서로 고르면 쉬워요", "type": "p", "items": [card["intents"].get("고르는 법", "")]},
            {"id": "care", "h2": "관리·보관은 이렇게", "type": "list", "items": card["care"]},
            {"id": "mistakes", "h2": "많이들 하는 실수", "type": "list", "items": card["mistakes"]},
            {"id": "checks", "h2": "안전·설치, 이건 꼭 확인", "type": "list", "items": card["checks"]}]
    return {"h1": _rng(combo, "title").choice(card["title_templates"]).format(kw=kw), "meta": _fit(card["summary"] + " " + intro[0]),
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
    compare = any(ax == "intent" and t in ("비교", "장단점") for ax, t in ms)
    methods = card["methods"] if compare else (_pick(card["methods"], 1, r("meth")) if len(card["methods"]) >= 3 else [])
    crit = _pick(card["criteria"], 4, r("crit"))
    care = _pick(card["care"], 2, r("care")) + _pick(card["mistakes"], 1, r("mis")) + _pick(card["checks"], 1, r("chk"))
    lead = paras[0]["text"].split(". ")[0] if paras else (intent_p or "")
    titles = card["title_templates"] if any(ax == "intent" for ax, _t in ms) else REC_TITLES
    first_p = " ".join([p["text"] for p in paras] + ([intent_p] if intent_p else []))
    pts = ([lead] if lead and lead != intent_p else []) + _pick(card["points"], 3, r("pt"))[:2]
    terms = "·".join(t for _ax, t in ms)
    return _ray(combo, kw, r("title").choice(titles).format(kw=kw), _fit(first + " " + lead), first, pts,
                [("종류", card.get("kind", "")), ("조건" if paras else "찾는 목적", terms), ("비교 기준", "·".join(x["item"] for x in crit)),
                 ("방식", "·".join(m["name"] for m in (methods or card["methods"])))],
                (None, first_p), crit, methods, care, _faqs(_pick(card["faqs"], 4, r("faq"))))


def _jo(w, with_b, without_b):
    """받침 따라 조사: _jo('컨벡터', '은', '는') → '컨벡터는'. 한글이 아니면 받침 없는 쪽."""
    c = w[-1] if w else ""
    return w + (with_b if "가" <= c <= "힣" and (ord(c) - 0xAC00) % 28 else without_b)


def _fit_end(t):
    """'…붙여 쓸 곳' 같은 명사 끝 → '…붙여 쓸 곳에 맞아요.'"""
    t = t.strip().rstrip(".")
    return t + "." if t.endswith(("요", "다")) else t + "에 맞아요."


def _vs(combo, card, kw, kind):
    """방식 차이 페이지(레이 구조): 두 방식 장단점 문단 + 추천 상품 + 기준 4 + 나머지 방식 + 관리 + 질문 5."""
    from keywords import method_label, vs_pairs
    hit = next(((i, j) for i, j, k, _s in vs_pairs(card, kind) if k == kw), None)
    if not hit:
        return None
    r = lambda part: _rng(combo, part)  # noqa: E731
    ms = card["methods"]
    a, b = ms[hit[0]], ms[hit[1]]
    la, lb = method_label(a["name"], kind), method_label(b["name"], kind)
    h1 = r("title").choice([f"{la} vs {lb}, {kind} 뭐가 다를까요", f"{kw}, 이것만 알면 고르기 쉬워요",
                            f"{kind} {la}·{lb} 중 뭘 살까? 차이 정리", f"{kind} {la} {lb} 차이, 나한테 맞는 쪽은",
                            f"{_jo(la, '이랑', '랑')} {lb} 헷갈린다면? {kind} 차이 한눈에"])
    rest = [m for k, m in enumerate(ms) if k not in hit]
    crit = _pick(card["criteria"], 4, r("crit"))
    care = _pick(card["care"], 1, r("care")) + _pick(card["mistakes"], 1, r("mis")) + _pick(card["checks"], 1, r("chk"))
    ans = [f"{_jo(la, '은', '는')} {_fit_end(a['fit'])}", f"{_jo(lb, '은', '는')} {_fit_end(b['fit'])}"]
    first_p = _meth_p([a, b])
    out = _ray(combo, kw, h1, _fit(" ".join(ans) + f" {kw}, 장단점과 추천 상품까지 정리했어요."), ans[0],
               [ans[1]] + _pick(card["points"], 2, r("pt")),
               [("종류", kind), ("비교", f"{la}·{lb}"), ("비교 기준", "·".join(x["item"] for x in crit)),
                ("다른 방식", "·".join(m["name"] for m in rest))],
               ([f"{kw}: {_jo(la, '과', '와')} {lb}의 장단점", f"{kw} — 두 방식 나란히 보기", f"{kw}: 장단점과 맞는 경우"], first_p),
               crit, rest, care, _faqs(_pick(card["faqs"], 4, r("faq"))), h1_crit=crit)
    out["h1sub"] = h1sub(combo, kw, crit, [])  # 방식 이름은 키워드에 이미 있다
    return out


def _search(combo, card, kw):
    """실제 검색 키워드 페이지(레이 구조): 추천 상품(상품명에 그 말이 든 것 먼저) + 그 말 설명 3문장 + 기준 4 + 방식 1 + 관리 + 질문 5."""
    it = next((x for x in card.get("search", []) if x["kw"] == kw), None)
    if not it:
        return None
    r = lambda part: _rng(combo, part)  # noqa: E731
    first = r("first").choice(card["first_sentences"]).format(kw=kw)
    crit = _pick(card["criteria"], 4, r("crit"))
    methods = _pick(card["methods"], 1, r("meth")) if len(card["methods"]) >= 3 else []
    care = _pick(card["care"], 2, r("care")) + _pick(card["mistakes"], 1, r("mis")) + _pick(card["checks"], 1, r("chk"))
    return _ray(combo, kw, r("title").choice(REC_TITLES).format(kw=kw), _fit(first + " " + it["sents"][0]), first,
                [it["sents"][0]] + _pick(card["points"], 3, r("pt"))[:2],
                [("종류", card.get("kind", "")), ("검색어 뜻", it["term"]), ("비교 기준", "·".join(x["item"] for x in crit)),
                 ("방식", "·".join(m["name"] for m in (methods or card["methods"])))],
                (None, " ".join(it["sents"])), crit, methods, care, _faqs(_pick(card["faqs"], 4, r("faq"))))


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
            {"id": "posts", "h2": "지금 많이 찾는 추천", "type": "posts"},
            {"id": "kinds", "h2": "종류별 가이드", "type": "kinds"},
            {"id": "offers", "h2": "상품군 상품", "type": "offers"}]
    return {"h1": g["h1"], "meta": _fit(g["meta"]), "answer": [g["answer"]], "points": g.get("points", []), "sections": secs, "faqs": []}


def plain(page):
    """고유성·린트용 본문 텍스트(상품 카드 제외)."""
    parts = [page["h1"], page.get("h1sub", ""), *page["answer"], *page["points"]]
    if page.get("summary"):
        parts += [page["summary"]["text"]] + [f"{k} {v}" for k, v in page["summary"]["dl"]]
    for s in page["sections"]:
        for it in s.get("items") or []:
            parts.append(it["text"] if isinstance(it, dict) and "text" in it else " ".join(it.values()) if isinstance(it, dict) else it)
        for row in s.get("rows") or []:
            parts.append(" ".join(row))
    parts += [f["question"] + " " + f["answer"] for f in page["faqs"]]
    return re.sub(r"\s+", " ", " ".join(parts)).strip()
