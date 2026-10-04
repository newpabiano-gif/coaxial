"""메일 제목과 본문 만들기. 폰에서 읽기 좋게 짧게, 기호는 하이픈과 슬래시만 쓴다."""

from datetime import date

from collect import CollectResult, Offering

NEED_CHECK = "확인 필요"
MISMATCH = "출처 간 불일치"
NO_SCHEDULE = "이번 주 청약 일정 없음"


def subject(today: date) -> str:
    return f"[공모주] {today:%Y-%m-%d} 이번 주 청약 일정"


def failure_subject(today: date) -> str:
    return f"[공모주] {today:%Y-%m-%d} 공모주 데이터 수집 실패"


def failure_body(reason: str) -> str:
    return f"공모주 데이터 수집 실패: {reason}"


def _md(d: date | None) -> str:
    return f"{d:%m/%d}" if d else NEED_CHECK


def _period(start: date | None, end: date | None) -> str:
    if not start:
        return NEED_CHECK
    return f"{_md(start)}-{_md(end)}"


def _price(o: Offering) -> str:
    if o.final_price:
        price = f"{o.final_price}원"
    elif o.band:
        price = f"{o.band}원 희망밴드"
    else:
        price = NEED_CHECK
    # 공모가 교차 확인은 확정 공모가가 있을 때만 한다. 미확정 단계의 신고서 가액은 비교 대상이 아니다.
    if o.final_price and o.dart_price and o.dart_price != o.final_price:
        price = f"{o.final_price}원 38커뮤니케이션 / {o.dart_price}원 DART {MISMATCH}"
    return price


def _subscription(o: Offering) -> str:
    text = _period(o.sub_start, o.sub_end)
    if o.dart_sub_start and (o.dart_sub_start, o.dart_sub_end) != (o.sub_start, o.sub_end):
        text = (
            f"{text} 38커뮤니케이션 / {_period(o.dart_sub_start, o.dart_sub_end)} DART {MISMATCH}"
        )
    return text


def offering_block(o: Offering, today: date) -> str:
    head = f"- {o.name} ({o.underwriter or NEED_CHECK})"
    if o.sub_end == today:
        head = f"- 오늘 마감 - {o.name} ({o.underwriter or NEED_CHECK})"
    lines = [
        head,
        f"  청약: {_subscription(o)} / 환불: {_md(o.refund)} / 상장: {_md(o.listing)}",
        f"  공모가: {_price(o)} / 경쟁률: {o.inst_ratio or NEED_CHECK} / 확약: {o.lockup or NEED_CHECK}",
    ]
    lines += [f"  참고: {n}" for n in o.notes]
    return "\n".join(lines)


def sort_key(o: Offering, today: date):
    # 오늘 마감이 맨 위, 나머지는 청약 시작일 순.
    return (o.sub_end != today, o.sub_start or date.max, o.name)


def body(result: CollectResult) -> str:
    if not result.offerings:
        # 규칙상 일정이 없으면 한 줄만 보낸다.
        return NO_SCHEDULE

    today = result.today
    offerings = sorted(result.offerings, key=lambda o: sort_key(o, today))
    parts = [offering_block(o, today) for o in offerings]
    parts.append("경쟁률은 기관 수요예측 경쟁률, 확약은 의무보유확약 비율")
    if result.warnings:
        parts.append("\n".join(f"- {w}" for w in result.warnings))
    parts.append("출처\n" + "\n".join(f"- {url}" for url in dict.fromkeys(result.sources)))
    return "\n\n".join(parts)
