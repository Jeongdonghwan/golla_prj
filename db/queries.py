from . import ex, q, q1

# ── 사이트 ────────────────────────────────────────────────────────────────
def site(host=None):
    """host 가 주어지면 그 호스트(또는 같은 서브도메인 라벨: law.localhost → law.도메인)의 사이트, 없으면 첫 사이트."""
    if host:
        label = host.split(":")[0].split(".")[0]
        s = q1("SELECT * FROM sites WHERE host=%s OR host LIKE %s ORDER BY (host=%s) DESC, id LIMIT 1", (host, label + ".%", host))
        if s:
            return s
    return q1("SELECT * FROM sites ORDER BY id LIMIT 1")


def sites():
    return q("SELECT * FROM sites ORDER BY id")


def settings(site_id):
    return {r["key"]: r["value"] for r in q("SELECT `key`,`value` FROM site_settings WHERE site_id=%s", (site_id,))}


def site_og(site_id):
    """메인·고정 페이지용 og:image — 상품군 허브의 OG 카드."""
    r = q1("SELECT p.og_image_path FROM pages p JOIN keyword_combos k ON k.id=p.combo_id "
           "WHERE k.site_id=%s AND k.page_type='group_hub' AND p.og_image_path IS NOT NULL ORDER BY k.id LIMIT 1", (site_id,))
    return r and r["og_image_path"]


def redirect_for(site_id, path):
    return q1("SELECT to_path, code FROM redirects WHERE site_id=%s AND from_path=%s", (site_id, path))


def bump_cache_version(site_id):
    ex("UPDATE sites SET cache_version=cache_version+1 WHERE id=%s", (site_id,))


def verticals(site_id):
    return q("SELECT * FROM verticals WHERE site_id=%s AND is_active=1 ORDER BY name", (site_id,))


def vertical(vertical_id):
    return q1("SELECT * FROM verticals WHERE id=%s", (vertical_id,))


def term(term_id):
    return q1("SELECT * FROM keyword_terms WHERE id=%s", (term_id,))


# ── 콤보·콘텐츠 ───────────────────────────────────────────────────────────
def combo(combo_id):
    return q1("SELECT * FROM keyword_combos WHERE id=%s", (combo_id,))


def combo_by_url(site_id, url_path):
    return q1("SELECT * FROM keyword_combos WHERE site_id=%s AND url_path=%s", (site_id, url_path))


def content(combo_id, version):
    return q1("SELECT * FROM page_contents WHERE combo_id=%s AND version=%s AND status='approved'", (combo_id, version))


def published(site_id, page_types=None, limit=50000, offset=0):
    where, args = "", [site_id]
    if page_types:
        where = " AND page_type IN (%s)" % ",".join(["%s"] * len(page_types))
        args += list(page_types)
    return q(f"""SELECT id, page_type, url_path, primary_keyword, content_updated_at FROM keyword_combos
                 WHERE site_id=%s AND status='published'{where} ORDER BY id LIMIT %s OFFSET %s""", args + [limit, offset])


def count_published(site_id, page_types):
    return q1("SELECT COUNT(*) n FROM keyword_combos WHERE site_id=%s AND status='published' AND page_type IN ("
              + ",".join(["%s"] * len(page_types)) + ")", [site_id] + list(page_types))["n"]


def latest_published(site_id, n=20):
    return q("""SELECT c.url_path, c.content_updated_at, p.title, p.meta_description FROM keyword_combos c
                JOIN page_contents p ON p.combo_id=c.id AND p.version=c.content_version
                WHERE c.site_id=%s AND c.status='published' ORDER BY c.content_updated_at DESC, c.id LIMIT %s""", (site_id, n))


_LINK = "SELECT id, vertical_id, url_path, primary_keyword FROM keyword_combos WHERE status='published' AND "


def children(combo_id, n=40):
    return q(_LINK + "parent_combo_id=%s ORDER BY id LIMIT %s", (combo_id, n))


def siblings(c, n=12):
    return q(_LINK + "parent_combo_id=%s AND page_type=%s AND id<>%s ORDER BY id LIMIT %s",
             (c["parent_combo_id"], c["page_type"], c["id"], n))


def kind_core(c, n=8):
    """같은 종류의 A등급 페이지(허브·의도·대표 공간) — 깊은 페이지에서도 핵심 페이지로 이어지게."""
    return q(_LINK + "vertical_id=%s AND grade='A' AND id<>%s AND page_type<>'group_hub' ORDER BY priority_score DESC, id LIMIT %s",
             (c["vertical_id"], c["id"], n))


def page(combo_id):
    return q1("SELECT * FROM pages WHERE combo_id=%s", (combo_id,))


def upsert_page(combo_id, cache_version, ranking_version, content_hash, in_sitemap, noindex):
    ex("""INSERT INTO pages (combo_id, rendered_at, cache_version, ranking_version, content_hash, in_sitemap, noindex)
          VALUES (%s,NOW(),%s,%s,%s,%s,%s)
          ON DUPLICATE KEY UPDATE rendered_at=NOW(), cache_version=VALUES(cache_version),
            ranking_version=VALUES(ranking_version), content_hash=VALUES(content_hash), in_sitemap=VALUES(in_sitemap), noindex=VALUES(noindex)""",
       (combo_id, cache_version, ranking_version, content_hash, in_sitemap, noindex))


def touch_combo(combo_id, only_if_null=False):
    ex("UPDATE keyword_combos SET content_updated_at=NOW() WHERE id=%s" + (" AND content_updated_at IS NULL" if only_if_null else ""),
       (combo_id,))


def queue_indexnow(site_id, url, reason):
    ex("INSERT INTO indexnow_queue (site_id, url, reason) VALUES (%s,%s,%s)", (site_id, url, reason))


# ── 상품·클릭 ─────────────────────────────────────────────────────────────
def active_offers(vertical_ids):
    if not vertical_ids:
        return []
    return q("SELECT * FROM offers WHERE status='active' AND vertical_id IN (" + ",".join(["%s"] * len(vertical_ids)) + ") ORDER BY id",
             list(vertical_ids))


def active_offer(offer_id):
    return q1("SELECT * FROM offers WHERE id=%s AND status='active'", (offer_id,))


def offer(offer_id):
    return q1("SELECT * FROM offers WHERE id=%s", (offer_id,))


def insert_click(**c):
    cols = ",".join(c)
    ex(f"INSERT INTO clicks ({cols}) VALUES ({','.join(['%s'] * len(c))})", list(c.values()))


# ── 관리자(범용 CRUD) ─────────────────────────────────────────────────────
# table/cols 는 admin.py 의 화이트리스트에서만 온다.
def admin_list(table, cols, limit=500):
    return q(f"SELECT id, {','.join(cols)} FROM {table} ORDER BY id LIMIT %s", (limit,))


def admin_get(table, row_id):
    return q1(f"SELECT * FROM {table} WHERE id=%s", (row_id,))


def admin_save(table, row_id, data):
    if row_id:
        ex(f"UPDATE {table} SET {','.join(f'`{k}`=%s' for k in data)} WHERE id=%s", list(data.values()) + [row_id])
        return row_id
    return ex(f"INSERT INTO {table} ({','.join(f'`{k}`' for k in data)}) VALUES ({','.join(['%s'] * len(data))})", list(data.values()))


def audit(actor, action, target, diff_json):
    ex("INSERT INTO audit_log (actor, action, target, diff) VALUES (%s,%s,%s,%s)", (actor, action, target, diff_json))
