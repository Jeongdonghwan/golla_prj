"""공개 페이지(web), 클릭 리다이렉터(go), robots/sitemap/rss(seo)."""
import hashlib
import re
import uuid
from datetime import date
from math import ceil
from urllib.parse import quote

from flask import Blueprint, Response, abort, current_app, g, redirect, render_template, request

from db import js, ops, queries as dbq
from engine import abs_url, render_combo, shown_offers, site_base

web_bp = Blueprint("web", __name__)
go_bp = Blueprint("go", __name__)
seo_bp = Blueprint("seo", __name__)

STATIC_PAGES = {  # url → (템플릿, 제목)
    "/about/": ("about", "사이트 소개"),
    "/운영사/": ("operator", "운영사 정보"),
    "/광고고지/": ("ad-disclosure", "광고 고지"),
    "/편집기준/": ("editorial", "편집 기준"),
    "/개인정보처리방침/": ("privacy", "개인정보처리방침"),
    "/이용약관/": ("terms", "이용약관"),
    "/면책조항/": ("disclaimer", "면책조항"),
}
STATIC_DESC = {  # 고정 페이지 설명(50~160자). {name}=사이트명
    "/about/": "{name}이(가) 어떤 사이트인지, {goods} 종류별 고르는 기준을 어떻게 쓰고 고치는지, 상품 링크를 어떤 기준으로 싣는지 정리한 소개 페이지입니다.",
    "/운영사/": "{name}을(를) 운영하는 사업자의 상호, 대표자, 사업자등록번호, 주소와 연락처를 안내합니다. 운영사는 상품을 직접 팔지 않습니다.",
    "/광고고지/": "{name}의 상품 링크는 {shop_link}이며 링크를 거쳐 구매하면 운영사가 수수료를 받을 수 있습니다. 광고 표시 방식을 안내합니다.",
    "/편집기준/": "{name}의 글이 어떤 자료를 근거로 쓰이고 언제 고쳐지는지, 고르는 기준과 상품 링크를 어떻게 구분하는지 정리한 편집 기준입니다.",
    "/개인정보처리방침/": "{name}은(는) 이름·연락처를 받지 않습니다. 접속 기록과 쿠키 등 수집되는 정보의 종류와 보관 기간을 안내합니다.",
    "/이용약관/": "{name} 이용에 관한 기본 조건입니다. 사이트의 정보는 일반 안내이며 가격·재고·배송·교환은 판매처에서 확인해야 합니다.",
    "/면책조항/": "{name}의 글은 종류별 일반 정보입니다. 상품의 가격·사양·판매 조건은 판매처 기준이며 구매와 사용 결과에 대한 책임 범위를 안내합니다.",
    "/사이트맵/": "{name}의 상품 종류별 안내, 고르는 기준 글, 질문 글로 가는 길을 한곳에 모았습니다. 원하는 종류를 골라 이동하세요.",
}
SITEMAP_TYPES = {"hub": ("group_hub", "kind_hub"), "kw": ("kind_mod", "versus", "search"), "qna": ("question",)}
SITEMAP_CHUNK = 5000
BOT_RE = re.compile(r"bot|crawl|spider|yeti|slurp|curl|wget|python-requests|headless|lighthouse", re.I)
B62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
LOCAL_HOST = re.compile(r"(^|\.)(localhost|127\.0\.0\.1)(:\d+)?$")


def site():
    """요청 호스트로 사이트를 고른다(서브도메인 = sites 1행). 기준 주소: BASE_URL 호스트면 BASE_URL, 그 외는 https://{host}.
    로컬 미리보기(heat.localhost:5000)에서는 요청 호스트를 그대로 써서 링크가 로컬에 머문다."""
    if "site" not in g:
        g.site = dbq.site(request.host)
        if g.site["host"] != current_app.config["BASE_URL"].split("://", 1)[-1]:
            g.base_url = f"{request.scheme}://{request.host}" if LOCAL_HOST.search(request.host) else f"https://{g.site['host']}"
    return g.site


def is_bot():
    ua = request.headers.get("User-Agent", "")
    return not ua or bool(BOT_RE.search(ua))


def net(s):
    """사이트의 제휴 통로에 따른 낱말: 쿠팡 사이트(site_settings network=coupang) / 네이버 쇼핑 커넥트 사이트."""
    cp = s["id"] in dbq.coupang_ids()
    return {"cp": cp, "shop": "쿠팡" if cp else "네이버 쇼핑", "goods": "용품" if cp else "가전",
            "shop_link": "쿠팡 파트너스 제휴 링크" if cp else "네이버 쇼핑 제휴 링크",
            "disclose": "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다." if cp
            else "이 글에는 네이버 쇼핑 제휴 링크가 있으며, 구매 시 수수료를 받을 수 있습니다."}


@web_bp.app_context_processor
def inject():
    s = site()
    n = net(s)
    return {"site": s, "settings": dbq.settings(s["id"]), "base_url": site_base(), "og_default": dbq.site_og(s["id"]),
            "nav_sites": [x for x in dbq.sites() if (x["id"] in dbq.coupang_ids()) == n["cp"]], "static_pages": STATIC_PAGES, **n}


@web_bp.before_app_request
def normalize_url():
    """소문자·중복 하이픈/슬래시 정리·트레일링 슬래시 통일 후 301."""
    path = request.path
    if request.method not in ("GET", "HEAD") or path.startswith(("/go/", "/admin", "/static/")):
        return None
    new = re.sub(r"/{2,}", "/", re.sub(r"-{2,}", "-", path.lower()))
    if "." not in new.rsplit("/", 1)[-1] and not new.endswith("/"):
        new += "/"
    if new != path:
        qs = request.query_string.decode()
        return redirect(quote(new) + ("?" + qs if qs else ""), 301)
    return None


@web_bp.app_errorhandler(404)
def not_found(_e):
    return render_template("errors/404.html", title="페이지를 찾을 수 없습니다", robots="noindex,follow"), 404


@web_bp.app_errorhandler(500)
def server_error(_e):
    return "<h1>일시적인 오류가 발생했습니다</h1>", 500


@web_bp.get("/")
def home():
    s = site()
    hub = dbq.combo_by_url(s["id"], "/")  # 상품군 허브가 공개돼 있으면 메인이 곧 허브
    if hub and hub["status"] == "published":
        html = render_combo(s, hub)
        if html:
            return Response(html, mimetype="text/html")
    st = dbq.settings(s["id"])
    return render_template(
        "pages/home.html", title=st.get("home_title") or s["name"],
        description=st.get("home_desc") or f"{s['name']}: {net(s)['goods']} 종류별로 고르는 기준과 방식 차이, 관리법을 정리하고 {net(s)['shop']} 제휴 상품을 함께 싣습니다.",
        canonical=abs_url("/"), hubs=dbq.published(s["id"], ("group_hub",)), kinds=dbq.kind_hubs(s["id"]))


def _page(template, title, url, **ctx):
    s = site()
    return render_template(template, title=f"{title}{s['title_sep']}{s['name']}", h1=title, canonical=abs_url(url),
                           **{"description": STATIC_DESC.get(url, "{name} " + title + " 페이지입니다.").format(name=s["name"], **net(s)), **ctx})


def _special(s, url):
    """콤보가 아닌 공개 페이지: 정적, 작성자, HTML 사이트맵."""
    if url in STATIC_PAGES:
        tpl, title = STATIC_PAGES[url]
        return _page(f"static/{tpl}.html", title, url)
    m = re.fullmatch(r"/작성자/([^/]+)/", url)
    if m:
        author = ops.author(m.group(1)) or abort(404)
        return _page("pages/author.html", author["name"], url, author=author)
    if url == "/사이트맵/":
        return _page("pages/html_sitemap.html", "사이트맵", url, hubs=dbq.published(s["id"], SITEMAP_TYPES["hub"]),
                     topics=dbq.published(s["id"], ("kind_mod",), 300))
    return None


@web_bp.get("/<path:path>/")
def resolve_combo(path):
    s, url = site(), f"/{path}/"
    special = _special(s, url)
    if special:
        return special
    combo = dbq.combo_by_url(s["id"], url)
    if combo and combo["status"] in ("published", "noindex"):
        html = render_combo(s, combo)
        if html:
            if not is_bot():  # 상품 노출 집계(봇 제외)
                ops.add_impressions(s["id"], combo["id"], shown_offers(s, combo))
            return Response(html, mimetype="text/html")
    elif combo and combo["status"] in ("merged", "retired"):
        parent, hops = combo, 0
        while parent and parent["status"] != "published" and parent["parent_combo_id"] and hops < 8:  # 체인 없이 공개된 조상으로 바로
            parent, hops = dbq.combo(parent["parent_combo_id"]), hops + 1
        if parent and parent["id"] != combo["id"] and parent["status"] == "published":
            return redirect(quote(parent["url_path"]), 301)
    r = dbq.redirect_for(s["id"], url)
    if r:
        return redirect(quote(r["to_path"]), r["code"] or 301)
    abort(404)


# ── /go/ 클릭 리다이렉터 ──────────────────────────────────────────────────
def _base62(n):
    out = ""
    while n:
        n, r = divmod(n, 62)
        out = B62[r] + out
    return out.rjust(22, "0")


def _hash(value):
    # IP 해시는 일 단위 salt
    return hashlib.md5(f"{current_app.config['SECRET_KEY']}|{date.today()}|{value}".encode()).hexdigest()


@go_bp.get("/go/<int:offer_id>")
def redirect_click(offer_id):
    """제휴 링크로 302. 서버는 제휴 링크를 열지 않는다 — 방문자 브라우저만 간다.
    빠진(inactive) 상품은 같은 종류의 점수 높은 상품으로 보낸다(오래된 캐시·북마크 대비)."""
    s, a = site(), request.args
    offer = dbq.active_offer(offer_id)
    if not offer:
        old = dbq.offer(offer_id) or abort(404)
        alt = dbq.active_offers([old["vertical_id"]]) if old["vertical_id"] else []
        if not alt:
            abort(404)
        offer = max(alt, key=lambda o: float(o["score"]))
    combo_id = a.get("p", type=int)
    ua = request.headers.get("User-Agent", "")
    dbq.insert_click(click_id=_base62(uuid.uuid4().int), site_id=s["id"], offer_id=offer["id"],
                     combo_id=combo_id if combo_id and dbq.combo(combo_id) else None, slot=(a.get("s") or "")[:30] or None,
                     ua_hash=hashlib.md5(ua.encode()).hexdigest(), ip_hash=_hash(request.remote_addr),
                     referrer=(request.referrer or "")[:255] or None,
                     is_bot=int(is_bot()))  # ponytail: UA 규칙만. 빈도 기반 판정은 트래픽이 생기면 추가
    resp = redirect(offer["url"], 302)
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ── SEO ───────────────────────────────────────────────────────────────────
def _xml(template, **ctx):
    return Response(render_template(template, **ctx), mimetype="application/xml")


@seo_bp.get("/robots.txt")
def robots():
    lines = ["User-agent: *", "Allow: /", "Disallow: /go/", "Disallow: /admin/", "", f"Sitemap: {abs_url('/sitemap.xml')}", ""]
    return Response("\n".join(lines), mimetype="text/plain")


@seo_bp.get("/favicon.ico")
@seo_bp.get("/apple-touch-icon.png")
@seo_bp.get("/apple-touch-icon-precomposed.png")
def root_icons():  # 없으면 /<path>/ 라우트가 잡아 끝 슬래시 리다이렉트 → 404
    return redirect("/static/favicon.png", 301)


@seo_bp.get("/og/c/<int:cid>.webp")
def og_card(cid):
    """페이지별 OG 카드(og.py, cpa_prj c3 디자인). 공개된 그 사이트 페이지만. 처음 요청될 때 그린다."""
    from flask import send_file
    from og import card_file
    s, combo = site(), dbq.combo(cid)
    if not combo or combo["site_id"] != s["id"] or combo["status"] not in ("published", "noindex"):
        abort(404)
    v = dbq.vertical(combo["vertical_id"]) if combo["vertical_id"] else None
    f = card_file(combo, v["name"] if v else s["group_name"], f"{s['name']} {s['group_name']}", js(dbq.settings(s["id"]).get("theme"), {}))
    return send_file(f, mimetype="image/webp", max_age=30 * 86400)


@seo_bp.get("/llms.txt")
def llms():
    s = site()
    st = dbq.settings(s["id"])
    lines = [f"# {s['name']}", "", f"> {st.get('home_desc') or s['name']}", "",
             f"{net(s)['goods']} 종류별로 고르는 기준·방식 차이·관리법을 정리한 정보 사이트입니다. 상품 링크는 {net(s)['shop_link']}(광고)이며 가격·재고는 판매처에서 확인합니다.", "",
             "## 주요 안내"]
    lines += [f"- [{r['primary_keyword']}]({abs_url(r['url_path'])})" for r in dbq.published(s["id"], SITEMAP_TYPES["hub"], 60)]
    lines += ["", "## 사이트 정보", f"- [사이트 소개]({abs_url('/about/')})", f"- [광고 고지]({abs_url('/광고고지/')})",
              f"- [편집 기준]({abs_url('/편집기준/')})", f"- [사이트맵]({abs_url('/사이트맵/')})", ""]
    return Response("\n".join(lines), mimetype="text/plain")


@seo_bp.get("/<name>.txt")
def indexnow_key(name):
    key = site()["indexnow_key"]
    if not key or name != key:
        abort(404)
    return Response(key, mimetype="text/plain")


@seo_bp.get("/sitemap.xml")
def sitemap_index():
    """운영에서는 nginx 가 export/<라벨>/sitemap*.xml 을 직접 내준다(jobs export-sitemaps). 이건 로컬·파일 없을 때 대체."""
    s, files = site(), ["sitemap-static-1.xml"]
    for stype, page_types in SITEMAP_TYPES.items():
        files += [f"sitemap-{stype}-{i}.xml" for i in range(1, ceil(dbq.count_published(s["id"], page_types) / SITEMAP_CHUNK) + 1)]
    return _xml("xml/sitemap_index.xml", files=[abs_url("/" + f) for f in files])


@seo_bp.get("/sitemap-<stype>-<int:n>.xml")
def sitemap(stype, n):
    if stype == "static" and n == 1:
        return _xml("xml/urlset.xml", urls=[{"loc": abs_url(p), "lastmod": None} for p in ["/", *STATIC_PAGES, "/사이트맵/"]])
    if stype not in SITEMAP_TYPES or n < 1:
        abort(404)
    rows = dbq.published(site()["id"], SITEMAP_TYPES[stype], SITEMAP_CHUNK, (n - 1) * SITEMAP_CHUNK)
    if not rows:
        abort(404)
    return _xml("xml/urlset.xml", urls=[{"loc": abs_url(r["url_path"]), "lastmod": r["content_updated_at"]} for r in rows])


@seo_bp.get("/rss.xml")
def rss():
    items = [{**r, "link": abs_url(r["url_path"])} for r in dbq.latest_published(site()["id"])]
    return _xml("xml/rss.xml", items=items, feed_title=site()["name"])
