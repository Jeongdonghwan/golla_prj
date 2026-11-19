"""콘텐츠 파이프라인: 사실 카드 → 조립 페이지 채점(assemble_pages) → 자동 승인 → 게이트 → 공개(publish_ready). API 호출 없음. 요청 처리 중에는 호출하지 않는다."""

from db import js, ops, queries as dbq
from engine import UNIQUENESS_MAX, gate, lint

PROMPT_VERSION = "p1"


def shingles(text, n=5):
    words = text.split()
    return {" ".join(words[i:i + n]) for i in range(max(len(words) - n + 1, 1))}


def gate_audit(site):
    """공개 중인 콤보를 게이트로 재검사해 실패분을 review 로 내린다. {url: 사유}"""
    demoted = {}
    for combo in ops.combos_by_status(site["id"], ["published"]):
        fails = gate(site, combo)
        if fails:
            ops.set_combo(combo["id"], status="review")
            demoted[combo["url_path"]] = fails
    if demoted:
        dbq.bump_cache_version(site["id"])
    return demoted


def publish_ready(site, cap=None):
    """approved 콤보를 우선순위 순으로 게이트 검사 후 공개. 주간 상한(cap) 안에서만. [(url, 실패사유 or None)]

    내부 링크 수는 공개된 페이지끼리 세므로, 이번 묶음을 먼저 공개 상태로 두고 검사한 뒤 실패분을 approved 로 되돌린다.
    되돌린 페이지 때문에 다른 페이지의 링크가 줄 수 있어 더 떨어지는 것이 없을 때까지 반복한다.
    """
    from datetime import date
    from engine import abs_url
    st = dbq.settings(site["id"])
    plan = js(st.get("publish_plan"), [])
    scheduled = cap is None  # cap 을 직접 넘긴 호출(테스트·수동)은 질문 글 몫을 열지 않는다
    step = {}
    if cap is None and plan:  # 날짜별 하루 공개 수(첫날 통과분 전부, 이후 하루 N쪽)
        today = date.today().isoformat()
        step = next((p for p in plan if not p.get("until") or today <= p["until"]), plan[-1])
        room = max(int(step["per_day"]) - ops.published_today(site["id"]), 0)
    else:
        if cap is None:
            cap = int(st.get("weekly_publish_cap", 100))
        room = max(cap - ops.published_last_days(site["id"]), 0)
    batch = ops.combos_by_status(site["id"], ["approved"], limit=room, posts=False) if room else []
    per_post = int(step.get("posts", st.get("posts_per_day") or 0)) if plan and cap is None else int(st.get("posts_per_day") or 0)
    posts_room = max(per_post - ops.published_today(site["id"], posts=True), 0) if scheduled else 0
    if posts_room:  # 질문 글: 미리 써서 승인해 둔 글을 하루 posts_per_day 편씩(우선순위 → id 순)
        batch += ops.combos_by_status(site["id"], ["approved"], limit=posts_room, posts=True)
    for combo in batch:
        ops.publish_combo(combo["id"])
    failed, changed = {}, True
    while changed:
        changed = False
        for combo in batch:
            if combo["id"] not in failed:
                fails = gate(site, dbq.combo(combo["id"]))
                if fails:
                    failed[combo["id"]] = fails
                    ops.set_combo(combo["id"], status="approved", published_at=combo["published_at"])
                    changed = True
    for combo in batch:
        if combo["id"] not in failed:
            dbq.queue_indexnow(site["id"], abs_url(combo["url_path"]), "publish")
    if batch:
        dbq.bump_cache_version(site["id"])
    return [(c["url_path"], failed.get(c["id"])) for c in batch]


def approve(site, content_id, reviewer):
    """검수 승인(관리자): 유사도 때문에 review 에 남은 페이지를 사람이 승인. 실제 공개는 publish_ready(게이트)가 한다."""
    content = ops.content_by_id(content_id)
    combo = dbq.combo(content["combo_id"])
    ops.review_content(content_id, "approved", reviewer)
    ops.set_combo(combo["id"], content_version=content["version"],
                  status=combo["status"] if combo["status"] in ("published", "noindex") else "approved")
    dbq.bump_cache_version(site["id"])


def reject(content_id, reviewer):
    ops.review_content(content_id, "rejected", reviewer)


# ── 조립 페이지 채점·승인(F4) ─────────────────────────────────────────────
VARIANTS = 12   # 페이지마다 시험해 보는 조립 변형 수


def assemble_pages(site, statuses=("draft",)):
    """콤보마다 composer 로 조립 → 린트 → 형제(같은 부모·같은 유형) 고유성 → page_contents 1행(본문은 저장하지 않고 변형 번호만 {"v": k}).
    형제 묶음 안에서 id 순으로, 앞 형제들과 가장 덜 겹치는 변형을 고른다(공개된 페이지는 변형을 바꾸지 않는다 — 본문이 흔들리지 않게).
    통과분 자동 승인(approved), 못 넘으면 review. {상태: 수}"""
    import composer
    from collections import Counter, defaultdict
    from db import q as _q
    todo = {c["id"]: c for c in _q("SELECT * FROM keyword_combos WHERE site_id=%s AND status IN (" + ",".join(["%s"] * len(statuses)) + ")",
                                   (site["id"], *statuses))}
    if not todo:
        return {}
    verticals = {v["id"]: v for v in _q("SELECT * FROM verticals WHERE site_id=%s", (site["id"],))}
    allc = _q("SELECT k.*, p.blocks pblocks, p.model pmodel FROM keyword_combos k LEFT JOIN page_contents p ON p.combo_id=k.id AND p.version=k.content_version "
              "WHERE k.site_id=%s AND k.status IN ('draft','review','approved','published','noindex') ORDER BY k.id", (site["id"],))
    groups = defaultdict(list)
    for c in allc:
        groups[(c["parent_combo_id"], c["page_type"])].append(c)
    jac = lambda a, b: len(a & b) / (len(a | b) or 1)  # noqa: E731
    chosen = {}   # id → (v, page, shingles)
    need = {(c["parent_combo_id"], c["page_type"]) for c in todo.values()}  # 새 페이지가 있는 형제 묶음만
    for key, members in groups.items():
        if key not in need:
            continue
        placed = []
        for c in members:
            v_fixed = None
            if c["id"] not in todo or c["status"] == "published":
                v_fixed = (js(c["pblocks"], {}) or {}).get("v", 0) if c["pmodel"] == "assembled" else 0
            best = None
            for v in ([v_fixed] if v_fixed is not None else range(VARIANTS if key[0] else 1)):
                pg = composer.compose(c, verticals[c["vertical_id"]], site, v)
                if not pg:
                    break
                sh = shingles(composer.plain(pg))
                worst = max((jac(sh, o) for o in placed), default=0.0)
                if best is None or worst < best[0]:
                    best = (worst, v, pg, sh)
                if worst <= 0.15:
                    break
            if best:
                chosen[c["id"]] = best[1:]
                placed.append(best[3])
    out = Counter()
    for cid, c in todo.items():
        if cid not in chosen:
            out["조립 불가"] += 1
            continue
        v, pg, mine = chosen[cid]
        sibs = [chosen[o["id"]][2] for o in groups[(c["parent_combo_id"], c["page_type"])] if o["id"] != cid and o["id"] in chosen] if c["parent_combo_id"] else []
        worst = max((jac(mine, o) for o in sibs), default=0.0)
        result = lint(composer.plain(pg) + " " + pg["meta"])
        passed = result["ok"] and worst <= UNIQUENESS_MAX
        old = ops.latest_content(cid)
        version = (old["version"] if old else 0) + 1
        content_id = ops.insert_content(cid, version, {"h1": pg["h1"], "title": pg["title"], "meta_description": pg["meta"], "blocks": {"v": v}},
                                        "assembled", PROMPT_VERSION)
        ops.save_scores(content_id, round(worst, 3), result, [], "approved" if passed else ("lint_fail" if not result["ok"] else "review"))
        if passed:
            ops.set_combo(cid, content_version=version, status=c["status"] if c["status"] in ("published", "noindex") else "approved")
        elif c["status"] in ("draft", "generated"):
            ops.set_combo(cid, status="review")
        out["approved" if passed else ("lint_fail" if not result["ok"] else "유사도 초과")] += 1
    dbq.bump_cache_version(site["id"])
    return dict(out)
