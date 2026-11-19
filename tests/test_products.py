"""상품 교체 점검(시드된 DB 대상): python tests/test_products.py
같은 CSV 재적재 → 변화 0 / 10개 빼고·수수료 1개 바꾸고·1개 추가 → 제외 10·변경 1·추가 1 / 원본 재적재로 복구(빠졌던 상품 다시 active).
"""
import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db  # noqa: E402
import products  # noqa: E402
from app import create_app  # noqa: E402

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "products_2026-11-19.csv")


def to_csv(rows):
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()


with create_app().app_context():
    text = open(SRC, encoding="utf-8-sig").read()
    rows = list(csv.reader(io.StringIO(text)))
    head, body = rows[0], rows[1:]
    before = db.q1("SELECT COUNT(*) n FROM offers WHERE status='active'")["n"]

    r = products.import_csv(text, "same.csv")
    assert (r["added"], r["removed"], r["changed"]) == (0, 0, 0), r

    gone_urls = [b[1] for b in body[:10]]
    changed = body[10][:]
    changed[0] = changed[0].replace("수수료 5%", "수수료 9%", 1) if "수수료 5%" in changed[0] else changed[0].replace("수수료 ", "수수료 1", 1)
    new = ["수수료 7%\n39,000\n원\n테스트 미니 가습기 USB 사무실 저소음\n테스트스토어\n4.5\n리뷰 12", "https://naver.me/TEST0001"]
    r = products.import_csv(to_csv([head, changed, *body[11:], new]), "edit.csv")
    assert (r["added"], r["removed"], r["changed"]) == (1, 10, 1), r
    t = db.q1("SELECT o.status, v.name, o.tags, o.fee FROM offers o JOIN verticals v ON v.id=o.vertical_id WHERE url=%s", ("https://naver.me/TEST0001",))
    assert t["name"] == "가습기" and "저소음" in t["tags"] and float(t["fee"]) == 7, t
    assert db.q1("SELECT COUNT(*) n FROM offers WHERE status='inactive' AND url IN (" + ",".join(["%s"] * 10) + ")", gone_urls)["n"] == 10

    r = products.import_csv(text, "restore.csv")  # 원본으로 복구: 빠졌던 10개 다시 active, 테스트 상품 빠짐
    assert (r["added"], r["removed"]) == (0, 1) and r["changed"] >= 11, r
    db.ex("DELETE FROM offers WHERE url='https://naver.me/TEST0001'")
    db.ex("DELETE FROM offer_imports WHERE filename IN ('same.csv','edit.csv','restore.csv')")
    assert db.q1("SELECT COUNT(*) n FROM offers WHERE status='active'")["n"] == before
    print("ok")
