"""레이 구조·h1 수식어 점검(2026-11-24, 읽기 전용 — DB 에 쓰지 않는다): python scripts/ray_check.py [--per 30] [--html 폴더]

공개된 키워드 페이지(kind_mod·search·versus)를 조합기로 다시 조립해서 잰다.
 ① h1(키워드+수식어) 중복 — 사이트별 전 페이지
 ② 같은 종류(주제) 페이지끼리 같은 문장 비율 — 종류마다 표본 per 쪽, 쌍마다 A 의 문장 글자 중 B 에도 그대로 있는 비율(dup_measure 와 같은 방식,
    단위는 화면 요소: h1·h2·dl 칸·질문은 통째로, 문단·답은 문장으로). 전체 쌍 평균 · 종류별 평균의 최댓값 · 쌍 최댓값.
 ③ 조사 오류 — 기준·방식 이름 바로 뒤 은/는·이/가·을/를·과/와 가 받침과 맞는지, '을를' 같은 자리표시
 ⑤ description 45~80자·중복, 키워드 든 h2 수(요약·절·FAQ, 4개 이상)
 ④ 번호 절 문단 길이 · 키워드가 통째로 든 h2 수 · 본문 글자(공백 제외) · title 이 저장값과 같은지
--html 폴더: 사이트마다 2쪽을 템플릿으로 렌더해 저장(engine.assemble + render_template — render_combo 를 거치지 않아 DB 쓰기 없음).
"""
import argparse
import collections
import itertools
import os
import random
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import composer  # noqa: E402
from app import create_app  # noqa: E402
from db import js, q  # noqa: E402

SENT = re.compile(r"(?<=[.?!])\s+")
PLACEHOLDER = re.compile(r"을를|를을|은는|는은|과와|와과|\(으\)로|\(이\)|\(가\)|\{[a-z]+\}")


def units(pg):
    """화면 요소 단위: 제목류는 통째로, 글은 문장으로."""
    out, texts = [pg["h1"], pg.get("h1sub", "")], list(pg["answer"]) + list(pg["points"])
    if pg.get("summary"):
        out.append(pg["summary"]["h2"])
        texts.append(pg["summary"]["text"])
        out += [f"{k} {v}" for k, v in pg["summary"]["dl"]]
    for s in pg["sections"]:
        out.append(s.get("h2", ""))
        for it in s.get("items") or []:
            texts.append(it["text"] if isinstance(it, dict) and "text" in it else " ".join(it.values()) if isinstance(it, dict) else it)
        out += [" ".join(r) for r in s.get("rows") or []]
    for f in pg["faqs"]:
        out.append(f["question"])
        texts.append(f["answer"])
    out += [x for t in texts for x in SENT.split(t or "")]
    return [x.strip() for x in out if len(x.strip()) > 12]


def batchim(ch):
    return (ord(ch) - 0xAC00) % 28 != 0


def josa_errors(text, words):
    errs = []
    for w in set(words):
        if not w or not ("가" <= w[-1] <= "힣"):
            continue
        for m in re.finditer(re.escape(w) + r"(은|는|이|가|을|를|과|와)(?=[\s,.])", text):
            if (m.group(1) in "은이을과") != batchim(w[-1]):
                errs.append(w + m.group(1))
    return errs + PLACEHOLDER.findall(text)


def pair_ratio(docs):
    v = []
    for a, b in itertools.permutations(docs, 2):
        sb, tot = set(b), sum(map(len, a)) or 1
        v.append(sum(len(x) for x in a if x in sb) / tot)
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=30)
    ap.add_argument("--html")
    a = ap.parse_args()
    app = create_app()
    with app.app_context():
        sites = {s["id"]: s for s in q("SELECT * FROM sites")}
        verts = {v["id"]: v for v in q("SELECT * FROM verticals")}
        rows = q("SELECT * FROM keyword_combos WHERE status='published' AND page_type IN ('kind_mod','search','versus') ORDER BY id")
        conts = {r["combo_id"]: r for r in q("""SELECT pc.combo_id, pc.blocks, pc.model, pc.title FROM page_contents pc JOIN keyword_combos k
                                                ON k.id=pc.combo_id AND pc.version=k.content_version WHERE k.status='published'""")}
        h1s, per_site, by_kind = collections.Counter(), collections.Counter(), collections.defaultdict(list)
        descs, dlen, h2kw, h2same = collections.Counter(), [], [], collections.Counter()
        title_diff, errs, plens, kwh2, chars = [], [], [], [], []
        for c in rows:
            ct = conts.get(c["id"])
            v = (js(ct["blocks"], {}) or {}).get("v", 0) if ct and ct["model"] == "assembled" else 0
            pg = composer.compose(c, verts[c["vertical_id"]], sites[c["site_id"]], v)
            if not pg:
                continue
            host = sites[c["site_id"]]["host"]
            per_site[host] += 1
            h1s[(host, pg["h1kw"] + " " + pg["h1sub"])] += 1
            descs[(host, pg["meta"])] += 1
            dlen.append(len(pg["meta"]))
            hs = [pg["summary"]["h2"]] + [s["h2"] for s in pg["sections"]] + [pg["faq_h2"]]
            h2kw.append(sum(1 for h in hs if pg["h1kw"] in h))
            h2same["|".join(hs)] += 1
            if ct and ct["title"] and ct["title"] != pg["title"]:
                title_diff.append(c["id"])
            card = js(verts[c["vertical_id"]]["card"], {})
            words = [x["item"] for x in card.get("criteria", [])] + [m["name"] for m in card.get("methods", [])]
            ps = [s["items"][0] for s in pg["sections"] if s["type"] == "p"]
            errs += josa_errors(" ".join([pg["h1sub"], pg["answer"][0], pg["summary"]["text"]] + [s["h2"] for s in pg["sections"]] + ps), words + [pg["h1kw"]])
            plens += [len(p) for p in ps]
            kwh2.append(sum(1 for s in pg["sections"] if s["type"] == "p" and s["h2"].startswith(pg["h1kw"])))
            chars.append(len("".join(units(pg)).replace(" ", "")))
            by_kind[(host, verts[c["vertical_id"]]["name"])].append(pg)
        rnd, allv, kinds = random.Random(3), [], []
        for key, pgs in by_kind.items():
            smp = rnd.sample(pgs, min(a.per, len(pgs)))
            if len(smp) < 2:
                continue
            v = pair_ratio([units(p) for p in smp])
            allv += v
            kinds.append((statistics.mean(v), max(v), *key))
        dup = [k for k, n in h1s.items() if n > 1]
        kinds.sort(reverse=True)
        print("키워드 페이지", dict(per_site), "합계", sum(per_site.values()))
        print("h1 중복", len(dup), dup[:5])
        print("description 길이 %d~%d · 45~80 밖 %d쪽 · 중복 %d" % (min(dlen), max(dlen), sum(1 for x in dlen if not 45 <= x <= 80), sum(1 for n in descs.values() if n > 1)))
        print("키워드 든 h2(요약·절·FAQ) 최소 %d 평균 %.1f · h2 묶음이 통째로 같은 페이지 %d" % (min(h2kw), statistics.mean(h2kw), sum(n for n in h2same.values() if n > 1)))
        print("title 이 저장값과 다른 쪽", len(title_diff), title_diff[:8])
        print("조사 오류", len(errs), collections.Counter(errs).most_common(5))
        print("같은 종류끼리 같은 문장: 쌍 평균 %.1f%% · 종류별 평균 최대 %.1f%% (%s %s) · 쌍 최대 %.1f%%" % (
            100 * statistics.mean(allv), 100 * kinds[0][0], kinds[0][2], kinds[0][3], 100 * max(allv)))
        print("  종류별 평균 상위", [(h.split(".")[0], n, round(100 * m, 1)) for m, _x, h, n in kinds[:6]])
        sp = sorted(plens)
        print("번호 절 문단 글자 평균 %d · 중앙 %d · 10%%~90%% %d~%d" % (statistics.mean(sp), statistics.median(sp), sp[len(sp) // 10], sp[len(sp) * 9 // 10]))
        print("키워드 통째로 든 h2(번호 절) 평균 %.2f · 본문 글자 평균 %d" % (statistics.mean(kwh2), statistics.mean(chars)))
        if a.html:
            from flask import render_template
            import engine
            os.makedirs(a.html, exist_ok=True)
            for sid, s in sites.items():
                mine = [c for c in rows if c["site_id"] == sid]
                for c in mine[:1] + [c for c in mine if c["page_type"] != "kind_mod"][:1]:
                    with app.app_context(), app.test_request_context(c["url_path"], base_url=f"http://{s['host'].split('.')[0]}.localhost:5000"):
                        html = render_template(f"pages/{c['page_type']}.html", **engine.assemble(s, c))
                    fn = os.path.join(a.html, f"{s['host'].split('.')[0]}-{c['id']}.html")
                    open(fn, "w", encoding="utf-8").write(html)
                    print("렌더", fn)


if __name__ == "__main__":
    main()
