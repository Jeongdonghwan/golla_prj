# 골라봄 (shopa_prj) — 가전 제휴 SEO 사이트

기획서: **docs/plan.md** — 작업 전에 읽는다. 플로우 F0~F9 순서대로 진행하고, 다르게 해야 하면 이유를 말하고 사용자에게 묻는다.

# 규칙
- 수익: 네이버 쇼핑 커넥트(CPS). 링크는 `naver.me` 제휴 주소. **키워드·페이지는 '상품 종류'에 붙이고, 상품(링크)은 갈아 끼우는 부품**이다. 상품이 바뀌어도 페이지 본문·제목·주소는 바뀌지 않는다.
- 제휴 링크를 직접 열거나 긁지 않는다(클릭 집계·약관). 상품 정보는 CSV(`data/`)와 상품명에서만.
- 페이지에 가격·할인율·평점·리뷰 수를 숫자로 쓰지 않는다("가격·할인은 판매처에서 확인"). 상품 이미지 복제 금지. Product 스키마 금지.
- 고지: 제목 아래 "이 글에는 네이버 쇼핑 제휴 링크가 있으며, 구매 시 수수료를 받을 수 있습니다" + 상품 카드마다 '광고'. 링크는 `/go/` 경유 + `rel="sponsored nofollow noopener" target=_blank`.
- 금지 표현: 1위·최고·최저가·유일·품절 임박·100%·"직접 써 보니" 등 지어낸 후기·근거 없는 수치. 린트로 막는다.
- 브랜드명은 제목·본문에 사실 표기로 쓴다(대표 브랜드 나열은 상품 수 순·가나다순). "공식·제휴" 암시·로고·브랜드 비방·상품명에 없는 스펙 금지. 브랜드 수식어는 CSV 상품 3개 이상 브랜드만.
- 글은 Claude API 가 아니라 **Claude Code 세션**으로 쓴다(사용자 결정 2026-11-19). AI 로 쓰는 것: 종류 사실 카드·허브·질문 글. 롱테일 키워드 페이지는 사실 카드 조립(시드 고정 변형, 같은 문장 30% 이하).
- 품질 게이트(같은 문장 ≤30%, 첫 문장 틀 ≤50%, 린트, 상품 슬롯 ≥1, 내부 링크 ≥5) 미통과 페이지는 공개하지 않는다.
- 디자인: 흰 바탕 블로그형, 답이 위·상품 카드는 중간, 짙은 덩어리·세로 무늬·숫자 띠·"(참고 사진)" 캡션 금지, 광고 고지는 작게. 모바일 390px 확인.

# 정한 것
- 도메인: golrabom.kr (확정). 외부 사이트 게시 OK(사용자 확정). **첫날 준비된 페이지 전부 오픈 → 이후 매일 자동 공개 + 매주 확장 루틴**(docs/plan.md 6장). 공개 수는 사용자가 정한다 — 줄이자고 하지 않는다. 서브도메인 = 상품군: heat·air·clean·kitchen·beauty·digital·life.
- 서버: 211.45.175.195 (cpa_prj 와 같은 서버). 배포는 설치 스크립트 한 줄(`deploy/install.sh`, cpa_prj 것을 복사·수정). cron 05:40.
- 코드 기반: `C:\side_Prj\cpa_prj` 를 복사해 지역·리플알바·법률 부분을 걷어낸다(F1). 스택 Flask 3 + Jinja SSR + MariaDB(PyMySQL), 로컬 MariaDB 3307 재사용, Python 3.12 venv(서버 3.10 호환 유지 — f-string 안 같은 따옴표·역슬래시 금지).
- 원본 상품: `data/products_2026-11-19.csv` (1,717개).

# 미결 (사용자)
- 도메인 등록·DNS(heat·air·clean·kitchen·beauty·digital·life A 레코드), GitHub 저장소 주소(비공개 권장)
- D0 이후 하루 공개 수(D 등급 키워드·질문 글)

# 코드 구조 (F1~F6 완료 2026-11-19, 배포 전)
- app.py(팩토리) · web.py(공개·/go/·SEO, 메인 = 상품군 허브) · admin.py(상품 CSV 교체·검수·클릭 집계·CRUD) · engine.py(상품 선택·렌더·게이트·린트)
  · products.py(CSV 파서·종류 사전 KINDS·하위 종류 SUBKINDS·태그·가격대·브랜드·교체 적재) · cards.py(사실 카드 입력·점검·적재)
  · keywords.py(카드 → 페이지 조합·등급·우선순위) · composer.py(카드 → 페이지 조립, 시드 고정) · pipeline.py(조립 채점·자동 승인·공개) · jobs.py(배치 CLI) · seed.py.
- sites = 상품군 7, verticals = 상품 종류(parent_id = 하위 종류, card = 사실 카드 JSON), offers = 상품(url 고유키, 숫자는 순위용·페이지에 안 씀),
  keyword_combos = 페이지(mod1~3 = 수식어, grade A~D), page_contents = 조립 페이지의 제목·점수·변형 번호({"v": k})만 — 본문은 렌더 때 카드에서 다시 조립.
- 글의 원본은 `content/cards/<종류>.json`(git). 쓰는 규칙 prompts/card.md. 질문 글도 카드 posts 안에 있다. 카드에 문단이 있는 수식어만 페이지가 된다.
- 흐름: products-import → cards-input → (세션이 카드 작성) → cards-load → keywords → assemble → plan/publish. 서버는 cron 05:30 git pull → jobs refresh.
- 고유성: 형제(같은 부모·같은 유형) 최대 5-gram Jaccard ≤ 0.30. assemble 이 페이지마다 조립 변형 12개 중 앞 형제와 가장 덜 겹치는 것을 고른다(공개된 페이지는 변형 고정).
- 분류: data/classify.json(세션 판단, 재적재해도 유지) → KINDS 사전(SKIP_RE = 제외) → 미분류. 관리자가 고친 분류(classified_by=admin)는 재적재에도 유지. 상품 3개 미만 종류는 자동 비활성.
- 점검: `python products.py` · `python tests/test_products.py` · `python tests/test_pages.py` · `python scripts/snapshot_sites.py <폴더>` → site-launch check_all.py(폴더 7개).
- 대시보드: export-sitemaps 가 export/<라벨>/ 에 sitemap·.ops.json → nginx root → 서버 seo-dash 수집기(install.sh 9단계가 수집 대상에 7개 호스트 추가).

# 현황 (2026-11-19)
- 종류 82개 활성(모두 카드 있음. 모니터·에어컨은 상품이 조명·바람막이뿐이라 비활성), 상품 1,717 중 제외 40여 개.
- 페이지 13,100: A 573 · B 1,435(질문 글 164 포함) · C 5,441 · D 5,651. 첫날(A·B·C·허브·질문 글) 약 7,450쪽, D 5,651쪽은 둘째 날부터 하루 THEN 쪽.
- 출시 점검(사이트별 표본 60쪽): SEO 구조·중복 문서(같은 문장 평균 10~20%, 본문 평균 1,330~1,510자)·AI 티·답 페이지 틀(첫 문장 반복 3~8%) 7개 사이트 모두 통과. 형제 유사도 초과로 보류 2쪽.
- 기획(약 200종·종류당 1,000~3,000쪽) 대비 작다: CSV 상품으로 3개 이상 되는 종류가 82개뿐이고, 카드에 어울리는 수식어만 페이지로 만들었기 때문. 확장은 새 CSV 종류·수식어 추가·카드 보강으로.

# h1 수식어·레이 구조 적용(2026-11-24)
- 키워드 페이지(kind_mod·search·versus 14,315쪽): h1 = `<span>키워드</span> <span class="hero-sub">수식어</span>`(카드의 기준·방식 이름 4벌 중 시드 고정, 키워드와 겹치는 낱말 제외) · 분류 줄 · 설명 한 문단 · "{kw} 핵심 요약"(결론 3문장 + dl 4) · 상품 · 번호 절 최대 6개(한 문단씩, 4개에 키워드) · "{kw} 자주 묻는 질문"(4개) · 관련 링크 3묶음·15개 이하. composer.py `_ray`. 카드 문장은 다시 쓰지 않고 같은 조각을 문단으로 묶었다. title·설명·주소는 그대로(저장된 h1 문구는 title 용으로 유지).
- 측정 `python scripts/ray_check.py`(읽기 전용): h1 중복 0 · 조사 오류 0 · 같은 종류끼리 같은 문장 쌍 평균 24.9%(이전 구조 31.8%), 종류별 평균 최대 29.0%(캠핑 가전) · 번호 절 문단 평균 139자(카드 문장이 짧아 230~250자에 못 미침 — 늘리려면 카드 보강) · 본문 평균 1,238자.
- description(§5-3, 2026-11-24): 키워드 페이지는 `composer.desc` 로 45~80자 — "{kw} — {비교 기준 1~3}기준으로 고르는 법. {방식} 차이와 추천 상품 비교." 측정 47~80자·중복 0. h2 는 요약·번호 절 4개·FAQ 에 키워드 전체(페이지당 최소 5, 평균 7.0). title·주소는 그대로. 저장된 page_contents.meta_description 은 다음 assemble 때 새 값으로 바뀐다(화면은 렌더 때 계산).
- title(2026-11-24): 키워드 페이지 title = `{키워드} {h1 수식어} | 골라봄` — 앞부분이 h1 글자와 같다(렌더 때 조합, DB 저장값은 다음 assemble 때 바뀜). 키워드+수식어 40자·전체 60자 안에 드는 수식어 벌만 고르고, 안 되면 기준 하나짜리 짧은 벌, 그래도 넘치면 수식어 없음(80쪽 — 키워드가 길다). twitter:title 추가. 측정(ray_check): title 중복 0 · 길이 23~46 · 규칙 어긴 쪽 0. 허브·질문 글·메인 title 은 그대로.
- title 40자(2026-11-24 보정): `{키워드} {h1 수식어} | 골라봄` 이 40자를 넘으면 상호를 뺀다(og:site_name 에는 상호 유지). 측정: 중복 0 · 길이 23~40 · 상호 붙은 쪽 8,400 · 뺀 쪽 5,915.
- 카드 보강(2026-11-24): `content/cards_more/<slug>.json` 89종(기준·방식·관리·실수·안전 문장 뒤 덧붙임 + 축 문장, 4,051문장) — 카드 원본은 그대로, `cards.with_search` 가 `card["more"]` 로 합치고 `composer._ray` 가 절 문단에 붙인다. 측정(`ray_check.py --files`, 보강 종류 14,315쪽): 본문 평균 1,238 → 1,805자(최소 1,258, 공백 제외) · 번호 절 문단 평균 199자(10~90% 157~263) · 같은 종류 같은 문장 쌍 평균 26.7%(종류별 최대 33.6% 캠핑 가전, 5종 30% 초과) · h1·title·description 중복 0 · 조사 오류 0. 서버는 `jobs cards-load` 뒤 재시작. 쿠팡 74종은 보강 전.

# 쿠팡 사이트 5개 추가(2026-11-24, 사용자 결정)
- 쿠팡 파트너스는 **별도 사이트**로: salim(살림)·baby(육아)·style(뷰티·패션)·play(취미·레저)·office(디지털·오피스), sites id 8~12, `site_settings.network=coupang`. 각 사이트가 여러 분야를 다루는 종합 용품 사이트(네이버 커넥트 7개와 성과 비교). 기존 7개 사이트 종류와 겹치는 종류는 만들지 않는다.
- 상품은 CSV 가 아니라 API 자동 수집(coupang.py, `jobs coupang-sync|collect|export|import`). offers.ext_id = `cp:<상품번호>`, url = 딥링크 짧은 주소(subId = 사이트 라벨), advertiser = '쿠팡'. CSV 교체(import_csv)는 쿠팡 상품을 건드리지 않는다.
- 고지 문구는 쿠팡 지정 문장("이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.") — 위·아래에만. 본문에 '가전'·판매처 이름을 쓰지 않는다. 식품·건강식품·화장품·유아용품은 효능 단정 금지(prompts/card_coupang.md).
- 종류 547개(data/coupang_kinds.json) 중 1차 74개 카드 작성. 나머지는 검색수 순(keywords/coupang_kind_volume.csv)으로 이어 쓴다.
- 보강 2차(2026-11-24): 쿠팡 173종 `cards_more` + 전 종류 FAQ 답 보강(`cards_more.faqs`, composer.compose 끝에서 답 뒤에 붙임) + 같은 문장 30% 초과 9종 카드 항목 덧붙임. 측정(ray_check --files, 12개 사이트 18,464쪽): 본문 평균 1,982자(사이트별 1,915~2,030) · 1,800자 미만 6.2% · 같은 문장 쌍 평균 27.9% · 30% 초과 0종. 새 카드는 카드 + 보강(criteria·methods·care·mistakes·checks·axes·faqs)을 같이 쓴다(prompts/card_agent.md · cards_more.md · cards_more_faq.md). 푸시 전 `scripts/py310_check.py`(서버 3.10 문법) 필수.
