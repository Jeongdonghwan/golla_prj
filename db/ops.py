"""SQL 함수: 렌더 보조, 통계, 콘텐츠 파이프라인, 키워드, 측정."""
import json

from . import ex, q, q1

J = lambda v: json.dumps(v, ensure_ascii=False, default=str)  # noqa: E731


def set_setting(site_id, key, value):
    ex("REPLACE INTO site_settings (site_id, `key`, `value`) VALUES (%s,%s,%s)", (site_id, key, value))


# ── 렌더 보조 ─────────────────────────────────────────────────────────────
def anchor_variants(combo_ids):
    out = {}
    if combo_ids:
        for r in q("SELECT combo_id, anchor FROM anchor_variants WHERE combo_id IN (%s) ORDER BY combo_id, anchor"
                   % ",".join(["%s"] * len(combo_ids)), combo_ids):
            out.setdefault(r["combo_id"], []).append(r["anchor"])
    return out


def default_author(site=None):
    return for_site(q1("SELECT * FROM authors ORDER BY id LIMIT 1"), site)


def for_site(author, site):
    """이름이 골라봄이 아닌 독립 사이트(home '살림9단의 가구가전 핫템')는 작성자도 그 사이트 이름 편집팀으로 보인다."""
    if author and site and site["name"] != "골라봄":
        return {**author, "name": f"{site['name']} 편집팀", "role": f"{site['group_name']} 종류별 고르는 기준 정리"}
    return author


def author(slug):
    return q1("SELECT * FROM authors WHERE slug=%s", (slug,))


def vertical_by_base(site_id, url_base):
    return q1("SELECT * FROM verticals WHERE site_id=%s AND url_base=%s AND is_active=1", (site_id, url_base))


def latest_published_vertical(vertical_id, n=20):
    return q("""SELECT c.url_path, c.content_updated_at, p.title, p.meta_description FROM keyword_combos c
                JOIN page_contents p ON p.combo_id=c.id AND p.version=c.content_version
                WHERE c.vertical_id=%s AND c.status='published' ORDER BY c.content_updated_at DESC, c.id LIMIT %s""", (vertical_id, n))


# ── 통계 ──────────────────────────────────────────────────────────────────
def add_impressions(site_id, combo_id, shown):
    """shown = [(slot, offer_id)]"""
    if shown:
        ex("INSERT INTO stats_daily (`date`, site_id, combo_id, offer_id, slot, impressions) VALUES "
           + ",".join(["(CURDATE(),%s,%s,%s,%s,1)"] * len(shown)) + " ON DUPLICATE KEY UPDATE impressions=impressions+1",
           [v for slot, offer_id in shown for v in (site_id, combo_id, offer_id, slot)])


def rollup(site_id, day):
    """clicks → stats_daily(하루치 다시 계산, 노출 수는 실시간 누적값 유지)."""
    ex("UPDATE stats_daily SET clicks=0 WHERE `date`=%s AND site_id=%s", (day, site_id))
    ex("""INSERT INTO stats_daily (`date`, site_id, combo_id, offer_id, slot, clicks)
          SELECT DATE(created_at), site_id, IFNULL(combo_id,0), offer_id, IFNULL(slot,''), COUNT(*) FROM clicks
          WHERE is_bot=0 AND DATE(created_at)=%s AND site_id=%s GROUP BY IFNULL(combo_id,0), offer_id, IFNULL(slot,'')
          ON DUPLICATE KEY UPDATE clicks=VALUES(clicks)""", (day, site_id))


def dashboard(group, days=30):
    """group: offer | combo | vertical"""
    name, join = {
        "offer": ("o.name", "JOIN offers o ON o.id=s.offer_id"),
        "combo": ("IFNULL(k.primary_keyword,'(페이지 없음)')", "LEFT JOIN keyword_combos k ON k.id=s.combo_id"),
        "vertical": ("IFNULL(v.name,'(미분류)')", "JOIN offers o ON o.id=s.offer_id LEFT JOIN verticals v ON v.id=o.vertical_id"),
    }[group]
    return q(f"""SELECT {name} name, SUM(s.impressions) impressions, SUM(s.clicks) clicks
                 FROM stats_daily s {join} WHERE s.`date`>=CURDATE()-INTERVAL %s DAY GROUP BY name ORDER BY clicks DESC, impressions DESC LIMIT 50""", (days,))


# ── 콘텐츠 파이프라인(Phase 2) ────────────────────────────────────────────
def sibling_contents(combo):
    """고유성 비교 대상: 같은 부모·같은 page_type 콤보의 현재 버전 블록."""
    return q("""SELECT k.id combo_id, p.blocks FROM keyword_combos k JOIN page_contents p ON p.combo_id=k.id AND p.version=k.content_version
                WHERE k.parent_combo_id<=>%s AND k.page_type=%s AND k.id<>%s AND k.parent_combo_id IS NOT NULL""",
             (combo["parent_combo_id"], combo["page_type"], combo["id"]))


def latest_content(combo_id):
    return q1("SELECT * FROM page_contents WHERE combo_id=%s ORDER BY version DESC LIMIT 1", (combo_id,))


def content_by_id(content_id):
    return q1("SELECT * FROM page_contents WHERE id=%s", (content_id,))


def insert_content(combo_id, version, data, model, prompt_version):
    return ex("""INSERT INTO page_contents (combo_id, version, h1, title, meta_description, blocks, faqs, links, model, prompt_version, status)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'generated')""",
              (combo_id, version, data["h1"][:150], data["title"][:160], data["meta_description"][:200], J(data["blocks"]),
               J(data.get("faqs") or []) if data.get("faqs") else None, J(data.get("internal_link_suggestions") or []), model, prompt_version))


def save_scores(content_id, uniqueness, lint_result, fact_flags, status=None):
    ex("UPDATE page_contents SET uniqueness_score=%s, lint_result=%s, fact_flags=%s" + (", status=%s" if status else "") + " WHERE id=%s",
       [uniqueness, J(lint_result), J(fact_flags)] + ([status] if status else []) + [content_id])


def review_content(content_id, status, reviewer):
    ex("UPDATE page_contents SET status=%s, reviewer=%s, reviewed_at=NOW() WHERE id=%s", (status, reviewer, content_id))


def set_combo(combo_id, **fields):
    ex(f"UPDATE keyword_combos SET {','.join(f'{k}=%s' for k in fields)} WHERE id=%s", list(fields.values()) + [combo_id])


def review_queue():
    return q("""SELECT p.id, p.combo_id, p.version, p.status, p.h1, p.uniqueness_score, p.model, k.url_path, k.page_type
                FROM page_contents p JOIN keyword_combos k ON k.id=p.combo_id WHERE p.status IN ('review','lint_fail','lint_ok','generated')
                ORDER BY p.id LIMIT 200""")


POST_TYPE = "question"  # 질문 글(미리 써 둔 글을 하루 posts_per_day 편씩 연다). 키워드 페이지 공개 몫과 따로 센다


def combos_by_status(site_id, statuses, limit=5000, with_url=True, posts=None):
    """posts=None 전체 / True 질문 글만 / False 질문 글 제외."""
    kind = "" if posts is None else (" AND page_type='question'" if posts else " AND page_type<>'question'")
    return q("SELECT * FROM keyword_combos WHERE site_id=%s AND status IN (" + ",".join(["%s"] * len(statuses)) + ")"
             + (" AND url_path IS NOT NULL" if with_url else "") + kind + " ORDER BY priority_score DESC, id LIMIT %s", [site_id, *statuses, limit])


def published_last_days(site_id, days=7):
    return q1("SELECT COUNT(*) n FROM keyword_combos WHERE site_id=%s AND published_at>=NOW()-INTERVAL %s DAY", (site_id, days))["n"]


def published_today(site_id, posts=False):
    kind = "=" if posts else "<>"
    return q1(f"SELECT COUNT(*) n FROM keyword_combos WHERE site_id=%s AND published_at>=CURDATE() AND page_type{kind}'question'", (site_id,))["n"]


def count_posts(site_id):
    return {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM keyword_combos WHERE site_id=%s AND page_type='question' GROUP BY status", (site_id,))}


def stage_all(site_id, keep_types=()):
    """공개 페이지를 모두 approved 로 되돌린다(오픈 전 준비). keep_types 의 page_type 은 그대로 둔다."""
    keep = " AND page_type NOT IN (" + ",".join(["%s"] * len(keep_types)) + ")" if keep_types else ""
    return ex("UPDATE keyword_combos SET status='approved', published_at=NULL, content_updated_at=NULL WHERE site_id=%s AND status='published'" + keep,
              [site_id, *keep_types])


def count_status(site_id):
    return {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM keyword_combos WHERE site_id=%s GROUP BY status", (site_id,))}


def publish_combo(combo_id):
    ex("UPDATE keyword_combos SET status='published', published_at=IFNULL(published_at, NOW()), content_updated_at=IFNULL(content_updated_at, NOW()) WHERE id=%s",
       (combo_id,))


# ── 키워드(Phase 4) ───────────────────────────────────────────────────────
def terms(vertical_id):
    return q("""SELECT t.*, d.slug dim FROM keyword_terms t JOIN keyword_dimensions d ON d.id=t.dimension_id
                WHERE t.vertical_id=%s AND t.is_active=1 ORDER BY t.id""", (vertical_id,))


def all_combos(vertical_id):
    return q("SELECT * FROM keyword_combos WHERE vertical_id=%s ORDER BY id", (vertical_id,))


def insert_combo(**c):
    return ex(f"INSERT INTO keyword_combos ({','.join(c)}) VALUES ({','.join(['%s'] * len(c))})", list(c.values()))


# ── SEO 확장(Phase 5) ─────────────────────────────────────────────────────
def set_vertical_og(vertical_id, path):
    """종류별 OG 카드를 그 종류의 모든 페이지에 연결(페이지별 생성은 수만 장이라 하지 않는다)."""
    ex("""INSERT INTO pages (combo_id, og_image_path) SELECT id, %s FROM keyword_combos WHERE vertical_id=%s
          ON DUPLICATE KEY UPDATE og_image_path=VALUES(og_image_path)""", (path, vertical_id))


def set_og_image(combo_id, path):
    ex("INSERT INTO pages (combo_id, og_image_path) VALUES (%s,%s) ON DUPLICATE KEY UPDATE og_image_path=VALUES(og_image_path)", (combo_id, path))


def indexnow_pending(site_id, limit):
    return q("SELECT * FROM indexnow_queue WHERE site_id=%s AND sent_at IS NULL ORDER BY id LIMIT %s", (site_id, limit))


def indexnow_mark(ids, code):
    ex("UPDATE indexnow_queue SET sent_at=NOW(), response_code=%s WHERE id IN (" + ",".join(["%s"] * len(ids)) + ")", [code, *ids])


def replace_anchors(combo_id, anchors):
    ex("DELETE FROM anchor_variants WHERE combo_id=%s", (combo_id,))
    for a in anchors:
        ex("INSERT IGNORE INTO anchor_variants (combo_id, anchor) VALUES (%s,%s)", (combo_id, a))


# ── 측정(Phase 6) ─────────────────────────────────────────────────────────
IMPORTS = {  # kind → (table, CSV 열 순서)
    "gsc": ("gsc_daily", ["date", "page", "query", "device", "clicks", "impressions", "ctr", "position"]),
}


def import_rows(kind, site_id, rows):
    table, cols = IMPORTS[kind]
    with_site = True
    names = cols[:1] + (["site_id"] if with_site else []) + cols[1:]
    sql = (f"REPLACE INTO {table} ({','.join('`%s`' % c for c in names)}) VALUES ({','.join(['%s'] * len(names))})")
    for r in rows:
        ex(sql, [r[cols[0]]] + ([site_id] if with_site else []) + [r.get(c) or None for c in cols[1:]])
    return len(rows)


def add_bot_hits(rows):
    for (day, path, bot, status), hits in rows.items():
        ex("REPLACE INTO bot_crawl_daily (`date`, path, bot, hits, status) VALUES (%s,%s,%s,%s,%s)", (day, path, bot, hits, status))
