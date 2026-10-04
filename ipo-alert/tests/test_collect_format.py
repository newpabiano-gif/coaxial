"""38커뮤니케이션 페이지 구조를 흉내 낸 HTML 로 파서와 본문 형식을 확인한다.

실제 페이지를 저장한 것이 아니므로, 실제 페이지에서의 동작은 --dry-run 실행으로 따로 확인해야 한다.
"""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import format as fmt  # noqa: E402
from collect import (  # noqa: E402
    CollectResult,
    Offering,
    _date_range,
    in_window,
    parse_detail_page,
    parse_list_page,
)

LIST_HTML = """
<table><tr><td>
  <table summary="공모주 청약일정">
    <tr><td>종목명</td><td>공모주일정</td><td>확정공모가</td><td>희망공모가</td>
        <td>청약경쟁률</td><td>주간사</td><td>분석</td></tr>
    <tr><td><a href="/html/fund/?o=v&no=2316&l=&page=1"><font>엘리스그룹</font></a></td>
        <td>2026.10.07~10.08</td><td>-</td><td>70,400~90,500</td><td>&nbsp;</td>
        <td>미래에셋증권,삼성증권</td><td><a href="/html/fund/index.htm?o=v&no=2316"><img></a></td></tr>
    <tr><td><a href="/html/fund/?o=v&no=2320&l=&page=1">진코스텍</a></td>
        <td>2026.10.02~10.06</td><td>23,500</td><td>19,500~23,500</td><td></td>
        <td>하나증권</td><td></td></tr>
    <tr><td><a href="/html/fund/?o=v&no=9999">연말기업</a></td>
        <td>2026.12.31~01.02</td><td>-</td><td>1,000~2,000</td><td></td><td>KB증권</td><td></td></tr>
  </table>
</td></tr></table>
"""

DETAIL_HTML = """
<table><tr><td>
  <table>
    <tr><td>총공모주식수</td><td>852,000 주</td><td>액면가</td><td>500 원</td></tr>
    <tr><td>희망공모가액</td><td>19,500 ~ 23,500 원</td><td>청약경쟁률</td><td>&nbsp;</td></tr>
    <tr><td>확정공모가</td><td><b>23,500</b> 원</td><td>공모금액</td><td>20,022 (백만원)</td></tr>
    <tr><td>주간사</td><td><b>하나증권</b></td><td>주식수: 213,000 주 / 청약한도: 10,000~12,000 주</td></tr>
  </table>
  <table>
    <tr><td rowspan="6">주요일정</td><td>수요예측일</td><td>2026.09.16 &nbsp;~&nbsp; 2026.09.22</td></tr>
    <tr><td>공모청약일</td><td>2026.10.02 &nbsp;~&nbsp; 2026.10.06</td></tr>
    <tr><td>배정공고일(신문)</td><td>2026.10.08 (주간사 홈페이지 참조)</td></tr>
    <tr><td>납입일</td><td>2026.10.08</td></tr>
    <tr><td>환불일</td><td>2026.10.08</td></tr>
    <tr><td>상장일</td><td>2026.10.15</td></tr>
    <tr><td>수요예측결과</td><td><table><tr>
      <td>기관경쟁률</td><td>1097.62:1</td><td>의무보유확약</td><td>5.29%</td>
    </tr></table></td></tr>
  </table>
</td></tr></table>
"""

DETAIL_HTML_UNPRICED = """
<table>
  <tr><td>희망공모가액</td><td>70,400 ~ 90,500 원</td></tr>
  <tr><td>확정공모가</td><td>- 원</td></tr>
  <tr><td>주간사</td><td>미래에셋증권,삼성증권</td></tr>
  <tr><td>공모청약일</td><td>2026.10.07 ~ 2026.10.08</td></tr>
  <tr><td>환불일</td><td>2026.10.12</td></tr>
  <tr><td>상장일</td><td></td></tr>
  <tr><td>기관경쟁률</td><td></td><td>의무보유확약</td><td></td></tr>
</table>
"""


class ParseTest(unittest.TestCase):
    def test_list_page(self):
        items = parse_list_page(LIST_HTML)
        self.assertEqual([i["name"] for i in items], ["엘리스그룹", "진코스텍", "연말기업"])
        self.assertEqual(items[0]["detail_url"], "https://www.38.co.kr/html/fund/?o=v&no=2316&l=&page=1")
        self.assertEqual((items[1]["start"], items[1]["end"]), (date(2026, 10, 2), date(2026, 10, 6)))
        # 해를 넘기는 청약
        self.assertEqual(items[2]["end"], date(2027, 1, 2))

    def test_detail_page(self):
        o = parse_detail_page(DETAIL_HTML, Offering("진코스텍", "u"))
        self.assertEqual(o.underwriter, "하나증권")
        self.assertEqual((o.sub_start, o.sub_end), (date(2026, 10, 2), date(2026, 10, 6)))
        self.assertEqual(o.refund, date(2026, 10, 8))
        self.assertEqual(o.listing, date(2026, 10, 15))
        self.assertEqual(o.final_price, "23,500")
        self.assertEqual(o.band, "19,500-23,500")
        self.assertEqual(o.inst_ratio, "1097.62:1")
        self.assertEqual(o.lockup, "5.29%")

    def test_detail_page_missing_values_stay_none(self):
        o = parse_detail_page(DETAIL_HTML_UNPRICED, Offering("엘리스그룹", "u"))
        self.assertIsNone(o.final_price)
        self.assertEqual(o.band, "70,400-90,500")
        self.assertIsNone(o.listing)
        self.assertIsNone(o.inst_ratio)
        self.assertIsNone(o.lockup)
        self.assertEqual(o.underwriter, "미래에셋증권,삼성증권")

    def test_date_range_formats(self):
        self.assertEqual(
            _date_range("2026년 10월 02일 ~ 2026년 10월 06일"), (date(2026, 10, 2), date(2026, 10, 6))
        )
        self.assertEqual(_date_range("2026.10.02 ~ 10.06"), (date(2026, 10, 2), date(2026, 10, 6)))
        self.assertEqual(_date_range("-"), (None, None))

    def test_window(self):
        today = date(2026, 10, 5)  # 월요일
        self.assertTrue(in_window(date(2026, 10, 2), date(2026, 10, 6), today))  # 진행 중
        self.assertTrue(in_window(date(2026, 10, 11), date(2026, 10, 12), today))  # 7일째 시작
        self.assertFalse(in_window(date(2026, 10, 12), date(2026, 10, 13), today))  # 8일째 시작
        self.assertFalse(in_window(date(2026, 10, 1), date(2026, 10, 2), today))  # 이미 끝남


class FormatTest(unittest.TestCase):
    today = date(2026, 10, 6)

    def _result(self, offerings, warnings=()):
        return CollectResult(self.today, offerings, ["https://www.38.co.kr/x"], list(warnings))

    def test_empty(self):
        self.assertEqual(fmt.body(self._result([])), "이번 주 청약 일정 없음")

    def test_subject(self):
        self.assertEqual(fmt.subject(self.today), "[공모주] 2026-10-06 이번 주 청약 일정")

    def test_closing_today_first_and_missing_values(self):
        later = Offering("엘리스그룹", "u2", underwriter="미래에셋증권", band="70,400-90,500",
                         sub_start=date(2026, 10, 7), sub_end=date(2026, 10, 8))
        closing = Offering("진코스텍", "u1", underwriter="하나증권", final_price="23,500",
                           sub_start=date(2026, 10, 2), sub_end=date(2026, 10, 6),
                           refund=date(2026, 10, 8), listing=date(2026, 10, 15),
                           inst_ratio="1097.62:1", lockup="5.29%")
        text = fmt.body(self._result([later, closing]))
        blocks = text.split("\n\n")
        self.assertEqual(
            blocks[0],
            "- 오늘 마감 - 진코스텍 (하나증권)\n"
            "  청약: 10/02-10/06 / 환불: 10/08 / 상장: 10/15\n"
            "  공모가: 23,500원 / 경쟁률: 1097.62:1 / 확약: 5.29%",
        )
        self.assertEqual(
            blocks[1],
            "- 엘리스그룹 (미래에셋증권)\n"
            "  청약: 10/07-10/08 / 환불: 확인 필요 / 상장: 확인 필요\n"
            "  공모가: 70,400-90,500원 희망밴드 / 경쟁률: 확인 필요 / 확약: 확인 필요",
        )
        self.assertTrue(text.endswith("출처\n- https://www.38.co.kr/x"))

    def test_source_mismatch_shows_both(self):
        o = Offering("A", "u", underwriter="B", final_price="10,000",
                     sub_start=date(2026, 10, 7), sub_end=date(2026, 10, 8),
                     dart_price="9,000", dart_sub_start=date(2026, 10, 8), dart_sub_end=date(2026, 10, 9))
        block = fmt.offering_block(o, self.today)
        self.assertIn("10,000원 38커뮤니케이션 / 9,000원 DART 출처 간 불일치", block)
        self.assertIn("10/07-10/08 38커뮤니케이션 / 10/08-10/09 DART 출처 간 불일치", block)

    def test_failure_body(self):
        self.assertEqual(fmt.failure_body("타임아웃"), "공모주 데이터 수집 실패: 타임아웃")


if __name__ == "__main__":
    unittest.main()
