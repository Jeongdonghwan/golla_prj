"""페이지 점검(시드·조립·공개된 DB 대상): python tests/test_pages.py
유형마다 공개 페이지 하나씩: 200 · 고지 문구 · 상품 카드 '광고'·/go/ rel · 가격·평점 숫자 없음 · 같은 페이지 두 번 렌더하면 같은 본문 · /go/ 302.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db  # noqa: E402
from app import create_app  # noqa: E402

app = create_app()
c = app.test_client()
with app.app_context():
    rows = db.q("""SELECT k.page_type, k.url_path, s.host FROM keyword_combos k JOIN sites s ON s.id=k.site_id
                   WHERE k.status='published' AND k.id IN (SELECT MIN(id) FROM keyword_combos WHERE status='published' GROUP BY page_type, grade)""")
assert {r["page_type"] for r in rows} >= {"group_hub", "kind_hub", "kind_mod", "question"}, rows
for r in rows:
    h = {"Host": r["host"].split(".")[0] + ".localhost:5000", "User-Agent": "Mozilla/5.0 test"}
    with app.app_context():
        a = c.get(r["url_path"], headers=h)
    with app.app_context():
        b = c.get(r["url_path"], headers=h)
    t = a.get_data(as_text=True)
    assert a.status_code == 200, (r, a.status_code)
    assert "이 글에는 네이버 쇼핑 제휴 링크가 있으며, 구매 시 수수료를 받을 수 있습니다" in t, r
    assert '<span class="ad">광고</span>' in t and 'rel="sponsored nofollow noopener" target="_blank"' in t, r
    body = re.sub(r"<[^>]+>", " ", t.split("<main>")[1].split("</main>")[0])
    assert not re.search(r"\d{1,3}(,\d{3})+\s*원|평점\s*\d|리뷰\s*[\d,]+", body), (r, re.findall(r".{20}\d{1,3}(?:,\d{3})+\s*원.{5}", body)[:2])
    strip = lambda x: re.sub(r'<ul class="offers">.*?</ul>', "", x, flags=re.S)  # noqa: E731
    assert strip(t) == strip(b.get_data(as_text=True)), ("조립이 렌더마다 달라짐", r)
    go = re.search(r'href="(/go/\d+)[^"]*"', t).group(1)
    with app.app_context():
        g = c.get(go, headers=h)
    assert g.status_code == 302 and g.headers["Location"].startswith("https://naver.me/"), (go, g.status_code)
    print("ok", r["page_type"], r["url_path"])
print("ok")
