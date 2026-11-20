"""상품 CSV(네이버 쇼핑 커넥트 내려받기) 적재·분류·교체. 페이지 본문은 건드리지 않는다 — 상품은 갈아 끼우는 부품.

CSV: 열 2개(상품명 셀, 제휴 URL). 상품명 셀은 여러 줄이고 형식이 두 가지(판매가·할인율·할인가가 있는 형 / 가격 한 줄형)에
앞머리 표시(NEW·기획전 이름·'수수료+')와 '추가 N%'(가산 수수료)가 섞인다. 끝에서부터 읽으면 모두 같다:
  … 가격, '원', 상품명, 스토어, 평점, '리뷰 N'
분류: ① data/classify.json(세션이 읽고 정한 것, URL → 종류) ② 종류어 사전(KINDS, 위에서부터 첫 일치) ③ 못 고르면 미분류(NULL).
"""
import csv
import io
import json
import math
import re
from collections import Counter
from pathlib import Path

from db import ex, q, q1

OG_IMAGE_RE = re.compile(r"""<meta[^>]+(?:property|name)=["'](?:og:image|twitter:image)["'][^>]*>""", re.I)


def og_image(html):
    """링크 미리보기용 썸네일 주소(og:image). 없으면 ''."""
    for tag in OG_IMAGE_RE.findall(html or ""):
        m = re.search(r"""content=["']([^"']+)""", tag, re.I)
        if m and m.group(1).startswith(("http://", "https://")):
            return m.group(1).replace("&amp;", "&")
    return ""


def fetch_thumb(url, timeout=10):
    """링크 미리보기처럼 한 번 열어 og:image 를 읽는다(상품마다 한 번, 결과는 offers.image 에 저장).
    naver.me 는 미리보기 봇 UA 일 때만 og:image 가 든 페이지를 준다(일반 UA 는 JS 빈 페이지)."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "facebookexternalhit/1.1", "Accept-Language": "ko"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return og_image(r.read(400_000).decode("utf-8", "replace"))

ROOT = Path(__file__).parent
OVERRIDES = ROOT / "data" / "classify.json"   # {url: {"kind": "종류" | "제외", "tags": [...]}} — 다시 올려도 유지된다
SKIP = "제외"                                  # 가전 사이트에 싣지 않을 상품(세션 판단)

GROUPS = {  # 라벨 → (사이트 id, 이름)
    "heat": (1, "난방"), "air": (2, "공기·가습"), "clean": (3, "청소·세탁"), "kitchen": (4, "주방"),
    "beauty": (5, "미용·건강"), "digital": (6, "음향·디지털"), "life": (7, "캠핑·생활"),
}

# (상품군, 종류, 포함 정규식[, 제외 정규식]) — 위에서부터 첫 일치. 소모품·구체적인 것을 먼저 둔다.
KINDS = [
    # 소모품(본체로 잘못 잡히지 않게 맨 앞)
    # 소모품(본체로 잘못 잡히지 않게 맨 앞). 본체 이름에도 '필터·브러시'가 흔해서 '호환·교체·먼지봉투' 같은 신호가 있어야 소모품
    ("air", "공기청정기 필터", r"(공기청정기|청정기|공청기).{0,40}필터|필터 모음전|고퓨어.*필터", r"필터 ?교체 ?없는|필터포함|본품|탁상용|\d+평|㎡"),
    ("clean", "청소기 소모품", r"먼지봉투|더스트백|(청소기|로보락|다이슨|코드제로|샤크|제트).*(호환|소모품|교체용|세정제|세제|세척액|회전솔|전용 필터|필터 \d+개입)"
                           r"|(호환|교체용).*(청소기|로보락|다이슨|코드제로|캐치웰|샤크|제트)|정품.*로보락.*(세제|세정제|클리너|소모품)"
                           r"|로보락 ?전용 ?(세제|클리너)|로보락세제", r"본품"),
    ("beauty", "칫솔모·구강 소모품", r"칫솔모|리필모|교체용 칫솔|브러시헤드|노즐 교체"),
    ("kitchen", "정수기 필터", r"정수기 ?필터|정수 ?필터|필터 ?카트리지|막스트라", r"리켈리|본품"),
    ("kitchen", "커피머신 소모품", r"포터필터|포타필터|퍽스크린|퍽필터|샤워스크린|가스켓"),
    # 난방
    ("heat", "전기매트", r"탄소매트|카본매트|온열매트|전기매트|전기 ?장판|전기요|그래핀 ?매트|온수매트|카본 매트|전기 ?담요|전기 ?이불|전기 ?매트|온찜매트|아랫목", r"방석|푸드|음식"),
    ("heat", "전기방석", r"방석"),
    ("heat", "발난로", r"발난로|발 ?히터|풋워머|발 ?온열"),
    ("heat", "오방난로", r"오방난로|오방 ?히터"),
    ("heat", "라디에이터", r"라디에이터|오일히터"),
    ("heat", "열풍기", r"열풍기|산업용 ?히터|업소용 ?히터|돈풍기"),
    ("heat", "온풍기", r"온풍기|PTC ?히터|PTC ?난방"),
    ("heat", "전기히터", r"히터|난로|난방기|스토브|하이라이트 ?(전기|난방)", r"인덕션|전기레인지|드라이"),
    # 공기
    ("air", "가습기", r"가습기|가습"),
    ("air", "제습기", r"제습기|제습"),
    ("air", "공기청정기", r"공기청정기|청정기"),
    ("air", "서큘레이터", r"서큘레이터"),
    ("air", "환풍기", r"환풍기|환기팬|욕실팬"),
    ("air", "선풍기", r"선풍기|냉풍기|실링팬"),
    ("air", "에어컨", r"에어컨"),
    # 청소·세탁
    ("clean", "로봇청소기", r"로봇청소기|로봇 ?청소"),
    ("clean", "물걸레청소기", r"물걸레 ?청소기|물걸레 ?로봇|습건식"),
    ("clean", "스팀청소기", r"스팀 ?청소기|스팀 ?걸레"),
    ("clean", "침구청소기", r"침구 ?청소기|진드기"),
    ("clean", "핸디청소기", r"핸디 ?청소기|차량용 ?청소기|미니 ?청소기|핸디형 청소기"),
    ("clean", "무선청소기", r"청소기"),
    ("clean", "스팀다리미", r"스팀 ?다리미|핸디 ?스티머|스티머"),
    ("clean", "다리미", r"다리미"),
    ("clean", "보풀제거기", r"보풀"),
    ("clean", "신발건조기", r"신발.{0,6}(건조|살균)|슈즈.{0,6}(건조|살균|쏙)"),
    ("clean", "세탁기 부품", r"(세탁기|건조기).*(홀더|클립|호스|커넥터|선반|스태킹|받침대|거치대)"),
    ("clean", "세탁기", r"세탁기|세탁 ?\d+kg|AI ?콤보|원바디"),
    ("clean", "의류건조기", r"의류 ?건조기|건조기", r"식기|손|드라이|식품|과일|음식물|고추|칫솔"),
    # 주방
    ("kitchen", "음식물처리기", r"음식물 ?처리기|음식물 ?분쇄|음식물"),
    ("kitchen", "김치냉장고", r"김치냉장고"),
    ("kitchen", "냉장고", r"냉장고|냉동고"),
    ("kitchen", "식기세척기", r"식기세척기|식세기"),
    ("kitchen", "식기건조기", r"식기 ?건조|식기 ?살균|젖병 ?소독|젖병 ?살균"),
    ("kitchen", "식품건조기", r"식품 ?건조|고추 ?건조|과일 ?건조"),
    ("kitchen", "분유포트", r"분유포트|분유 ?제조기|분유"),
    ("kitchen", "전기포트", r"전기포트|커피포트|전기주전자|무선포트|전기 ?주전자|티포트|포트|주전자"),
    ("kitchen", "커피머신", r"커피머신|에스프레소|캡슐 ?커피|커피 ?메이커|드립 ?머신"),
    ("kitchen", "커피 그라인더", r"그라인더|원두 ?분쇄", r"네일|손톱|발톱|후추|소금|페퍼|참깨"),
    ("kitchen", "에어프라이어", r"에어프라이|에어 ?프라이"),
    ("kitchen", "전자레인지", r"전자레인지|전자렌지|전자랜지"),
    ("kitchen", "오븐", r"오븐|토스터|토스트기"),
    ("kitchen", "인덕션", r"인덕션|하이라이트|전기레인지|핫플레이트|하이브리드 ?레인지"),
    ("kitchen", "가스레인지", r"가스 ?레인지|가스 ?쿡탑"),
    ("kitchen", "주방후드", r"후드"),
    ("kitchen", "전기밥솥", r"밥솥", r"패킹"),
    ("kitchen", "계란찜기", r"계란|에그 ?쿠커|달걀"),
    ("kitchen", "멀티쿠커", r"멀티 ?쿠커|전기냄비|전기 ?냄비|라면포트|찜기|전기 ?그릴|그릴|와플|샌드위치 ?메이커|고구마"),
    ("kitchen", "이유식 마스터", r"이유식"),
    ("kitchen", "믹서기", r"믹서|블렌더|다지기|초퍼|분쇄기|착즙|원액기|핸드블렌더"),
    ("kitchen", "정수기", r"정수기"),
    ("kitchen", "두유제조기", r"두유|죽 ?메이커"),
    ("kitchen", "요거트메이커", r"요거트|요구르트"),
    ("kitchen", "진공포장기", r"진공 ?포장기|진공 ?머신|진공기|밀봉기|압축기", r"포장지|비닐|진공팩"),
    ("kitchen", "우유거품기", r"거품기|밀크 ?포머|휘핑|밀크 ?프로더"),
    ("kitchen", "전동채칼", r"채칼|슬라이서|채써는"),
    ("kitchen", "수비드 머신", r"수비드"),
    ("kitchen", "제빙기", r"제빙기|아이스 ?메이커"),
    ("kitchen", "탄산수제조기", r"탄산수"),
    ("kitchen", "푸드워머", r"푸드 ?워머|보온 ?매트|음식 ?워머|온장고"),
    ("kitchen", "간식 메이커", r"붕어빵|호빵|솜사탕|팝콘"),
    # 미용·건강
    ("beauty", "피부 관리기", r"피부|고주파|LED ?마스크|갈바닉|프라엘|더마|리프팅|미백|톤케어|써마|뷰티 ?디바이스|클렌저|클렌징|세안기|필링기", r"칫솔|세정액"),
    ("beauty", "전기면도기", r"면도기|쉐이버|셰이버"),
    ("beauty", "코털제거기", r"코털"),
    ("beauty", "이발기", r"바리깡|이발기|트리머|클리퍼", r"제모|바디 ?트리머|솜털"),
    ("beauty", "제모기", r"제모기|제모|모근 ?제거|바디 ?트리머|솜털"),
    ("beauty", "전동칫솔", r"전동칫솔|음파칫솔|소닉케어|칫솔", r"살균기"),
    ("beauty", "칫솔살균기", r"칫솔 ?살균|칫솔살균"),
    ("beauty", "구강세정기", r"구강세정기|물치실|워터픽|치간"),
    ("beauty", "고데기", r"고데기|매직기|봉고데|에어랩|스타일러|컬링|웨이브|볼륨"),
    ("beauty", "드라이기", r"드라이기|드라이어|헤어 ?드라이"),
    ("beauty", "안마기", r"안마|마사지|맛사지|마사져|족욕|찜질|온열 ?벨트|EMS"),
    ("beauty", "체중계", r"체중계|체지방"),
    # 음향·디지털
    ("digital", "골전도 이어폰", r"골전도|오픈형 이어폰|귀찌|이어 ?커프|클립형"),
    ("digital", "무선이어폰", r"이어폰|이어버드|버즈|에어팟|이어팟"),
    ("digital", "헤드폰", r"헤드폰|헤드셋|WH-\d|MDR-"),
    ("digital", "사운드바", r"사운드바"),
    ("digital", "블루투스 스피커", r"스피커"),
    ("digital", "마이크", r"마이크"),
    ("digital", "TV 액세서리", r"(TV|티비|HDMI|[삼엔짭]?[탠텐]바이미|모니터|빔).{0,30}(분배기|선택기|셀렉터|동글|보호기|보호대|가드|안테나|스틱|셋[톱탑] ?박스|거치대|브라켓|받침대|리모컨)"
                            r"|(분배기|셀렉터|거치대|보호기|동글).{0,30}(TV|티비|HDMI|모니터)|HDMI ?(분배기|선택기|셀렉터)|화면 ?보호기"),
    ("digital", "TV", r"TV|티비|텔레비전|스탠바이미|QLED|OLED|UHD|사이니지", r"거치대|받침대|리모컨"),
    ("digital", "모니터", r"모니터", r"조명|램프|라이트바|스탠드"),
    ("digital", "빔프로젝터", r"프로젝터|빔"),
    ("digital", "충전기·보조배터리", r"보조배터리|충전기|고속충전|멀티탭|어댑터"),
    ("digital", "녹음기", r"녹음기|보이스 ?레코더|ICD-"),
    ("digital", "라디오·오디오", r"라디오|CD ?플레이어|블루투스 ?오디오"),
    ("digital", "스마트 글래스", r"스마트 ?안경|AI ?글래스"),
    # 캠핑·생활
    ("life", "조명·무드등", r"무드등|독서등|조명|스탠드 ?조명|LED ?스탠드|수면등|램프", r"포충|유인 ?램프|글루 ?페이퍼|모기|해충"),
    ("life", "운동기구", r"러닝머신|런닝머신|트레드밀|사이클|워킹패드"),
    ("life", "캠핑 가전", r"캠핑|차박|파워뱅크|파워스테이션"),
    ("life", "도어락", r"도어락|도어록|번호키"),
    ("life", "해충퇴치기", r"퇴치기|모기|포충|해충|유인 ?램프|글루 ?페이퍼"),
    ("life", "고압세척기", r"고압 ?세척기|고압 ?세차기"),
    ("life", "순간온수기", r"온수기"),
    ("life", "재봉틀", r"미싱|재봉틀"),
]

# (태그, 정규식) — 상품명에 있는 것만. 페이지 수식어(특징)와 이어진다.
TAGS = [
    ("무선", r"무선|코드리스|충전식"), ("유선", r"유선"), ("USB", r"USB|C ?타입|C타입"), ("휴대용", r"휴대용|핸디|포터블|미니 ?휴대"),
    ("미니", r"미니|초소형|소형|컴팩트"), ("대용량", r"대용량|대형|점보"), ("저소음", r"저소음|무소음|조용한|소음 ?적은"),
    ("BLDC", r"BLDC"), ("가열식", r"가열식"), ("초음파", r"초음파"), ("복합식", r"복합식|하이브리드 ?가습"), ("자연기화", r"기화식|자연기화"),
    ("1인용", r"1인용|(?<!슈퍼)싱글|1인"), ("2인용", r"2인용|더블|퀸|킹|2인"), ("슈퍼싱글", r"슈퍼싱글"),
    ("전자파 차단", r"전자파 ?없는|전자파 ?차단|EMF|저전자파"), ("업소용", r"업소용|산업용|공업용|매장용|미용실|세탁소"), ("가정용", r"가정용"),
    ("사무실", r"사무실|탁상|책상|데스크"), ("차량용", r"차량용|차량"), ("캠핑", r"캠핑|차박"), ("원룸", r"원룸|자취"),
    ("아기", r"아기|신생아|유아|베이비|아이방"), ("스탠드형", r"스탠드|타워"), ("벽걸이", r"벽걸이|벽부착|무타공"),
    ("타이머", r"타이머|예약"), ("리모컨", r"리모컨"), ("살균", r"살균|UV|자외선"), ("세척 쉬운", r"통세척|분리 ?세척|세척 ?쉬운|자동세척"),
    ("스텐", r"스텐|스테인리스|올스텐"), ("접이식", r"접이식|폴딩"), ("방수", r"방수|IPX"), ("스마트", r"\bAI\b|IoT|IOT|스마트|와이파이|앱 ?연동"),
    ("노이즈캔슬링", r"노이즈 ?캔슬링|ANC"), ("무드등", r"무드등|LED ?조명"), ("온도조절", r"온도 ?조절|온도조절"), ("1등급", r"1등급"),
    ("자동먼지비움", r"자동 ?먼지 ?비움|먼지 ?비움|스테이션"), ("물걸레", r"물걸레"), ("저전력", r"저전력|절전|초절전"), ("탄소", r"탄소|카본|그래핀"),
]
CAPACITY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(L|리터|ml|mL)\b")
_TAG_RES = [(t, re.compile(p, re.I)) for t, p in TAGS]
_KIND_RES = [(g, k, re.compile(p, re.I), re.compile(x, re.I) if x else None) for g, k, p, *rest in KINDS for x in [rest[0] if rest else None]]
BRAND_ALIAS = {"브라운네보": "브라운", "곰표한일": "한일", "한일온열기": "한일", "삼성전자": "삼성", "삼성공식파트너": "삼성", "엘지": "LG", "LG전자": "LG", "사운드코어": "앤커", "Anker": "앤커", "필립스코리아": "필립스", "한일전기": "한일",
               "한일의료기": "한일의료기", "샤크닌자": "닌자", "SHARK": "샤크", "JBL": "JBL", "Apple": "애플"}
BRAND_NOISE = re.compile(r"^(공식|정품|단독|신제품|NEW|new|국산|당일발송|무료배송|N배송|독일|최신형|\d+년형?|20\d\d|\+|/)$")


def slugify(name):
    return re.sub(r"-{2,}", "-", re.sub(r"[^0-9a-z가-힣]+", "-", name.lower())).strip("-")


# ── CSV ───────────────────────────────────────────────────────────────────
def _int(s):
    s = re.sub(r"[^\d]", "", s or "")
    return int(s) if s else None


def parse_cell(cell):
    """상품명 셀 → dict. 형식이 다르면 ValueError."""
    lines = [l.strip() for l in cell.replace("\r", "").split("\n") if l.strip()]
    if len(lines) < 6 or not lines[-1].startswith("리뷰") or lines[-5] != "원":
        raise ValueError("형식 불명: " + " / ".join(lines[-6:]))
    out = {"review_count": _int(lines[-1]), "rating": float(lines[-2]) if re.fullmatch(r"\d(\.\d+)?", lines[-2]) else None,
           "advertiser": lines[-3][:100], "name": lines[-4][:200], "fee": 0.0, "fee_extra": 0.0, "label": None,
           "list_price": None, "sale_price": None, "discount_pct": None}
    head = lines[:-5]
    labels = []
    for i, l in enumerate(head):
        m = re.fullmatch(r"(수수료|추가) (\d+(?:\.\d+)?)%", l)
        if m:
            out["fee" if m.group(1) == "수수료" else "fee_extra"] = float(m.group(2))
        elif l in ("판매가", "할인율", "할인가"):
            nxt = head[i + 1] if i + 1 < len(head) else ""
            key = {"판매가": "list_price", "할인율": "discount_pct", "할인가": "sale_price"}[l]
            out[key] = _int(nxt)
        elif re.fullmatch(r"[\d,]+원?|\d+%", l):
            continue  # 위 표시의 값이거나 아래 가격 줄
        else:
            labels.append(l)
    price = _int(lines[-6])
    if out["sale_price"] is None:
        out["sale_price"] = price
    out["label"] = " ".join(l for l in labels if l != "수수료+")[:40] or None
    return out


def read_csv(text):
    """CSV 텍스트 → ([상품], [오류]). URL 이 겹치면 첫 행만."""
    rows, errors, seen = [], [], set()
    for i, r in enumerate(csv.reader(io.StringIO(text.lstrip("﻿")))):
        if i == 0 and r and r[0].strip() == "상품명":
            continue
        if len(r) < 2 or not r[1].strip():
            errors.append(f"{i + 1}행: 열 부족")
            continue
        url = r[1].strip()
        if not re.match(r"^https://(naver\.me|[a-z.]*naver\.com)/", url):
            errors.append(f"{i + 1}행: 네이버 제휴 주소가 아님 {url[:60]}")
            continue
        try:
            p = parse_cell(r[0])
        except ValueError as e:
            errors.append(f"{i + 1}행: {e}")
            continue
        if url not in seen:
            seen.add(url)
            rows.append({**p, "url": url})
    return rows, errors


# ── 분류·태그·브랜드 ──────────────────────────────────────────────────────
def _clean(name):
    return re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", name)


SKIP_RE = re.compile(r"대여|렌탈|임대|리뷰 ?작성|바람막이|가림막|네일 ?드릴|페퍼밀|후추|소금 ?그라인더|무전기|면도기 세정액")  # 판매가 아니거나 이 사이트 종류에 없는 상품


def kind_of(name):
    """종류어 사전으로 (상품군, 종류). 싣지 않을 상품은 (None, SKIP), 못 고르면 None."""
    if SKIP_RE.search(name):
        return None, SKIP
    n = name  # [호환]·[정품] 같은 괄호가 소모품 신호라 지우지 않는다
    for g, k, inc, exc in _KIND_RES:
        if inc.search(n) and not (exc and exc.search(n)):
            return g, k
    return None


# 가격대 태그(누적: 2만 원 상품은 '3만 원 이하'와 '5만 원 이하' 둘 다). 가격대 수식어 페이지가 이 태그로 상품을 고른다. 숫자는 태그 이름일 뿐 가격 표시가 아니다
PRICE_BANDS = [(30000, "3만 원 이하"), (50000, "5만 원 이하"), (100000, "10만 원 이하"), (200000, "20만 원 이하"),
               (300000, "30만 원 이하"), (500000, "50만 원 이하"), (1000000, "100만 원 이하")]
PRICE_TAGS = [t for _p, t in PRICE_BANDS]
# 방식·크기로 쪼갠 하위 종류: (부모 종류, 하위 종류, 상품 태그). 부모 상품 중 태그가 맞는 것이 하위 종류 상품
SUBKINDS = [("가습기", "가열식 가습기", ["가열식"]), ("가습기", "초음파 가습기", ["초음파"]), ("가습기", "미니 가습기", ["미니"]),
            ("전기매트", "탄소매트", ["탄소"])]
MIN_KIND = 3   # 종류마다 상품이 3개 이상이어야 독립 종류(페이지)로 둔다


def tags_of(name, price=None):
    n = _clean(name)
    tags = [t for t, r in _TAG_RES if r.search(n)]
    tags += [t for limit, t in PRICE_BANDS if price and price <= limit]
    for num, unit in CAPACITY_RE.findall(n):
        liters = float(num) / (1000 if unit.lower() == "ml" else 1)
        tags.append(f"{liters:g}L")
    return list(dict.fromkeys(tags))


def brand_token(name):
    """상품명 첫 낱말(괄호·공식 같은 꾸밈말 제외) → 브랜드 후보. 3개 이상인 것만 브랜드로 쓴다(import 에서 센다)."""
    for w in _clean(name).split():
        if BRAND_NOISE.match(w):
            continue
        if any(r.fullmatch(w) for _t, r in _TAG_RES) or any(inc.search(w) for _g, _k, inc, _x in _KIND_RES):
            return None  # 첫 낱말이 '무선'·'계란찜기' 같은 일반어면 브랜드 없음
        return BRAND_ALIAS.get(w, w)[:40]
    return None


def score_of(p):
    """순위 점수(페이지에는 쓰지 않는다): 수수료율 × 가격 × log 리뷰 수. 오픈 후엔 클릭 실적을 더한다(F9)."""
    price = p.get("sale_price") or p.get("list_price") or 0
    return round((p["fee"] + p["fee_extra"]) / 100 * price * math.log10((p.get("review_count") or 0) + 10), 3)


# ── 종류 행 ───────────────────────────────────────────────────────────────
def ensure_kinds():
    """KINDS 의 종류를 verticals 행으로(없는 것만). {종류 이름: id}"""
    have = {r["name"]: r["id"] for r in q("SELECT id, name FROM verticals WHERE parent_id IS NULL")}
    for g, k, *_ in KINDS:
        if k not in have:
            slug = slugify(k)
            have[k] = ex("INSERT INTO verticals (site_id, slug, name, url_base) VALUES (%s,%s,%s,%s)",
                         (GROUPS[g][0], slug, k, "/" + slug))
    return have


def load_overrides():
    return json.loads(OVERRIDES.read_text("utf-8")) if OVERRIDES.exists() else {}


def classify(p, kinds, overrides):
    """→ (vertical_id, classified_by, tags). 세션 판단(overrides)이 사전보다 먼저."""
    o = overrides.get(p["url"])
    tags = tags_of(p["name"], p.get("sale_price") or p.get("list_price"))
    if o:
        tags = list(dict.fromkeys(tags + o.get("tags", [])))
        return (None if o["kind"] == SKIP else kinds.get(o["kind"])), "session", tags
    hit = kind_of(p["name"])
    return (kinds.get(hit[1]), "dict", tags) if hit else (None, None, tags)


# ── 적재(교체) ────────────────────────────────────────────────────────────
def import_csv(text, filename="upload.csv", add_only=False):
    """URL 로 대조: 새 상품 추가·분류 / 목록에서 빠진 상품 inactive / 수수료·이름 변경 반영. 결과 요약 dict.
    add_only: 매일 주는 '새 상품만' 목록 — 목록에 없는 기존 상품을 내리지 않는다."""
    rows, errors = read_csv(text)
    if not rows:
        raise ValueError("읽은 상품이 없습니다: " + "; ".join(errors[:3]))
    kinds, overrides = ensure_kinds(), load_overrides()
    brand_n = Counter(brand_token(p["name"]) for p in rows)
    if add_only:  # 작은 목록이라 브랜드 3개 기준을 기존 상품까지 합쳐서 센다
        brand_n += Counter(brand_token(r["name"]) for r in q("SELECT name FROM offers WHERE status='active'"))
    existing = {r["url"]: r for r in q("SELECT id, url, name, fee, fee_extra, status, vertical_id, classified_by FROM offers")}
    added = changed = 0
    for p in rows:
        b = brand_token(p["name"])
        p["brand"] = b if b and brand_n[b] >= 3 else None
        p["score"] = score_of(p)
        old = existing.get(p["url"])
        vals = {k: p[k] for k in ("name", "advertiser", "brand", "label", "fee", "fee_extra", "list_price", "sale_price",
                                  "discount_pct", "rating", "review_count", "score")}
        if old and old["classified_by"] == "admin":   # 관리자가 손으로 고친 분류는 유지
            vid, by, tags = old["vertical_id"], "admin", tags_of(p["name"], p["sale_price"] or p["list_price"])
        else:
            vid, by, tags = classify(p, kinds, overrides)
        tags += ["@" + p["brand"]] if p["brand"] else []   # 브랜드 수식어 페이지가 이 태그로 상품을 고른다
        vals.update(vertical_id=vid, classified_by=by, tags=json.dumps(tags, ensure_ascii=False), status="active")
        if old:
            if old["status"] != "active" or old["name"] != p["name"] or float(old["fee"]) != p["fee"] or float(old["fee_extra"]) != p["fee_extra"]:
                changed += 1
            ex("UPDATE offers SET " + ",".join(f"{k}=%s" for k in vals) + ", imported_at=NOW() WHERE id=%s", [*vals.values(), old["id"]])
        else:
            ex("INSERT INTO offers (url, " + ",".join(vals) + ", imported_at) VALUES (%s," + ",".join(["%s"] * len(vals)) + ",NOW())",
               [p["url"], *vals.values()])
            added += 1
    gone = [] if add_only else [r["id"] for u, r in existing.items() if r["status"] == "active" and u not in {p["url"] for p in rows}]
    if gone:
        ex("UPDATE offers SET status='inactive' WHERE id IN (" + ",".join(["%s"] * len(gone)) + ")", gone)
    unclassified = q1("SELECT COUNT(*) n FROM offers WHERE status='active' AND classified_by IS NULL")["n"]
    result = {"rows_total": len(rows), "added": added, "removed": len(gone), "changed": changed, "unclassified": unclassified}
    ex("INSERT INTO offer_imports (filename, rows_total, added, removed, changed, unclassified, errors) VALUES (%s,%s,%s,%s,%s,%s,%s)",
       (filename[:120], *result.values(), json.dumps(errors, ensure_ascii=False)))
    refresh_kinds()
    ex("UPDATE sites SET cache_version=cache_version+1")
    return {**result, "errors": errors}


def kind_offers(vertical):
    """종류의 활성 상품. 하위 종류 = 부모 종류 상품 중 match_tags 를 모두 가진 것, 부모 종류 = 자기 상품 전부."""
    if vertical["parent_id"]:
        need = set(json.loads(vertical["match_tags"] or "[]"))
        return [o for o in q("SELECT * FROM offers WHERE status='active' AND vertical_id=%s ORDER BY id", (vertical["parent_id"],))
                if need <= set(json.loads(o["tags"] or "[]"))]
    return q("SELECT * FROM offers WHERE status='active' AND vertical_id=%s ORDER BY id", (vertical["id"],))


def refresh_kinds():
    """하위 종류 행을 만들고, 상품이 MIN_KIND 개 미만인 종류는 비활성(페이지 생성·공개에서 빠짐). 상품이 다시 늘면 다시 활성."""
    parents = {r["name"]: r for r in q("SELECT * FROM verticals WHERE parent_id IS NULL")}
    for parent, name, tags in SUBKINDS:
        pr = parents.get(parent)
        if pr and not q1("SELECT id FROM verticals WHERE name=%s", (name,)):
            slug = slugify(name)
            ex("INSERT INTO verticals (site_id, parent_id, slug, name, url_base, match_tags) VALUES (%s,%s,%s,%s,%s,%s)",
               (pr["site_id"], pr["id"], slug, name, "/" + slug, json.dumps(tags, ensure_ascii=False)))
    for v in q("SELECT * FROM verticals"):
        active = int(len(kind_offers(v)) >= MIN_KIND)
        if active != v["is_active"]:
            ex("UPDATE verticals SET is_active=%s WHERE id=%s", (active, v["id"]))


def apply_overrides():
    """data/classify.json 을 현재 상품에 다시 적용(세션이 분류를 쓴 뒤). 바뀐 수."""
    kinds, overrides, n = ensure_kinds(), load_overrides(), 0
    for r in q("SELECT id, url, name, brand, sale_price, list_price FROM offers WHERE IFNULL(classified_by,'')<>'admin'"):
        if r["url"] in overrides:
            vid, by, tags = classify(r, kinds, overrides)
            tags += ["@" + r["brand"]] if r["brand"] else []
            ex("UPDATE offers SET vertical_id=%s, classified_by=%s, tags=%s WHERE id=%s", (vid, by, json.dumps(tags, ensure_ascii=False), r["id"]))
            n += 1
    refresh_kinds()
    return n


def unclassified():
    """사전으로 못 고른 상품(세션이 읽고 data/classify.json 에 종류를 쓴다)."""
    return q("SELECT id, url, name, advertiser FROM offers WHERE status='active' AND classified_by IS NULL ORDER BY id")


if __name__ == "__main__":  # 자체 점검: python products.py (DB 불필요)
    a = parse_cell("수수료 5%\n판매가\n218,000원\n할인율\n27%\n할인가\n159,000\n원\n엄공 하이라이트 전기히터 난로 가정용 사무실 난방기\n엄공\n4.71\n리뷰 28,557")
    assert (a["fee"], a["list_price"], a["sale_price"], a["discount_pct"], a["review_count"], a["advertiser"]) == (5, 218000, 159000, 27, 28557, "엄공"), a
    b = parse_cell("NEW\n수수료 2%\n추가 3%\n229,000\n원\n공식 애플 에어팟 5 MKFT4KH/A\n애플 공식 브랜드스토어\n4.92\n리뷰 3,833")
    assert (b["fee"], b["fee_extra"], b["sale_price"], b["label"], b["list_price"]) == (2, 3, 229000, "NEW", None), b
    c = parse_cell("김장대전\n수수료 5%\n판매가\n599,000원\n할인율\n41%\n할인가\n349,000\n원\n[김장대전] 미닉스 미니 김치냉장고 39L\n미닉스\n4.77\n리뷰 4,548")
    assert c["label"] == "김장대전" and c["sale_price"] == 349000
    d = parse_cell("수수료+\n수수료 5%\n추가 3%\n판매가\n599,000원\n할인율\n14%\n할인가\n514,000\n원\nLG 프라엘 써마샷\nLG프라엘\n4.79\n리뷰 5,629")
    assert d["label"] is None and d["fee_extra"] == 3
    assert kind_of("엄공 하이라이트 전기히터 난로 가정용 사무실 난방기")[1] == "전기히터"
    assert kind_of("쿠쿠 하이라이트 1구 전기레인지")[1] == "인덕션"
    assert kind_of("[호환] 다이슨 청소기 필터 V8")[1] == "청소기 소모품"
    assert kind_of("오아 퓨어 미니 소형 공기청정기 원룸 탁상용 H13 헤파필터")[1] == "공기청정기"
    assert kind_of("HDMI 분배기 4K UHD 영상 1:2 스위치")[1] == "TV 액세서리" and kind_of("삼성 Mini LED KU75MH70AFXKR 189cm 4K AI 스마트 축구 대형TV 스탠드")[1] == "TV" and kind_of("유맥스 무결점보증 MX43F 109cm(43인치) FHD 1등급 LEDTV 중소기업 스탠드")[1] == "TV"
    assert kind_of("비쎌 스팀청소기대여 3일") == (None, SKIP)
    assert kind_of("한일의료기 탄소매트 전기매트 1인용")[1] == "전기매트"
    assert brand_token("[공식] 삼성전자 갤럭시 버즈") == "삼성" and brand_token("공식 애플 에어팟 5") == "애플"
    assert "1L" in tags_of("스테나 가습기 1L 가열식") and "가열식" in tags_of("스테나 가습기 1L 가열식")
    assert brand_token("계란찜기 스텐 미니") is None and brand_token("브라운네보 전기면도기") == "브라운"
    assert "3만 원 이하" in tags_of("x", 29000) and "5만 원 이하" in tags_of("x", 29000) and "3만 원 이하" not in tags_of("x", 31000)
    assert kind_of("오아 클린이퓨어pro 칫솔살균기 바람 건조 히팅 휴대용 살균기 건조기")[1] == "칫솔살균기"
    assert kind_of("엘레센스 2in1 모니터 조명 라이트바 LED 스탠드 램프")[1] == "조명·무드등"
    assert kind_of("블라우풍트 블루소닉 음파 전동 칫솔 스윙 모션 고주파 잇몸")[1] == "전동칫솔"
    assert kind_of("윈드비 천장형 에어컨 바람막이") == (None, SKIP) and kind_of("젤 네일 드릴 발톱 그라인더") == (None, SKIP)
    assert og_image('<meta property="og:image" content="https://shop-phinf.pstatic.net/a.jpg?type=m510&amp;x=1">') == "https://shop-phinf.pstatic.net/a.jpg?type=m510&x=1"
    assert og_image('<meta content="//x.jpg" property="og:image">') == "" and og_image("") == ""
    print("ok")
