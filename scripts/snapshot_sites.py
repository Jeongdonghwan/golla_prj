"""SEO 점검용 표본 스냅샷: 실행 중인 로컬 서버에서 사이트별로 메인·robots·사이트맵·RSS·고지 페이지와 무작위 공개 페이지를 받아
<출력>/<라벨>/out/ 에 정적 사이트처럼 저장한다. 그 폴더에 site-launch 스킬의 check_all.py 를 그대로 돌린다.

    python scripts/snapshot_sites.py <출력 폴더> [표본수=60] [서버=http://127.0.0.1:5000]   (프로젝트 venv)
"""
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app  # noqa: E402
import db  # noqa: E402

OUT = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 60
BASE = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:5000"
EXTRA = ["/", "/robots.txt", "/rss.xml", "/llms.txt", "/about/", "/광고고지/", "/운영사/", "/사이트맵/"]


CLIENT = None


def fetch(host, path):
    """실행 중인 서버 대신 Flask 테스트 클라이언트로 받는다(서버를 띄울 필요 없음)."""
    with CLIENT.application.app_context():  # 요청마다 새 컨텍스트 — 바깥 컨텍스트의 g.site 가 다른 사이트로 새지 않게
        r = CLIENT.get(path, headers={"Host": host, "User-Agent": "Mozilla/5.0 snapshot"})
    if r.status_code != 200:
        raise RuntimeError(f"{host}{path} → {r.status_code}")
    return r.get_data()


def save(root, path, body):
    p = urllib.parse.unquote(path).strip("/")
    if path.endswith((".txt", ".xml")):
        f = os.path.join(root, p)
    else:
        f = os.path.join(root, p, "index.html") if p else os.path.join(root, "index.html")
    os.makedirs(os.path.dirname(f), exist_ok=True)
    open(f, "wb").write(body)


def main():
    global CLIENT
    app = create_app()
    CLIENT = app.test_client()
    with app.app_context():
        sites = db.q("SELECT id, host FROM sites ORDER BY id")
        for s in sites:
            label = s["host"].split(".")[0]
            host = f"{label}.localhost:5000"
            root = os.path.join(OUT, label, "out")
            rows = db.q("SELECT url_path FROM keyword_combos WHERE site_id=%s AND status='published' ORDER BY RAND() LIMIT %s", (s["id"], N))
            for path in EXTRA + [r["url_path"] for r in rows]:
                save(root, path, fetch(host, path))
            # 사이트맵: 운영에서는 export/<라벨>/ 의 파일을 nginx 가 내준다 → 그 파일을 쓰되, 주소는 표본에 있는 것만 남긴다
            locs = "".join(f"<url><loc>https://{s['host']}{urllib.parse.quote(p)}</loc></url>\n" for p in ["/"] + [r["url_path"] for r in rows])
            open(os.path.join(root, "sitemap.xml"), "w", encoding="utf-8").write(
                f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{locs}</urlset>\n')
            import json  # 중복 측정 표본 = 콘텐츠 페이지만(고정 안내 페이지 제외) — dup_measure --pages 형식
            os.makedirs(os.path.join(OUT, label, "data"), exist_ok=True)
            json.dump({"pages": [{"slug": urllib.parse.unquote(r["url_path"]).strip("/"), "kw": r["url_path"]} for r in rows if r["url_path"] != "/"]},
                      open(os.path.join(OUT, label, "data", "site-pages.json"), "w", encoding="utf-8"), ensure_ascii=False)
            import shutil  # ItemList·og 썸네일 파일이 있는지 점검기가 보도록 OG 카드를 같이 둔다
            og = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "og")
            if os.path.isdir(og):
                shutil.copytree(og, os.path.join(root, "static", "og"), dirs_exist_ok=True)
            print(label, len(rows) + len(EXTRA), "files", flush=True)


if __name__ == "__main__":
    main()
