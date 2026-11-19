"""관리자: 범용 CRUD + 상품 CSV 교체 + 검수 + 클릭 집계. 로그인 + IP 제한 + audit_log."""
import hmac
import json
import secrets

from flask import Blueprint, abort, current_app, redirect, render_template, request, session, url_for

import pipeline
import products
from db import js, ops, q, queries as dbq
from engine import gate, lint_text

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

# 편집 가능한 컬럼 화이트리스트. json = JSON 검증 대상, lint = 금지 표현 검사 대상
TABLES = {
    "offers": {
        "label": "상품", "list": ["vertical_id", "name", "advertiser", "brand", "fee", "status", "classified_by"],
        "cols": ["vertical_id", "url", "name", "advertiser", "brand", "tags", "manual_weight", "status"],
        "json": ["tags"], "lint": []},
    "verticals": {
        "label": "종류", "list": ["site_id", "parent_id", "name", "url_base", "is_active"],
        "cols": ["site_id", "parent_id", "slug", "name", "url_base", "match_tags", "card", "is_active"],
        "json": ["match_tags", "card"], "lint": ["card"]},
    "keyword_terms": {
        "label": "수식어", "list": ["vertical_id", "dimension_id", "term", "slug", "tag", "canonical_term_id"],
        "cols": ["vertical_id", "dimension_id", "term", "slug", "tag", "canonical_term_id", "is_active"], "json": [], "lint": ["term"]},
    "keyword_combos": {
        "label": "페이지", "list": ["page_type", "grade", "primary_keyword", "url_path", "status", "priority_score"],
        "cols": ["primary_keyword", "url_path", "parent_combo_id", "cannibal_group_id", "is_group_rep", "status"], "json": [], "lint": ["primary_keyword"]},
    "redirects": {"label": "리다이렉트", "list": ["site_id", "from_path", "to_path", "code"], "cols": ["site_id", "from_path", "to_path", "code"], "json": [], "lint": []},
    "authors": {"label": "작성자", "list": ["slug", "name", "role"], "cols": ["slug", "name", "role", "bio", "links"], "json": ["links"], "lint": ["bio"]},
}
PAGES = {"products": "상품 CSV 교체", "review": "콘텐츠 검수", "dashboard": "클릭 집계"}


@admin_bp.context_processor
def menu():
    return {"tables": TABLES, "pages": PAGES}


@admin_bp.before_request
def guard():
    cfg = current_app.config
    if cfg["ADMIN_IPS"] and request.remote_addr not in cfg["ADMIN_IPS"]:
        abort(403)
    if not cfg["ADMIN_PASSWORD"]:  # 비밀번호 미설정이면 관리자 전체 비활성
        abort(403)
    if request.endpoint != "admin.login" and not session.get("admin"):
        return redirect(url_for("admin.login"))
    if request.method == "POST" and request.endpoint != "admin.login":
        if not hmac.compare_digest(request.form.get("csrf", ""), session.get("csrf", "-")):
            abort(400)
    return None


@admin_bp.after_request
def no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


def _audit(action, target, diff=None):
    dbq.audit(session["admin"], action, target, json.dumps(diff or {}, ensure_ascii=False, default=str))


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    # ponytail: 로그인 시도 제한 없음. ADMIN_IPS 또는 nginx allow 로 막고, 외부 공개가 필요해지면 rate limit 추가
    error = None
    if request.method == "POST":
        cfg = current_app.config
        ok = hmac.compare_digest(request.form.get("user", "").encode(), cfg["ADMIN_USER"].encode()) & \
            hmac.compare_digest(request.form.get("password", "").encode(), cfg["ADMIN_PASSWORD"].encode())
        if ok:
            session.clear()
            session.update(admin=cfg["ADMIN_USER"], csrf=secrets.token_hex(16))
            return redirect(url_for("admin.index"))
        error = "아이디 또는 비밀번호가 맞지 않습니다."
    return render_template("admin/login.html", error=error)


@admin_bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


@admin_bp.get("/")
def index():
    return render_template("admin/list.html", table=None)


# ── 상품 CSV 교체(F2) ─────────────────────────────────────────────────────
@admin_bp.route("/products", methods=["GET", "POST"], endpoint="products")
def products_page():
    result = error = None
    if request.method == "POST":
        f = request.files.get("file")
        try:
            result = products.import_csv(f.read().decode("utf-8-sig") if f else "", f.filename if f else "")
            _audit("products_import", "offers", {k: v for k, v in result.items() if k != "errors"})
        except (ValueError, UnicodeDecodeError) as e:
            error = f"실패: {e}"
    return render_template("admin/products.html", result=result, error=error, unclassified=products.unclassified(),
                           history=q("SELECT * FROM offer_imports ORDER BY id DESC LIMIT 10"))



# ── 검수 ──────────────────────────────────────────────────────────────────
@admin_bp.route("/review", methods=["GET", "POST"])
def review():
    message = None
    if request.method == "POST":  # 승인분 공개 실행(게이트·공개 계획 적용)
        results = [r for s in dbq.sites() for r in pipeline.publish_ready(s)]
        _audit("publish_ready", "keyword_combos", {"n": len(results)})
        message = f"공개 {sum(1 for _u, f in results if not f)}건, 게이트 미통과 {sum(1 for _u, f in results if f)}건"
    return render_template("admin/review.html", rows=ops.review_queue(), message=message)


@admin_bp.route("/review/<int:content_id>", methods=["GET", "POST"])
def review_one(content_id):
    content = ops.content_by_id(content_id) or abort(404)
    combo = dbq.combo(content["combo_id"])
    site = q("SELECT * FROM sites WHERE id=%s", (combo["site_id"],))[0]
    if request.method == "POST":
        if request.form.get("action") == "approve":
            if not js(content["lint_result"], {}).get("ok"):
                abort(400)  # 린트 미통과 콘텐츠는 승인할 수 없다(고쳐서 다시 제출)
            pipeline.approve(site, content_id, session["admin"])
        else:
            pipeline.reject(content_id, session["admin"])
        _audit(f"content_{request.form.get('action')}", f"page_contents:{content_id}")
        return redirect(url_for("admin.review"))
    return render_template("admin/review_one.html", c=content, combo=combo, blocks=js(content["blocks"], []), faqs=js(content["faqs"], []),
                           lint=js(content["lint_result"], {}), flags=js(content["fact_flags"], []),
                           gate=gate(site, combo) if content["status"] == "approved" else None)


@admin_bp.get("/dashboard")
def dashboard():
    group = request.args.get("group", "offer")
    if group not in ("offer", "combo", "vertical"):
        abort(404)
    return render_template("admin/dashboard.html", group=group, rows=ops.dashboard(group))


# ── 범용 CRUD ─────────────────────────────────────────────────────────────
@admin_bp.get("/t/<table>/")
def rows(table):
    t = TABLES.get(table) or abort(404)
    return render_template("admin/list.html", table=table, t=t, rows=dbq.admin_list(table, t["list"], 3000))


@admin_bp.route("/t/<table>/new", methods=["GET", "POST"], defaults={"row_id": None})
@admin_bp.route("/t/<table>/<int:row_id>", methods=["GET", "POST"])
def edit(table, row_id):
    t = TABLES.get(table) or abort(404)
    row = dbq.admin_get(table, row_id) if row_id else {}
    if row is None:
        abort(404)
    errors = []
    if request.method == "POST":
        data = {c: (request.form.get(c, "").strip() or None) for c in t["cols"]}
        for c in t["json"]:
            try:
                data[c] and json.loads(data[c])
            except ValueError:
                errors.append(f"{c}: JSON 형식이 아닙니다.")
        for c in t["lint"]:
            if lint_text(data[c]):
                errors.append(f"{c}: 금지 표현({', '.join(lint_text(data[c]))})")
        if table == "offers" and not errors:
            if not (data["url"] or "").startswith("https://"):
                errors.append("url: https:// 로 시작하는 제휴 주소가 필요합니다.")
            if data["vertical_id"] and not dbq.vertical(data["vertical_id"]):
                errors.append("vertical_id: 없는 종류입니다.")
            if not row_id or str(row.get("vertical_id")) != str(data["vertical_id"]):
                data["classified_by"] = "admin"  # 손으로 고친 분류는 CSV 를 다시 올려도 유지
        if not errors:
            try:
                saved_id = dbq.admin_save(table, row_id, data)
            except Exception as e:  # 중복 url·slug 등 DB 제약 오류를 폼에 그대로 보여준다
                errors.append(f"저장 실패: {e}")
            else:
                _audit("update" if row_id else "create", f"{table}:{saved_id}", data)
                for s in dbq.sites():
                    dbq.bump_cache_version(s["id"])
                return redirect(url_for("admin.rows", table=table))
        row = {**row, **data}
    return render_template("admin/form.html", table=table, t=t, row=row, row_id=row_id, errors=errors)
