"""시드: 스키마 + 상품군 사이트 7개 + 키워드 차원 + 상품 CSV 적재(종류 행은 적재가 사전에서 만든다).

    python seed.py --reset [CSV=data/products_2026-11-19.csv]    # shopa_prj DB를 지우고 처음부터 (개발용)

운영사 정보는 임시값이다(실제 값이 정해지면 관리자 > 사이트 설정에서 교체).
"""
import json
import secrets
import sys
from pathlib import Path

from pymysql.constants import CLIENT

import db
import products
from app import create_app

ROOT = Path(__file__).parent
DOMAIN = "golrabom.kr"
SETTINGS = {"op_name": "미정(임시)", "op_ceo": "미정(임시)", "op_bizno": "미정(임시)", "op_address": "미정(임시)", "op_email": "미정(임시)",
            "indexnow_daily_cap": "10000"}
DIMENSIONS = [("space", "공간"), ("persona", "대상"), ("situation", "상황"), ("feature", "특징"), ("price", "가격대"),
              ("intent", "의도"), ("question", "질문"), ("brand", "브랜드")]


def main():
    app = create_app()
    cfg = app.config
    raw = db.connect(cfg, database=False, client_flag=CLIENT.MULTI_STATEMENTS)
    with raw.cursor() as cur:
        if "--reset" in sys.argv:
            cur.execute(f"DROP DATABASE IF EXISTS `{cfg['DB_NAME']}`")
        cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name='sites'", (cfg["DB_NAME"],))
        if cur.fetchone():
            sys.exit("이미 시드된 DB입니다. 다시 만들려면 --reset 을 붙이세요.")
        schema = (ROOT / "db" / "schema.sql").read_text("utf-8").replace("shopa_prj", cfg["DB_NAME"])
        cur.execute(schema)
        while cur.nextset():
            pass
    raw.close()

    with app.app_context():
        for label, (sid, group) in products.GROUPS.items():
            db.ex("INSERT INTO sites (id, host, name, group_name, indexnow_key) VALUES (%s,%s,%s,%s,%s)",
                  (sid, f"{label}.{DOMAIN}", "골라봄", group, secrets.token_hex(16)))
            for k, v in SETTINGS.items():
                db.ex("INSERT INTO site_settings (site_id, `key`, `value`) VALUES (%s,%s,%s)", (sid, k, v))
        for i, (slug, name) in enumerate(DIMENSIONS, 1):
            db.ex("INSERT INTO keyword_dimensions (id, slug, name, rank_order) VALUES (%s,%s,%s,%s)", (i, slug, name, i))
        db.ex("INSERT INTO authors (slug, name, role, bio) VALUES (%s,%s,%s,%s)",
              ("편집팀", "골라봄 편집팀", "가전 종류별 고르는 기준 정리",
               "상품명·제조사 공개 자료로 종류별 고르는 기준과 방식 차이를 정리합니다. 상품을 직접 써 보고 쓰는 후기 글은 싣지 않습니다."))
        csv_path = next((a for a in sys.argv[1:] if a.endswith(".csv")), str(ROOT / "data" / "products_2026-11-19.csv"))
        r = products.import_csv(Path(csv_path).read_text("utf-8-sig"), Path(csv_path).name)
        print(json.dumps({k: v for k, v in r.items() if k != "errors"}, ensure_ascii=False), f"오류 {len(r['errors'])}")
        print("종류", db.q1("SELECT COUNT(*) n FROM verticals")["n"], "· 상품군별 상품",
              {x["host"].split(".")[0]: x["n"] for x in db.q("""SELECT s.host, COUNT(o.id) n FROM sites s LEFT JOIN verticals v ON v.site_id=s.id
                                                                 LEFT JOIN offers o ON o.vertical_id=v.id AND o.status='active' GROUP BY s.id""")})


if __name__ == "__main__":
    main()
