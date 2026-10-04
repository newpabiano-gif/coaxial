"""공모주 청약 일정 수집.

출처
- 38커뮤니케이션 공모주 청약일정 목록/상세 페이지 (주 출처)
- 금융감독원 OpenDART 증권신고서(지분증권) 주요정보 API (교차 확인용, DART_API_KEY 가 있을 때만)

숫자와 날짜는 페이지/API 에 적힌 값만 쓴다. 못 찾은 값은 None 으로 두고
format.py 에서 "확인 필요"로 바꾼다.
"""

import logging
import re
import ssl
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter

log = logging.getLogger(__name__)

BASE_38 = "https://www.38.co.kr"
LIST_URL_38 = BASE_38 + "/html/fund/index.htm?o=k"
DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_ESTK_URL = "https://opendart.fss.or.kr/api/estkRs.json"
DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="

USER_AGENT = "Mozilla/5.0 (personal IPO schedule alert; 1 request per second)"
REQUEST_INTERVAL_SEC = 1.0
WINDOW_DAYS = 7  # 오늘 포함 7일
MAX_LIST_PAGES = 3


class CollectError(Exception):
    """수집 실패. 메시지는 실패 메일 본문에 그대로 들어간다."""


@dataclass
class Offering:
    name: str
    detail_url: str
    underwriter: str | None = None
    sub_start: date | None = None
    sub_end: date | None = None
    refund: date | None = None
    listing: date | None = None
    final_price: str | None = None  # "23,500"
    band: str | None = None  # "19,500-23,500"
    inst_ratio: str | None = None  # "1097.62:1"
    lockup: str | None = None  # "5.29%"
    # OpenDART 교차 확인 결과
    dart_sub_start: date | None = None
    dart_sub_end: date | None = None
    dart_price: str | None = None
    dart_url: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class CollectResult:
    today: date
    offerings: list[Offering]
    sources: list[str]
    warnings: list[str]


# ---------------------------------------------------------------- HTML 파싱


class _CellParser(HTMLParser):
    """표를 행(tr) 단위로 읽는다.

    텍스트는 가장 안쪽 td/th 에만 붙인다. 바깥 레이아웃용 td 는 빈 칸이 되어
    "라벨 칸 바로 다음 칸이 값" 규칙이 깨지지 않는다.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells: list[str] = []  # 문서 순서대로 모든 칸
        self.rows: list[tuple[list[str], list[str]]] = []  # (칸 텍스트, 링크)
        self._cell_stack: list[list[str]] = []
        self._row_stack: list[tuple[list[str], list[str]]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row_stack.append(([], []))
        elif tag in ("td", "th"):
            self._cell_stack.append([])
        elif tag == "a" and self._row_stack:
            href = dict(attrs).get("href")
            if href:
                self._row_stack[-1][1].append(href)
        elif tag == "br" and self._cell_stack:
            self._cell_stack[-1].append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell_stack:
            text = _clean("".join(self._cell_stack.pop()))
            self.cells.append(text)
            if self._row_stack:
                self._row_stack[-1][0].append(text)
        elif tag == "tr" and self._row_stack:
            self.rows.append(self._row_stack.pop())

    def handle_data(self, data):
        if self._cell_stack:
            self._cell_stack[-1].append(data)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _key(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _parse(html: str) -> _CellParser:
    p = _CellParser()
    p.feed(html)
    p.close()
    return p


def _value_after(cells: list[str], label: str, numeric: bool = True) -> str | None:
    """라벨과 정확히 같은 칸을 찾아 바로 다음 칸 값을 돌려준다. 첫 번째 것만 쓴다.

    numeric 이면 숫자가 하나도 없는 값('-', 빈칸)은 못 찾은 것으로 본다.
    """
    want = _key(label)
    for i, c in enumerate(cells[:-1]):
        if _key(c) == want:
            v = cells[i + 1]
            if numeric and not re.search(r"\d", v):
                return None
            return v or None
    return None


_DATE_RE = re.compile(r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")


def _dates_in(text: str | None) -> list[date]:
    if not text:
        return []
    out = []
    for y, m, d in _DATE_RE.findall(text):
        try:
            out.append(date(int(y), int(m), int(d)))
        except ValueError:
            pass
    return out


def _first_date(text: str | None) -> date | None:
    ds = _dates_in(text)
    return ds[0] if ds else None


def _date_range(text: str | None) -> tuple[date | None, date | None]:
    ds = _dates_in(text)
    if len(ds) >= 2:
        return ds[0], ds[1]
    if len(ds) == 1:
        short = _list_range(text or "")  # '2026.10.02~10.06' 처럼 끝 날짜에 연도가 없는 경우
        return short if short else (ds[0], ds[0])
    return None, None


_LIST_RANGE_RE = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})\s*~\s*(\d{1,2})\.(\d{1,2})")


def _list_range(text: str) -> tuple[date, date] | None:
    """목록의 '2026.10.02~10.06' 형식. 끝 날짜의 연도는 생략되어 있다."""
    m = _LIST_RANGE_RE.search(text)
    if not m:
        return None
    y, m1, d1, m2, d2 = map(int, m.groups())
    try:
        start = date(y, m1, d1)
        end = date(y + 1 if m2 < m1 else y, m2, d2)
    except ValueError:
        return None
    return start, end


def _number(text: str | None) -> str | None:
    """'**23,500** 원' -> '23,500'"""
    if not text:
        return None
    m = re.search(r"\d[\d,]*", text)
    return m.group(0) if m else None


def _band(text: str | None) -> str | None:
    """'19,500 ~ 23,500 원' -> '19,500-23,500'"""
    if not text:
        return None
    nums = re.findall(r"\d[\d,]*", text)
    if len(nums) >= 2:
        return f"{nums[0]}-{nums[1]}"
    return nums[0] if nums else None


def _ratio(text: str | None) -> str | None:
    if not text:
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?\s*:\s*1", text)
    return re.sub(r"\s", "", m.group(0)) if m else None


def _percent(text: str | None) -> str | None:
    if not text:
        return None
    m = re.search(r"\d+(?:\.\d+)?\s*%", text)
    return re.sub(r"\s", "", m.group(0)) if m else None


def parse_list_page(html: str) -> list[dict]:
    """목록 페이지에서 종목명, 상세 링크, 청약 기간을 뽑는다."""
    items = []
    for cells, links in _parse(html).rows:
        detail = next((h for h in links if "o=v" in h and "no=" in h), None)
        if not detail:
            continue
        rng = next((r for r in map(_list_range, cells) if r), None)
        if not rng:
            continue
        name = next((c for c in cells if c), "")
        items.append(
            {
                "name": name,
                "detail_url": urljoin(BASE_38 + "/html/fund/", detail),
                "start": rng[0],
                "end": rng[1],
            }
        )
    return items


def parse_detail_page(html: str, offering: Offering) -> Offering:
    cells = _parse(html).cells
    underwriter = _value_after(cells, "주간사", numeric=False)
    offering.underwriter = _clean_underwriter(underwriter) if underwriter else None
    start, end = _date_range(_value_after(cells, "공모청약일"))
    if start:
        offering.sub_start, offering.sub_end = start, end
    offering.refund = _first_date(_value_after(cells, "환불일"))
    offering.listing = _first_date(_value_after(cells, "상장일")) or _first_date(
        _value_after(cells, "신규상장일")
    )
    offering.final_price = _number(_value_after(cells, "확정공모가"))
    offering.band = _band(_value_after(cells, "희망공모가액"))
    offering.inst_ratio = _ratio(_value_after(cells, "기관경쟁률"))
    offering.lockup = _percent(_value_after(cells, "의무보유확약"))
    return offering


def _clean_underwriter(text: str) -> str:
    # 상세 페이지 주간사 칸 뒤에 '주식수: ... / 청약한도: ...' 가 이어 붙는 경우가 있다.
    return re.split(r"주식수|청약한도", text)[0].strip(" /")


# ---------------------------------------------------------------- 네트워크


def _get(session: requests.Session, url: str, **kw) -> requests.Response:
    time.sleep(REQUEST_INTERVAL_SEC)
    resp = session.get(url, timeout=20, **kw)
    resp.raise_for_status()
    return resp


class _LegacyTLSAdapter(HTTPAdapter):
    """38커뮤니케이션 전용. 이 서버는 오래된 TLS 설정을 써서 OpenSSL 3 기본값으로는
    'sslv3 alert handshake failure' 가 난다. 인증서 검증은 그대로 두고 허용 범위만 넓힌다.
    """

    def _context(self):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        ctx.minimum_version = ssl.TLSVersion.TLSv1
        ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        return ctx

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self._context()
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs["ssl_context"] = self._context()
        return super().proxy_manager_for(*args, **kwargs)


def _get_38(session: requests.Session, url: str) -> str:
    try:
        resp = _get(session, url)
    except requests.exceptions.SSLError:
        # 완화한 TLS 로도 안 되면 공개 페이지라 http 로 한 번 더 시도한다.
        log.warning("HTTPS 접속 실패, HTTP로 재시도: %s", url)
        resp = _get(session, url.replace("https://", "http://", 1))
    # 38커뮤니케이션은 EUC-KR 계열로 응답한다. cp949 가 EUC-KR 의 상위 집합이다.
    return resp.content.decode("cp949", errors="replace")


def in_window(start: date, end: date, today: date) -> bool:
    """오늘부터 WINDOW_DAYS 일 안에 시작하거나, 지금 진행 중인 청약."""
    last_day = today + timedelta(days=WINDOW_DAYS - 1)
    return end >= today and start <= last_day


def collect_38(session: requests.Session, today: date) -> list[Offering]:
    targets = []
    for page in range(1, MAX_LIST_PAGES + 1):
        url = LIST_URL_38 if page == 1 else f"{LIST_URL_38}&page={page}"
        items = parse_list_page(_get_38(session, url))
        if not items:
            if page == 1:
                raise CollectError("38커뮤니케이션 목록 페이지에서 종목 표를 찾지 못함 (페이지 구조 변경 가능성)")
            break
        targets += [i for i in items if in_window(i["start"], i["end"], today)]
        # 목록은 청약일 내림차순이다. 이 페이지의 가장 오래된 청약이 이미 끝났으면 더 볼 필요 없다.
        if min(i["end"] for i in items) < today:
            break

    offerings = []
    for item in targets:
        o = Offering(name=item["name"], detail_url=item["detail_url"])
        try:
            parse_detail_page(_get_38(session, item["detail_url"]), o)
        except requests.RequestException as e:
            log.warning("상세 페이지 실패 %s: %s", item["detail_url"], e)
            o.notes.append("상세 페이지 조회 실패")
        if o.sub_start is None:
            o.sub_start, o.sub_end = item["start"], item["end"]
        elif (o.sub_start, o.sub_end) != (item["start"], item["end"]):
            o.notes.append(
                "38커뮤니케이션 목록과 상세 페이지의 청약일이 다름 "
                f"(목록 {item['start']:%m/%d}-{item['end']:%m/%d})"
            )
        offerings.append(o)
    return offerings


# ---------------------------------------------------------------- OpenDART


def _norm_name(name: str) -> str:
    name = re.sub(r"\(구\..*?\)", "", name)
    name = re.sub(r"\(주\)|㈜|주식회사", "", name)
    return re.sub(r"\s+", "", name)


def _dart_json(session: requests.Session, url: str, params: dict) -> dict:
    data = _get(session, url, params=params).json()
    status = data.get("status")
    if status not in ("000", "013"):  # 013: 조회된 데이터 없음
        raise CollectError(f"OpenDART 오류 {status}: {data.get('message')}")
    return data


def _dart_corp_codes(session: requests.Session, api_key: str, today: date) -> dict[str, str]:
    """최근 증권신고서(지분증권) 공시에서 회사명 -> 고유번호 표를 만든다."""
    codes: dict[str, str] = {}
    params = {
        "crtfc_key": api_key,
        "bgn_de": (today - timedelta(days=89)).strftime("%Y%m%d"),
        "end_de": today.strftime("%Y%m%d"),
        "pblntf_ty": "C",  # 발행공시
        "page_count": 100,
    }
    for page_no in range(1, 11):
        data = _dart_json(session, DART_LIST_URL, {**params, "page_no": page_no})
        for row in data.get("list", []):
            if "증권신고서(지분증권)" in row.get("report_nm", ""):
                codes.setdefault(_norm_name(row["corp_name"]), row["corp_code"])
        if page_no >= int(data.get("total_page", 1) or 1):
            break
    return codes


def enrich_with_dart(
    session: requests.Session, api_key: str, offerings: list[Offering], today: date
) -> None:
    codes = _dart_corp_codes(session, api_key, today)
    for o in offerings:
        corp_code = codes.get(_norm_name(o.name))
        if not corp_code:
            o.notes.append("OpenDART에서 증권신고서를 찾지 못함")
            continue
        data = _dart_json(
            session,
            DART_ESTK_URL,
            {
                "crtfc_key": api_key,
                "corp_code": corp_code,
                "bgn_de": (today - timedelta(days=180)).strftime("%Y%m%d"),
                "end_de": today.strftime("%Y%m%d"),
            },
        )
        groups = {g.get("title"): g.get("list", []) for g in data.get("group", [])}
        general = groups.get("일반사항", [])
        if not general:
            o.notes.append("OpenDART 증권신고서 요약 정보 없음")
            continue
        latest_no = max(r["rcept_no"] for r in general)
        g = next(r for r in general if r["rcept_no"] == latest_no)
        o.dart_sub_start, o.dart_sub_end = _date_range(g.get("sbd"))
        stock = next((r for r in groups.get("증권의종류", []) if r["rcept_no"] == latest_no), None)
        if stock:
            o.dart_price = _number(stock.get("slprc"))
        o.dart_url = DART_VIEWER_URL + latest_no


# ---------------------------------------------------------------- 진입점


def collect(today: date, dart_api_key: str | None = None) -> CollectResult:
    """한 번 수집한다. 38커뮤니케이션 실패는 CollectError/RequestException 으로 올린다.

    OpenDART 는 교차 확인용이라 실패해도 전체를 실패시키지 않고 warnings 에 남긴다.
    """
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.mount(BASE_38, _LegacyTLSAdapter())
    offerings = collect_38(session, today)
    sources = [LIST_URL_38] + [o.detail_url for o in offerings]
    warnings = []

    if offerings and dart_api_key:
        try:
            enrich_with_dart(session, dart_api_key, offerings, today)
            sources += [o.dart_url for o in offerings if o.dart_url]
        except (requests.RequestException, CollectError, ValueError, KeyError) as e:
            log.exception("OpenDART 교차 확인 실패")
            warnings.append(f"OpenDART 교차 확인 실패: {e}")
    elif offerings:
        warnings.append("OpenDART 교차 확인 안 함 (DART_API_KEY 없음)")

    return CollectResult(today=today, offerings=offerings, sources=sources, warnings=warnings)
