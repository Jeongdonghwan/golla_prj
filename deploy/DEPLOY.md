# 골라봄(shopa_prj) 배포 (211.45.175.195)

상품군 서브도메인 7개(heat·air·clean·kitchen·beauty·digital·life)를 Flask 앱 1개 + nginx 로 띄운다. cpa_prj 와 같은 서버, 포트·DB·서비스·cron 은 따로.

## 한 줄 설치·오픈

DNS(A 레코드 7개 → 211.45.175.195, 와일드카드 `*` 쓰지 않음)를 저장한 뒤 서버에서:

비공개 저장소(권장)는 토큰으로 먼저 받은 뒤 그 안의 스크립트를 돌린다(토큰: GitHub > Settings > Developer settings > Fine-grained token, 이 저장소 Contents 읽기만):

```bash
git clone https://<토큰>@github.com/<계정>/<저장소>.git /var/www/shopa_prj && THEN=<둘째 날부터 하루 공개 수> bash /var/www/shopa_prj/deploy/install.sh golrabom.kr 알림받을메일
```

공개 저장소라면 `THEN=<수> bash <(curl -fsSL https://raw.githubusercontent.com/<저장소>/main/deploy/install.sh) golrabom.kr 메일` 도 된다.

- 다시 실행해도 된다. DNS 가 아직이면 6단계에서 멈추고, 다시 돌리면 이어서 한다. 열린 페이지는 닫지 않는다.
- 첫 오픈(8단계): `stage` → `plan 오늘 --then $THEN` → `publish`. 첫날은 품질 게이트를 통과한 A·B·C 등급 + 허브 전부, 다음 날부터 하루 THEN 쪽(사용자가 정한 수).
- cron 05:40: publish → export-sitemaps → indexnow (cpa_prj 05:10, web_jd8 03:00 과 겹치지 않음).
- 9단계: 이 서버의 SEO 대시보드 수집기(`/var/lib/seo-dash/only`)에 golrabom 호스트 7개를 더한다. 수집기는 nginx 설정에서 각 서브도메인의 root(`/var/www/shopa_prj/export/<라벨>`)를 찾아 sitemap·`.ops.json`(전체 제작 수·남은 공개 일정)을 읽는다.

## 로컬(PC)에서 준비 — Claude 가 한다

- 덤프: `deploy/shopa_prj.sql.gz`(상품·종류·페이지·콘텐츠 전체). 서버에서 시드를 다시 돌리지 않는다.
- 코드 푸시: GitHub 비공개 저장소 main. install.sh 의 REPO 기본값을 그 주소로 바꾼다.

## 대시보드 수집기가 이 서버에 없을 때

```bash
cd /root && curl -fsSL https://raw.githubusercontent.com/Jeongdonghwan/j_dash/main/install-seo-dash-node.sh -o install-seo-dash-node.sh && DASH_PUSH_TOKEN=대시보드_푸시_토큰 DASH_ONLY="heat.golrabom.kr air.golrabom.kr clean.golrabom.kr kitchen.golrabom.kr beauty.golrabom.kr digital.golrabom.kr life.golrabom.kr" bash install-seo-dash-node.sh
```

cpa_prj 호스트가 이미 수집 중이면 `DASH_ONLY` 에 그 호스트도 함께 넣는다(값을 통째로 바꾸기 때문). install.sh 9단계는 기존 값에 더하므로 이 문제가 없다.

## 콘텐츠 확장(로컬 세션 → 서버 자동 반영)

- 새 종류·질문 글·문단 보강은 로컬 Claude Code 세션이 `content/cards/<종류>.json` 을 고치고(`jobs cards-input --missing` → prompts/card.md 규칙) `jobs cards-load --check` 통과 후 커밋·푸시한다.
- 서버 cron 05:30 이 `git pull` → `jobs refresh`(cards-load → keywords → assemble)로 새 페이지를 만들어 승인 대기열 뒤에 붙이고, 05:40 publish 가 하루 수만큼 연다.

## 매일 새 상품 (사용자 → 로컬 세션 → 서버 자동)

1. 사용자가 그날 새 상품 CSV(커넥트 형식, 새 것만)를 세션에 준다.
2. 로컬 세션: `data/inbox/<날짜>.csv` 로 저장 → `jobs products-inbox` → `jobs products-unclassified` → `data/classify.json` 작성(새 종류면 `content/cards/<종류>.json` 도) → `jobs products-classify` → `jobs refresh` 로 확인 → 커밋·푸시.
3. 서버 cron 05:30: `git pull` → `products-inbox`(추가만, 기존 상품 안 내림) → `refresh`(새 페이지 대기열) → 썸네일. 05:40 publish 가 하루 THEN 쪽 공개.

## 실제 검색 키워드 페이지 (search)

1. `python scripts/kw_research.py` — 네이버 검색광고 키워드도구로 종류별 연관 키워드·월 검색수 → `keywords/pool_<slug>.csv`(gitignore). 키는 로컬 `.env` 의 NAVER_AD_*.
2. `python scripts/search_input.py --top 60` — 상품 있는 말만, 이미 쓴 말·있는 주소 빼고 → `content/search/_input/<slug>.json`.
3. 세션이 `prompts/search.md` 대로 `content/search/<slug>.json` 에 3문장씩 덧붙임 → `jobs cards-load --check` → 커밋·푸시. 서버 05:30 refresh 가 페이지(월 1,000+ A, 그 밖 B)를 대기열에 붙인다.
- `docs/product_wishlist.md`: 상품이 없어 못 만든 말(월 검색수 순) — 이 상품을 CSV 로 주면 다음 조사 때 페이지가 된다.
- `keywords/post_topics.csv`: 문제·사용법 검색어 — 매일 글(posts) 주제.

## 하루 공개 기준
- 통로: ① 매일 글 40 ② 검색 키워드 페이지(세션 작성량) ③ 새 상품 목록 → 새 종류(종류당 약 30~160쪽). 모두 한 대기열, 매일 05:40 상한(THEN)만큼 우선순위 순 공개, 넘는 몫은 다음 날.

## 매주 상품 교체

관리자 `https://heat.golrabom.kr/admin/products` 에 커넥트 최신 CSV 업로드 → 추가·제외·변경 표 확인. 미분류가 있으면 로컬 세션에서
`jobs products-unclassified` → `data/classify.json` 작성 → 커밋·푸시 → 서버 `git pull && flask --app app jobs products-classify`.

## 업데이트

```bash
cd /var/www/shopa_prj && git pull && ./venv/bin/pip install -q -r requirements.txt && systemctl restart shopa_prj && systemctl is-active shopa_prj
```

## 문제가 생기면

```bash
journalctl -u shopa_prj -n 40 --no-pager
tail -40 /var/log/shopa_prj.log
nginx -t
```
