# 카드 작성 작업 지시 (쿠팡 사이트 종류)

작업 폴더: c:\side_Prj\shopa_prj (Windows, Git Bash 사용 가능). 한국어 제휴 쇼핑 정보 사이트의 '사실 카드' JSON 을 받은 slug 마다 하나씩 쓴다.

순서:
1. 규칙 두 파일을 끝까지 읽는다: prompts/card.md (형식·금지 규칙·말투·개수), prompts/card_coupang.md (가전이 아닌 용품 종류에 맞게 바꿔 읽는 규칙 — 이쪽이 우선).
2. 형식 참고로 이미 통과한 카드 하나를 본다: content/cards/텀블러.json (앞 150줄 정도. 문장은 베끼지 않는다).
3. slug 마다 입력 content/cards/_input/<slug>.json 을 읽고(product_names·feature/price/brand 후보·space/persona/situation options), content/cards/<slug>.json 을 Write 도구로 새로 쓴다. "kind" 는 입력의 kind 그대로. 입력 파일이 없는 slug 는 쓰지 말고 보고만 한다.
4. slug 마다 점검을 돌려 통과할 때까지 고친다:
   cd /c/side_Prj/shopa_prj && PYTHONIOENCODING=utf-8 .venv/Scripts/flask --app app jobs cards-load --check --only <slug>

꼭 지킬 것:
- mods 의 space·persona·situation 은 입력 options 에 있는 말만 쓴다. **그 종류에 실제로 어울리는 것은 상한 없이 전부 넣는다**(축마다 보통 5~10개 — 페이지 수가 여기서 정해진다). 억지 조합만 뺀다. 종류 이름과 같은 말(예: '휴대용 유모차'의 '휴대용')은 뺀다.
  feature·price·brand 는 입력 후보에 있는 것만 전부(후보가 비면 빈 객체 {}).
- 문장 길이 하한을 지킨다(mods 문장 35자 이상, hub_intro 문단 150자 이상 등 card.md 의 범위).
- 숫자(가격·용량 수치·기간·퍼센트·월령)·지어낸 경험·과장·효능 단정 금지. 블로그 말투(~예요/~해요/~하세요). '가전'이라는 낱말과 판매처 이름(쿠팡 등), '공식·정품'은 쓰지 않는다.
- 식품·건강식품·화장품·의약외품·유아용품·반려동물 먹거리·보호대류: 건강·피부·통증·다이어트 효과를 말하지 않는다. 원재료·성분·인증·사이즈·보관 같은 '제품 표기 확인'과 형태 차이만. 의학적인 대목은 "전문가(수의사)와 상의". 안전을 보장하는 말 금지.
- 검색어 뜻이 여러 가지인 종류는 입력 product_names 에 보이는 '사는 물건' 쪽으로 쓴다.
- 다른 파일(코드·다른 카드·입력)은 고치지 않는다. git 명령도 쓰지 않는다. DB 에 적재(--check 없는 cards-load)하지 않는다.

끝나면 slug 별 '통과/실패(사유)'와 mods 축별 개수를 표 하나로만 짧게 보고한다.
