"""배치 잡: `flask --app app jobs <name>`. cron 에서 호출한다. 요청 처리 경로에서는 쓰지 않는다."""
import csv
import io
import json
import os
import re
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit

import click
from flask import current_app, g
from flask.cli import AppGroup

import db
import pipeline
import products
from db import ops, queries as dbq
from engine import STATIC_PATHS, abs_url, site_base

jobs_cli = AppGroup("jobs", help="배치 잡")
ROOT = Path(__file__).parent
BOTS = {"Yeti": "naver", "Googlebot": "google", "Daum": "daum", "bingbot": "bing"}
LOG_RE = re.compile(r'\S+ \S+ \S+ \[(\d{2}/\w{3}/\d{4}):[^\]]+\] "(?:GET|HEAD) (\S+)[^"]*" (\d{3}) \S+ "[^"]*" "([^"]*)"')
HUBS = ("group_hub", "kind_hub")


def _sites(only=None):
    """대상 사이트들. only = 서브도메인 첫 라벨(heat 등) 또는 id. 사이트마다 기준 주소(g.base_url)를 맞춘다."""
    base_host = current_app.config["BASE_URL"].split("://", 1)[-1]
    for s in dbq.sites():
        if only and str(s["id"]) != str(only) and s["host"].split(".")[0] != only:
            continue
        g.base_url = None if s["host"] == base_host else f"https://{s['host']}"
        yield s


SITE_OPT = click.option("--site", "only", default=None, help="사이트(서브도메인 첫 라벨 또는 id). 생략하면 전체")


# ── 상품(F2) ──────────────────────────────────────────────────────────────
@jobs_cli.command("products-import")
@click.argument("path", type=click.Path(exists=True))
def products_import(path):
    """커넥트 상품 CSV 적재(교체): 새 상품 추가·분류 / 빠진 상품 inactive / 수수료 변경 반영. 페이지 본문은 그대로."""
    r = products.import_csv(Path(path).read_text("utf-8-sig"), Path(path).name)
    click.echo(f"상품 {r['rows_total']} · 추가 {r['added']} · 제외 {r['removed']} · 변경 {r['changed']} · 미분류 {r['unclassified']} · 오류 {len(r['errors'])}")
    for e in r["errors"][:20]:
        click.echo(f"  {e}")


@jobs_cli.command("products-inbox")
@click.option("--dir", "folder", default=str(ROOT / "data" / "inbox"))
def products_inbox(folder):
    """매일 새 상품: data/inbox/*.csv 중 아직 안 넣은 파일(offer_imports.filename 기준)을 '추가만' 모드로 넣고 세션 분류를 적용.
    서버 cron 05:30 이 git pull 뒤에 돌린다. 기존 상품은 내리지 않는다(전체 교체는 관리자 화면 업로드)."""
    done = {r["filename"] for r in db.q("SELECT filename FROM offer_imports")}
    for f in sorted(Path(folder).glob("*.csv")):
        if f.name in done:
            continue
        r = products.import_csv(f.read_text("utf-8-sig"), f.name, add_only=True)
        click.echo(f"{f.name}: 추가 {r['added']} · 변경 {r['changed']} · 미분류 {r['unclassified']} · 오류 {len(r['errors'])}")
    click.echo(f"분류 적용 {products.apply_overrides()}개")


@jobs_cli.command("coupang-sync")
def coupang_sync():
    """data/coupang_kinds.json → 쿠팡 사이트·종류 행(없는 것만)."""
    import coupang
    added, skipped = coupang.sync_kinds()
    click.echo(f"새 종류 {added} · 기존 사이트와 겹쳐 건너뜀 {len(skipped)}: {', '.join(skipped)}")


@jobs_cli.command("coupang-export")
def coupang_export():
    """로컬 쿠팡 상품 → data/coupang_offers.json (커밋해서 서버 첫 적재에 쓴다)."""
    import coupang
    click.echo(f"{coupang.dump()}개 → {coupang.DUMP}")


@jobs_cli.command("coupang-import")
def coupang_import():
    """data/coupang_offers.json → 상품(없는 것만 추가)."""
    import coupang
    click.echo(f"쿠팡 상품 추가 {coupang.load_dump()}")


@jobs_cli.command("coupang-collect")
@click.option("--calls", default=200, help="이번에 돌 검색어 수(검색 1회 = 상품 10개)")
@click.option("--delay", default=8.0, help="호출 사이 초")
@click.option("--site", default=None, help="이 사이트 라벨(home 등)의 검색어만")
def coupang_collect(calls, delay, site):
    """쿠팡 상품 수집: 대기열에서 오래된 검색어부터. 한도 오류가 나면 멈추고 다음 실행 때 이어서 돈다."""
    import coupang
    done, added = coupang.collect(calls, delay, click.echo, site)
    total = db.q1("SELECT COUNT(*) n FROM offers WHERE ext_id IS NOT NULL AND status='active'")["n"]
    click.echo(f"쿠팡 수집: 검색어 {done} · 새 상품 {added} · 쿠팡 상품 합계 {total}")


@jobs_cli.command("products-unclassified")
@click.option("--out", default=str(ROOT / "content" / "classify" / "pending.json"))
def products_unclassified(out):
    """사전으로 못 고른 상품을 내보낸다 → Claude Code 세션이 읽고 data/classify.json 에 {url: {kind, tags}} 를 쓴다 → products-classify."""
    rows = products.unclassified()
    kinds = sorted({k for _g, k, *_ in products.KINDS})
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({"kinds": kinds + [products.SKIP], "items": [{"url": r["url"], "name": r["name"], "store": r["advertiser"]} for r in rows]},
                                    ensure_ascii=False, indent=1), "utf-8")
    click.echo(f"미분류 {len(rows)}개 → {out}")


@jobs_cli.command("products-classify")
def products_classify():
    """data/classify.json(세션 분류)을 현재 상품에 적용."""
    click.echo(f"{products.apply_overrides()}개 적용")
    db.ex("UPDATE sites SET cache_version=cache_version+1")


@jobs_cli.command("products-summary")
def products_summary():
    """종류별 활성 상품 수(상품군 순)."""
    for r in db.q("""SELECT s.host, v.name, COUNT(o.id) n FROM verticals v JOIN sites s ON s.id=v.site_id
                     LEFT JOIN offers o ON o.vertical_id=v.id AND o.status='active' GROUP BY v.id ORDER BY s.id, n DESC"""):
        click.echo(f"{r['host'].split('.')[0]:8} {r['name']:14} {r['n']}")
    st = db.q1("SELECT SUM(status='active') active, SUM(status='inactive') inactive, SUM(status='active' AND classified_by IS NULL) pending, "
               "SUM(status='active' AND vertical_id IS NULL AND classified_by IS NOT NULL) skipped FROM offers")
    click.echo(f"활성 {st['active']} · 빠짐 {st['inactive']} · 미분류 {st['pending']} · 제외 {st['skipped']}")


@jobs_cli.command("offers-thumbs")
@click.option("--limit", default=0, help="이번에 처리할 최대 수(0=전부)")
@click.option("--delay", default=2.0, help="요청 사이 초")
@click.option("--retry", is_flag=True, help="못 찾았던 상품('')도 다시")
def offers_thumbs(limit, delay, retry):
    """썸네일이 없는 활성 상품의 링크를 한 번씩 열어 og:image 를 저장(링크 미리보기와 같은 방식). 새 상품만 처리하므로 매일 돌려도 몇 건."""
    import time
    cond = "(image IS NULL OR image='')" if retry else "image IS NULL"
    rows = db.q(f"SELECT id, url FROM offers WHERE status='active' AND vertical_id IS NOT NULL AND {cond} ORDER BY score DESC" + (f" LIMIT {int(limit)}" if limit else ""))
    ok = 0
    for i, r in enumerate(rows):
        try:
            img = products.fetch_thumb(r["url"])
        except Exception as e:  # 실패는 '' 로 두고 다음 상품
            img = ""
            click.echo(f"  {r['id']} 실패: {e}")
        db.ex("UPDATE offers SET image=%s WHERE id=%s", (img[:500], r["id"]))
        ok += bool(img)
        if i + 1 < len(rows):
            time.sleep(delay)
    if rows:
        db.ex("UPDATE sites SET cache_version=cache_version+1")
    click.echo(f"썸네일 {ok}/{len(rows)}")


# ── 콘텐츠 ────────────────────────────────────────────────────────────────
# ── 공개 ──────────────────────────────────────────────────────────────────
@jobs_cli.command("publish")
@SITE_OPT
@click.option("--quiet", is_flag=True, help="건별 출력 없이 합계만")
def publish(only, quiet):
    """approved 콤보를 게이트 검사 후 공개. 하루 수: publish_plan(날짜별) + 질문 글 posts_per_day."""
    for site in _sites(only):
        rows = pipeline.publish_ready(site)
        if not quiet:
            for url, fails in rows:
                click.echo(f"{url}: {'공개' if not fails else fails}")
        click.echo(f"[{site['host']}] 공개 {sum(1 for _u, f in rows if not f)} · 보류 {sum(1 for _u, f in rows if f)} · 상태 {ops.count_status(site['id'])}")


@jobs_cli.command("plan")
@click.argument("start")
@SITE_OPT
@click.option("--then", "then", type=int, required=True, help="둘째 날부터 하루 공개 수(사용자가 정한다)")
@click.option("--grades", default="ABCD", help="첫날(START) 여는 등급. 기본 전 등급(사용자 결정 2026-11-20: 첫날 만든 페이지 전부, 이후엔 새로 만든 것만 하루 THEN 쪽)")
@click.option("--posts-per-day", type=int, default=None, help="질문 글 하루 편수(생략하면 기존 값 유지)")
def plan(start, only, then, grades, posts_per_day):
    """공개 계획 저장: START 하루에 GRADES 등급 승인 페이지 전부, 이후 나머지(새로 만든 페이지)를 하루 THEN 쪽씩 우선순위 순으로.
    새 페이지는 기존 대기열 뒤에 붙는다(우선순위 + id 순). 열린 페이지는 닫지 않는다."""
    for site in _sites(only):
        marks = ",".join(["%s"] * len(grades))
        first = db.q1(f"SELECT COUNT(*) n FROM keyword_combos WHERE site_id=%s AND status='approved' AND page_type<>'question' "
                      f"AND (grade IN ({marks}) OR page_type IN ('group_hub','kind_hub'))", (site["id"], *grades))["n"]
        posts = ops.count_posts(site["id"]).get("approved", 0)  # 첫날: 써 둔 질문 글도 전부
        ops.set_setting(site["id"], "publish_plan", json.dumps([{"until": start, "per_day": max(first, then), "posts": posts}, {"per_day": then}]))
        if posts_per_day is not None:
            ops.set_setting(site["id"], "posts_per_day", str(posts_per_day))
        rest = ops.count_status(site["id"]).get("approved", 0) - ops.count_posts(site["id"]).get("approved", 0) - first
        click.echo(f"[{site['host']}] {start} {max(first, then)}쪽, 이후 하루 {then}쪽(나머지 {rest}쪽, 약 {-(-rest // then) if then else 0}일)")


@jobs_cli.command("stage")
@SITE_OPT
@click.option("--yes", is_flag=True, help="확인 없이 실행")
def stage(only, yes):
    """오픈 준비: 공개 페이지를 approved 로 되돌린다(허브는 남김). 이후 publish 가 publish_plan 대로 연다."""
    if not yes:
        click.confirm("공개 페이지를 비공개(approved)로 되돌립니다. 계속할까요?", abort=True)
    for site in _sites(only):
        ops.stage_all(site["id"], HUBS)
        dbq.bump_cache_version(site["id"])
        click.echo(f"[{site['host']}] 비공개로 전환 · 상태 {ops.count_status(site['id'])}")


@jobs_cli.command("import")
@click.argument("kind")
@click.argument("path", type=click.Path(exists=True))
@SITE_OPT
def import_cmd(kind, path, only):
    """측정 CSV 적재: gsc (date,page,query,device,clicks,impressions,ctr,position)"""
    rows = list(csv.DictReader(io.StringIO(Path(path).read_text("utf-8-sig"))))
    if kind not in ops.IMPORTS:
        raise click.ClickException(f"알 수 없는 종류: {kind}")
    for site in _sites(only):
        click.echo(f"[{site['host']}] {ops.import_rows(kind, site['id'], rows)}행")


@jobs_cli.command("rollup")
@click.option("--day", default=None, help="YYYY-MM-DD (기본: 오늘)")
def rollup(day):
    """clicks → stats_daily(전 사이트)."""
    for site in _sites():
        ops.rollup(site["id"], day or date.today().isoformat())


# ── SEO ───────────────────────────────────────────────────────────────────
@jobs_cli.command("og-kinds")
@SITE_OPT
def og_kinds(only):
    """종류별 OG 카드(1200x630)를 만들고 그 종류의 모든 페이지에 연결한다."""
    from PIL import Image, ImageDraw, ImageFont
    font_path = os.environ.get("OG_FONT") or r"C:\Windows\Fonts\malgunbd.ttf"
    if not Path(font_path).exists():
        raise click.ClickException(f"한글 폰트가 없습니다: {font_path} (환경변수 OG_FONT 로 지정)")
    big, small = ImageFont.truetype(font_path, 76), ImageFont.truetype(font_path, 32)
    (ROOT / "static" / "og").mkdir(parents=True, exist_ok=True)
    for site in _sites(only):
        st = dbq.settings(site["id"])
        th = json.loads(st.get("theme") or "{}")
        for v in dbq.verticals(site["id"]):
            name = f"v{v['id']}.webp"
            img = Image.new("RGB", (1200, 630), th.get("tint", "#f4f6f8"))
            dr = ImageDraw.Draw(img)
            dr.rectangle([0, 606, 1200, 630], fill=th.get("accent", "#1f6f5c"))
            dr.text((90, 120), site["group_name"], font=small, fill=th.get("accent", "#1f6f5c"))
            dr.text((90, 200), v["name"], font=big, fill=th.get("ink", "#15202b"))
            dr.text((90, 300), "고르는 기준 · 방식 차이 · 관리법", font=small, fill=th.get("muted", "#5c6b7a"))
            dr.text((90, 500), site["name"], font=small, fill=th.get("muted", "#5c6b7a"))
            img.save(ROOT / "static" / "og" / name, "WEBP", quality=82)
            ops.set_vertical_og(v["id"], f"/static/og/{name}")
        dbq.bump_cache_version(site["id"])
        click.echo(f"[{site['host']}] 종류 OG 연결 완료")


SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def _urlset(rows):
    import html as _h
    body = "".join(f"<url><loc>{_h.escape(abs_url(r['url_path']))}</loc>"
                   + (f"<lastmod>{r['content_updated_at']:%Y-%m-%d}</lastmod>" if r.get("content_updated_at") else "") + "</url>\n" for r in rows)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="{SITEMAP_NS}">\n{body}</urlset>\n'


@jobs_cli.command("export-sitemaps")
@click.argument("out", type=click.Path())
@SITE_OPT
@click.option("--chunk", default=1000, help="키워드 사이트맵 1개당 주소 수")
def export_sitemaps(out, only, chunk):
    """사이트별 정적 사이트맵: OUT/<첫 라벨>/sitemap.xml(색인)·sitemap-core.xml·sitemap-posts.xml·sitemap-kw-N.xml + .ops.json.
    nginx 가 이 폴더를 root 로 두고 /sitemap*.xml 을 직접 내준다. SEO 대시보드 수집기가 같은 폴더의 sitemap·.ops.json 을 읽는다."""
    for site in _sites(only):
        d = Path(out) / site["host"].split(".")[0]
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("sitemap*.xml"):
            old.unlink()
        rows = dbq.published(site["id"], None, 10_000_000)
        core = list({r["url_path"]: r for r in [{"url_path": p} for p in ["/", *STATIC_PATHS, "/사이트맵/"]]
                     + [r for r in rows if r["page_type"] in HUBS + ("group_hub",)]}.values())
        posts = [r for r in rows if r["page_type"] == "question"]
        kw = [r for r in rows if r["page_type"] not in HUBS + ("group_hub", "question")]
        (d / "sitemap-core.xml").write_text(_urlset(core), "utf-8")
        files = ["sitemap-core.xml"]
        if posts:  # 질문 글은 따로(매일 늘어 수집 상태를 따로 본다)
            (d / "sitemap-posts.xml").write_text(_urlset(posts), "utf-8")
            files.append("sitemap-posts.xml")
        for i in range(0, len(kw), chunk):
            name = f"sitemap-kw-{i // chunk + 1}.xml"
            (d / name).write_text(_urlset(kw[i:i + chunk]), "utf-8")
            files.append(name)
        idx = "".join(f"<sitemap><loc>{abs_url('/' + f)}</loc></sitemap>\n" for f in files)
        (d / "sitemap.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="{SITEMAP_NS}">\n{idx}</sitemapindex>\n', "utf-8")
        status, pst = ops.count_status(site["id"]), ops.count_posts(site["id"])
        st = dbq.settings(site["id"])
        plan_ = json.loads(st.get("publish_plan") or "[]")
        per_post = int(st.get("posts_per_day") or 0)
        left, pleft, day, sched = status.get("approved", 0) - pst.get("approved", 0), pst.get("approved", 0), date.today(), []
        while (left > 0 and plan_ or pleft > 0 and per_post) and len(sched) < 3650:  # 남은 공개 일정(대시보드 '앞으로 열릴 수')
            day += timedelta(days=1)
            step = next((p for p in plan_ if not p.get("until") or day.isoformat() <= p["until"]), plan_[-1]) if plan_ else {"per_day": 0}
            n, m = min(int(step["per_day"]), left), min(per_post, pleft)
            sched.append({"date": day.isoformat(), "pages": n, "posts": m})
            left, pleft = left - n, pleft - m
        (d / ".ops.json").write_text(json.dumps({"total_pages": sum(status.values()), "published": status.get("published", 0),
                                                 "posts_total": sum(pst.values()), "posts_published": pst.get("published", 0),
                                                 "posts_per_day": per_post, "schedule": sched[:60]}, ensure_ascii=False), "utf-8")
        click.echo(f"[{site['host']}] 공개 {len(rows)} → {d} ({len(files)}개 파일)")


def post_json(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def indexnow_send(site, post=post_json):
    """큐의 URL을 IndexNow 로 전송하고 sent_at·response_code 를 기록한다. (보낸 수, 응답코드)"""
    host = urlsplit(site_base()).netloc
    if not site["indexnow_key"]:
        raise ValueError("sites.indexnow_key 가 비어 있습니다")
    if host.split(":")[0].endswith(("localhost", "127.0.0.1")):
        raise ValueError("로컬 주소는 IndexNow 로 보내지 않습니다(BASE_URL 확인)")
    rows = ops.indexnow_pending(site["id"], int(dbq.settings(site["id"]).get("indexnow_daily_cap", 10000)))
    if not rows:
        return 0, None
    code = post("https://api.indexnow.org/indexnow", {
        "host": host, "key": site["indexnow_key"], "keyLocation": abs_url(f"/{site['indexnow_key']}.txt"),
        "urlList": sorted({r["url"] for r in rows})})
    if code in (200, 202):  # 실패(403 키 확인 전 등)는 큐에 남겨 다음 실행 때 다시 보낸다
        ops.indexnow_mark([r["id"] for r in rows], code)
    return len(rows), code


@jobs_cli.command("indexnow")
@SITE_OPT
def indexnow(only):
    """IndexNow 큐 전송(네이버·빙). 하루 한도(indexnow_daily_cap)를 넘는 몫은 다음 날 자동으로."""
    for site in _sites(only):
        try:
            click.echo(f"[{site['host']}] 전송 %s건, 응답 %s" % indexnow_send(site))
        except ValueError as e:
            click.echo(f"[{site['host']}] 건너뜀: {e}")


@jobs_cli.command("anchors")
@SITE_OPT
def anchors(only):
    """공개 콤보의 앵커 텍스트 변형(3개) 생성."""
    for site in _sites(only):
        n = 0
        for c in dbq.published(site["id"]):
            kw = c["primary_keyword"]
            ops.replace_anchors(c["id"], [kw, f"{kw} 고르는 기준", f"{kw} 정리"])
            n += 1
        dbq.bump_cache_version(site["id"])
        click.echo(f"[{site['host']}] {n}개 콤보")


# ── 측정 ──────────────────────────────────────────────────────────────────
def parse_log(lines):
    """nginx combined 로그 → {(date, path, bot, status): hits}. 검색엔진 봇 요청만."""
    hits = defaultdict(int)
    for line in lines:
        m = LOG_RE.match(line)
        bot = m and next((name for ua, name in BOTS.items() if ua in m.group(4)), None)
        if bot:
            day = datetime.strptime(m.group(1), "%d/%b/%Y").date().isoformat()
            hits[(day, unquote(m.group(2).split("?")[0])[:255], bot, int(m.group(3)))] += 1
    return hits


@jobs_cli.command("logs")
@click.argument("path", type=click.Path(exists=True))
def logs(path):
    """nginx access log 파싱 → bot_crawl_daily."""
    with open(path, encoding="utf-8", errors="replace") as f:
        hits = parse_log(f)
    ops.add_bot_hits(hits)
    click.echo(f"{len(hits)}행")


# ── 사실 카드(F4) ─────────────────────────────────────────────────────────
@jobs_cli.command("cards-input")
@click.option("--missing", is_flag=True, help="카드가 아직 없는 종류만")
def cards_input(missing):
    """활성 종류마다 세션 작성용 입력 content/cards/_input/<slug>.json 을 쓴다(작성 규칙: prompts/card.md)."""
    import cards
    d = cards.CARD_DIR / "_input"
    d.mkdir(parents=True, exist_ok=True)
    n = 0
    for v in db.q("SELECT * FROM verticals WHERE is_active=1 ORDER BY site_id, id"):
        k = cards.key(v)
        if missing and (v["card"] or (cards.CARD_DIR / f"{k}.json").exists()):
            continue
        (d / f"{k}.json").parent.mkdir(parents=True, exist_ok=True)
        (d / f"{k}.json").write_text(json.dumps(cards.build_input(v), ensure_ascii=False, indent=1), "utf-8")
        n += 1
    click.echo(f"{n}개 → {d}")


@jobs_cli.command("cards-load")
@click.option("--check", is_flag=True, help="점검만(적재하지 않음)")
@click.option("--only", default=None, help="이 slug 들만(쉼표로)")
def cards_load(check, only):
    """content/cards/<slug>.json 을 점검하고 통과한 카드를 verticals.card 에 넣는다."""
    import cards
    ok = bad = 0
    for v in db.q("SELECT * FROM verticals WHERE is_active=1 ORDER BY site_id, id"):
        k = cards.key(v)
        path = cards.CARD_DIR / f"{k}.json"
        if not path.exists() or (only and k not in only.split(",")):
            continue
        try:
            card = cards.with_search(k, json.loads(path.read_text("utf-8-sig")))
        except ValueError as e:
            click.echo(f"✗ {v['slug']}: JSON 오류 {e}")
            bad += 1
            continue
        errs = cards.validate(card, cards.build_input(v))
        if errs:
            click.echo(f"✗ {v['slug']}: " + " | ".join(errs[:8]))
            bad += 1
            continue
        if not check:
            cards.load(v, card)
        ok += 1
    click.echo(f"통과 {ok} · 미통과 {bad}" + ("" if check else " (통과분 적재)"))


@jobs_cli.command("keywords")
def keywords_cmd():
    """승인된 카드의 수식어로 페이지(콤보) 생성(F3). 멱등. 상품군별 등급 수를 보고한다."""
    import keywords
    tot = {}
    for label, c in keywords.rebuild().items():
        click.echo(f"[{label}] 새로 " + " · ".join(f"{g} {c[g]}" for g in "ABCD"))
    for r in db.q("SELECT s.host, k.grade, COUNT(*) n FROM keyword_combos k JOIN sites s ON s.id=k.site_id GROUP BY s.id, k.grade ORDER BY s.id, k.grade"):
        tot.setdefault(r["host"].split(".")[0], {})[r["grade"]] = r["n"]
    click.echo("전체: " + " / ".join(f"{k} " + ",".join(f"{g}{n}" for g, n in v.items()) + f"={sum(v.values())}" for k, v in tot.items()))


@jobs_cli.command("quality")
@SITE_OPT
def quality(only):
    """전 페이지 다시 조립·채점(카드·틀을 고친 뒤) 후, 공개 중인데 게이트에 걸리는 페이지를 review 로 내린다."""
    for site in _sites(only):
        click.echo(f"[{site['host']}] {pipeline.assemble_pages(site, ('draft', 'review', 'approved', 'published'))}")
        for url, fails in pipeline.gate_audit(site).items():
            click.echo(f"  공개 중단: {url} {fails}")


@jobs_cli.command("refresh")
def refresh():
    """카드 변경 반영 한 번에(서버: git pull 뒤): cards-load → keywords → assemble. 새 페이지는 대기열 뒤에 붙는다."""
    ctx = click.get_current_context()
    for cmd in (cards_load, keywords_cmd, assemble_cmd):
        ctx.invoke(cmd)


@jobs_cli.command("assemble")
@SITE_OPT
@click.option("--all", "redo", is_flag=True, help="승인·공개된 페이지도 다시 조립·채점(카드를 고친 뒤)")
def assemble_cmd(only, redo):
    """조립 페이지 채점: 린트·형제 고유성 통과분 자동 승인, 나머지 review."""
    for site in _sites(only):
        st = ("draft", "review", "approved", "published") if redo else ("draft", "review")
        click.echo(f"[{site['host']}] {pipeline.assemble_pages(site, st)}")
