"""쿠팡 파트너스 Open API 시험 호출 — 어떤 정보가 오는지 본다(DB 는 건드리지 않는다).

    python scripts/coupang_test.py search 전기매트
    python scripts/coupang_test.py best 1015        # 카테고리 베스트(1015 가전디지털)
    python scripts/coupang_test.py goldbox

키: .env 의 COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY.
"""
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
ENV = dict(l.strip().split("=", 1) for l in (ROOT / ".env").read_text("utf-8").splitlines() if "=" in l and not l.startswith("#"))
BASE = "/v2/providers/affiliate_open_api/apis/openapi/v1"


def call(path, query=""):
    signed = time.strftime("%y%m%dT%H%M%SZ", time.gmtime())
    sig = hmac.new(ENV["COUPANG_SECRET_KEY"].encode(), f"{signed}GET{path}{query}".encode(), hashlib.sha256).hexdigest()
    auth = f"CEA algorithm=HmacSHA256, access-key={ENV['COUPANG_ACCESS_KEY']}, signed-date={signed}, signature={sig}"
    req = urllib.request.Request(f"https://api-gateway.coupang.com{path}{'?' + query if query else ''}", headers={"Authorization": auth, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"http": e.code, "body": e.read().decode("utf-8", "replace")[:500]}


if __name__ == "__main__":
    cmd, arg = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")
    if cmd == "search":
        out = call(f"{BASE}/products/search", f"keyword={quote(arg)}&limit=10")
    elif cmd == "best":
        out = call(f"{BASE}/products/bestcategories/{arg}", "limit=20")
    else:
        out = call(f"{BASE}/products/goldbox")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:6000])
