"""쿠팡 파트너스 Open API 상품 수집. 쿠팡 사이트(8~12)는 CSV 가 아니라 API 로 매일 자동으로 받는다.

- 사이트·분야·종류: data/coupang_kinds.json (세션이 종류를 덧붙인다 → sync_kinds 가 sites·verticals 행으로).
- 수집 대기열 coupang_kw(종류, 검색어): 종류명 + 카드가 생기면 '수식어 종류명'까지. 가장 오래전에 돈 검색어부터 돈다(계속 순환).
- 검색 1회 = 상품 10개(상한). 새 상품은 딥링크 API 로 고정 짧은 링크(subId = 사이트 라벨)를 받아 offers.url 에, 쿠팡 상품 번호는 offers.ext_id 에.
- 키: .env 의 COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY.
"""
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import quote

import products
from db import ex, q, q1

ROOT = Path(__file__).parent
KINDS_FILE = ROOT / "data" / "coupang_kinds.json"
API = "/v2/providers/affiliate_open_api/apis/openapi/v1"
STALE_DAYS = 14   # 이만큼 검색 결과에 다시 안 나온 상품은 내린다


def call(method, path, query="", body=None):
    signed = time.strftime("%y%m%dT%H%M%SZ", time.gmtime())
    sig = hmac.new(os.environ["COUPANG_SECRET_KEY"].encode(), f"{signed}{method}{path}{query}".encode(), hashlib.sha256).hexdigest()
    auth = f"CEA algorithm=HmacSHA256, access-key={os.environ['COUPANG_ACCESS_KEY']}, signed-date={signed}, signature={sig}"
    req = urllib.request.Request("https://api-gateway.coupang.com" + path + ("?" + query if query else ""), method=method,
                                 data=json.dumps(body).encode() if body else None, headers={"Authorization": auth, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"rCode": str(e.code), "rMessage": e.read().decode("utf-8", "replace")[:300]}


def ensure_schema():
    """쿠팡용 열·표를 없을 때만 만든다(서버 cron 에서도 그대로 돈다)."""
    has = lambda t, c: q1("SELECT 1 x FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=%s AND column_name=%s", (t, c))  # noqa: E731
    if not has("offers", "ext_id"):
        ex("ALTER TABLE offers ADD COLUMN ext_id VARCHAR(40) NULL UNIQUE, MODIFY image VARCHAR(1500) NULL")
    if not has("verticals", "section"):
        ex("ALTER TABLE verticals ADD COLUMN section VARCHAR(30) NULL")
    ex("CREATE TABLE IF NOT EXISTS coupang_kw (vertical_id SMALLINT UNSIGNED NOT NULL, keyword VARCHAR(80) NOT NULL, "
       "collected_at DATETIME NULL, found SMALLINT NOT NULL DEFAULT 0, PRIMARY KEY(vertical_id, keyword), KEY idx_at(collected_at))")


def site_ids():
    return [s["id"] for s in json.loads(KINDS_FILE.read_text("utf-8")).values()]


def sync_kinds(domain="golrabom.kr"):
    """coupang_kinds.json → sites(설정은 1번 사이트에서 복사) · verticals. 이미 다른 사이트에 있는 종류명은 건너뛴다. (새 종류 수, 건너뛴 이름)"""
    import secrets
    ensure_schema()
    cfg = json.loads(KINDS_FILE.read_text("utf-8"))
    have = {r["name"]: r["site_id"] for r in q("SELECT name, site_id FROM verticals")}
    added, skipped = 0, []
    for label, s in cfg.items():
        if not q1("SELECT id FROM sites WHERE id=%s", (s["id"],)):
            ex("INSERT INTO sites (id, host, name, group_name, indexnow_key) VALUES (%s,%s,%s,%s,%s)", (s["id"], f"{label}.{domain}", "골라봄", s["group"], secrets.token_hex(16)))
            ex("INSERT INTO site_settings (site_id, `key`, `value`) SELECT %s, `key`, `value` FROM site_settings WHERE site_id=1 AND `key` LIKE 'op\\_%%'", (s["id"],))
            for k, v in (("theme", json.dumps({"accent": s["accent"]})), ("network", "coupang")):
                ex("INSERT INTO site_settings (site_id, `key`, `value`) VALUES (%s,%s,%s)", (s["id"], k, v))
        for section, names in s["sections"].items():
            for name in names:
                if name in have:   # 이름이 같은 종류가 이미 있으면(기존 7개 사이트 포함) 만들지 않는다
                    if have.get(name) != s["id"]:
                        skipped.append(name)
                    continue
                slug = products.slugify(name)
                ex("INSERT INTO verticals (site_id, slug, name, url_base, section, is_active) VALUES (%s,%s,%s,%s,%s,0)", (s["id"], slug, name, "/" + slug, section))
                have[name] = s["id"]
                added += 1
    return added, skipped


def sync_queue():
    """대기열에 검색어 채우기: 종류명 + (카드가 있으면) '수식어 종류명'."""
    ids = site_ids()
    for v in q("SELECT id, name, card FROM verticals WHERE parent_id IS NULL AND site_id IN (" + ",".join(["%s"] * len(ids)) + ")", ids):
        mods = [t for ax in json.loads(v["card"] or "{}").get("mods", {}).values() for t in ax]
        for kw in [v["name"]] + [f"{t} {v['name']}" for t in mods]:
            ex("INSERT IGNORE INTO coupang_kw (vertical_id, keyword) VALUES (%s,%s)", (v["id"], kw[:80]))


def deeplinks(urls, sub_id):
    """상품 주소 → 고정 짧은 제휴 링크. {원래 주소: 짧은 링크}"""
    r = call("POST", API + "/deeplink", body={"coupangUrls": urls, "subId": sub_id})
    if r.get("rCode") == "400" and len(urls) > 1:   # 변환 안 되는 상품 하나가 묶음 전체를 실패시킨다 → 하나씩 다시, 안 되는 것만 버린다
        out = {}
        for u in urls:
            one = call("POST", API + "/deeplink", body={"coupangUrls": [u], "subId": sub_id})
            if one.get("rCode") not in ("0", "400"):
                raise RuntimeError(f"deeplink {one.get('rCode')} {one.get('rMessage', '')[:200]}")
            out.update({d["originalUrl"]: d["shortenUrl"] for d in one.get("data") or [] if d.get("shortenUrl")})
            time.sleep(1)
        return out
    if r.get("rCode") == "400":
        return {}
    if r.get("rCode") != "0":
        raise RuntimeError(f"deeplink {r.get('rCode')} {r.get('rMessage', '')[:200]}")
    return {d["originalUrl"]: d["shortenUrl"] for d in r.get("data") or [] if d.get("shortenUrl")}   # 링크를 못 받은 상품은 이번엔 건너뛴다


def collect(calls=200, delay=8.0, log=print):
    """대기열에서 오래된 검색어부터 calls 개. 오류(한도 등)가 나면 바로 멈춘다. (돈 검색어 수, 새 상품 수)"""
    ensure_schema()
    sync_queue()
    labels = {s["id"]: label for label, s in json.loads(KINDS_FILE.read_text("utf-8")).items()}
    rows = q("SELECT k.vertical_id, k.keyword, k.collected_at, v.site_id, v.slug FROM coupang_kw k JOIN verticals v ON v.id=k.vertical_id")
    carded = {p.stem for p in (ROOT / "content" / "cards").glob("*.json")}   # 안 돈 검색어 → 카드가 있는 종류 → 오래된 순
    rows = sorted(rows, key=lambda r: (r["collected_at"] is not None, r["slug"] not in carded, str(r["collected_at"]), r["vertical_id"]))[:calls]
    done = added = 0
    touched = set()
    for row in rows:
        r = call("GET", API + "/products/search", f"keyword={quote(row['keyword'])}&limit=10")
        if r.get("rCode") != "0":
            log(f"멈춤({row['keyword']}): {r.get('rCode')} {r.get('rMessage', '')[:200]}")
            break
        items = list({p["productId"]: p for p in reversed((r.get("data") or {}).get("productData") or [])}.values())[::-1]   # 옵션만 다른 같은 상품은 하나로
        known = {x["ext_id"]: x for x in q("SELECT id, ext_id, vertical_id FROM offers WHERE ext_id IN (" + ",".join(["%s"] * len(items)) + ")",
                                           [f"cp:{p['productId']}" for p in items])} if items else {}
        fresh = {f"https://www.coupang.com/vp/products/{p['productId']}": p for p in items if f"cp:{p['productId']}" not in known}
        try:
            links = deeplinks(list(fresh), labels[row["site_id"]]) if fresh else {}
        except RuntimeError as e:
            log(f"멈춤({row['keyword']}): {e}")
            break
        for p in items:
            ext, price = f"cp:{p['productId']}", int(p.get("productPrice") or 0)
            tags = json.dumps(products.tags_of(p["productName"], price), ensure_ascii=False)
            if ext in known:   # 다시 나온 상품: 이름·이미지·가격 갱신(종류는 처음 잡힌 곳 그대로)
                ex("UPDATE offers SET name=%s, image=%s, sale_price=%s, status='active', imported_at=NOW() WHERE id=%s",
                   (p["productName"][:200], p.get("productImage", "")[:1500], price, known[ext]["id"]))
                continue
            url = links.get(f"https://www.coupang.com/vp/products/{p['productId']}")
            if not url:
                continue
            ex("INSERT IGNORE INTO offers (vertical_id, url, ext_id, name, advertiser, tags, image, sale_price, score, classified_by, imported_at) "
               "VALUES (%s,%s,%s,%s,'쿠팡',%s,%s,%s,%s,'dict',NOW())",
               (row["vertical_id"], url, ext, p["productName"][:200], tags, p.get("productImage", "")[:1500], price, 100 - int(p.get("rank") or 50)))
            added += 1
        ex("UPDATE coupang_kw SET collected_at=NOW(), found=%s WHERE vertical_id=%s AND keyword=%s", (len(items), row["vertical_id"], row["keyword"]))
        touched.add(row["vertical_id"])
        done += 1
        time.sleep(delay)
    for vid in [r["vertical_id"] for r in q("SELECT DISTINCT vertical_id FROM offers WHERE ext_id IS NOT NULL AND vertical_id IS NOT NULL")]:   # 브랜드(중간에 끊긴 실행분까지 매번 전부): 그 종류 안에서 첫 낱말이 3개 이상 겹칠 때만(기존 규칙과 같다)
        offs = q("SELECT id, name, tags FROM offers WHERE vertical_id=%s AND status='active'", (vid,))
        n = Counter(products.brand_token(o["name"]) for o in offs)
        for o in offs:
            b = products.brand_token(o["name"])
            b = b if b and n[b] >= 3 else None
            tags = [t for t in json.loads(o["tags"] or "[]") if not t.startswith("@")] + (["@" + b] if b else [])
            ex("UPDATE offers SET brand=%s, tags=%s WHERE id=%s", (b, json.dumps(tags, ensure_ascii=False), o["id"]))
    # ponytail: 대기열 한 바퀴가 STALE_DAYS 안에 돌았을 때만 안 보이는 상품을 내린다(바퀴가 더 길면 멀쩡한 상품이 내려가므로). 대기열이 커지면 하루 호출 수를 늘린다.
    if not q1(f"SELECT 1 x FROM coupang_kw WHERE collected_at IS NULL OR collected_at < NOW() - INTERVAL {STALE_DAYS} DAY LIMIT 1"):
        ex(f"UPDATE offers SET status='inactive' WHERE ext_id IS NOT NULL AND status='active' AND imported_at < NOW() - INTERVAL {STALE_DAYS} DAY")
    products.refresh_kinds()
    ex("UPDATE sites SET cache_version=cache_version+1")
    return done, added


DUMP = ROOT / "data" / "coupang_offers.json"
COLS = ("url", "ext_id", "name", "brand", "tags", "image", "sale_price", "score")


def dump():
    """로컬에서 받은 쿠팡 상품 → data/coupang_offers.json (git). 서버 첫 적재용: 카드가 본 상품 목록과 서버 상품을 같게 맞춘다."""
    rows = q("SELECT v.name kind, " + ",".join("o." + c for c in COLS) + " FROM offers o JOIN verticals v ON v.id=o.vertical_id "
             "WHERE o.ext_id IS NOT NULL AND o.status='active' ORDER BY o.id")
    DUMP.write_text(json.dumps([{**r, "score": float(r["score"])} for r in rows], ensure_ascii=False, indent=0), "utf-8")
    return len(rows)


def load_dump():
    """data/coupang_offers.json → offers(없는 ext_id 만 추가). 추가한 수."""
    ensure_schema()
    kinds = {v["name"]: v["id"] for v in q("SELECT id, name FROM verticals WHERE parent_id IS NULL")}
    have = {r["ext_id"] for r in q("SELECT ext_id FROM offers WHERE ext_id IS NOT NULL")}
    n = 0
    for r in json.loads(DUMP.read_text("utf-8")):
        if r["ext_id"] in have or r["kind"] not in kinds:
            continue
        ex("INSERT IGNORE INTO offers (vertical_id, advertiser, classified_by, imported_at, " + ",".join(COLS) + ") VALUES (%s,'쿠팡','dict',NOW()," + ",".join(["%s"] * len(COLS)) + ")",
           [kinds[r["kind"]], *[r[c] for c in COLS]])
        n += 1
    products.refresh_kinds()
    ex("UPDATE sites SET cache_version=cache_version+1")
    return n
