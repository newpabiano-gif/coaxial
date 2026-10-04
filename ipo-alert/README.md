# 공모주 알림

매주 월요일 아침 8시(KST)에 그 주(월-일) 7일 안의 공모주 청약 일정을 Gmail로 보낸다. 규칙은 `CLAUDE.md`에 있다.

## 실행

```
pip install -r requirements.txt
cp .env.example .env        # 값 채우기
python send.py --dry-run    # 메일 대신 화면 출력
python send.py              # 메일 발송
python -m unittest discover -s tests
```

GitHub Actions(`.github/workflows/ipo-alert.yml`)가 매주 월요일 08:00 KST에 실행한다.
저장소 Settings - Secrets and variables - Actions 에 `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`,
`MAIL_TO`(받는 주소, 비우면 GMAIL_ADDRESS), `DART_API_KEY`(선택)를 넣는다. 로그는 실행마다 artifact로 30일 보관된다.

## 데이터 출처 확인 결과 (2026-10-04 확인)

38커뮤니케이션
- robots.txt 내용은 `User-agent: *` / `Disallow:` (빈 값)로, 크롤링을 막는 규칙이 없다.
- 이용약관에서 자동 수집을 금지하는지는 확인하지 못했다. 확인 필요.
- 목록: `https://www.38.co.kr/html/fund/index.htm?o=k`, 청약일 내림차순, 페이지당 약 30개.
  열: 종목명 / 공모주일정(`2026.10.02~10.06`) / 확정공모가 / 희망공모가 / 청약경쟁률 / 주간사.
  목록의 청약경쟁률은 일반 청약 경쟁률이라 쓰지 않는다.
- 상세: `https://www.38.co.kr/html/fund/?o=v&no=번호`. 라벨 칸 바로 다음 칸이 값이다.
  쓰는 라벨: 주간사, 공모청약일, 환불일, 상장일, 확정공모가, 희망공모가액, 기관경쟁률, 의무보유확약.
- 인코딩은 EUC-KR.
- 테스트는 위 구조를 흉내 낸 HTML로만 했다. 실제 페이지 파싱은 첫 `--dry-run` 실행으로 확인 필요.

OpenDART (교차 확인용)
- 증권신고서 주요정보 - 지분증권 API `https://opendart.fss.or.kr/api/estkRs.json`
  (요청: crtfc_key, corp_code, bgn_de, end_de / 응답: 청약기일 `sbd`, 모집가액 `slprc` 등).
- 이 API에는 수요예측 경쟁률과 의무보유확약 비율이 없다. 두 값은 38커뮤니케이션 값만 쓴다.
- corp_code는 공시검색 API `list.json`(발행공시 `pblntf_ty=C`, 최근 90일)에서 회사명으로 찾는다.
- `sbd`의 실제 날짜 표기 형식은 확인하지 못했다. 확인 필요. 여러 형식을 받도록 작성해 두었다.
- 인증키는 https://opendart.fss.or.kr 에서 발급한다. 이 컨테이너에서는 접속이 막혀 실제 호출은 확인하지 못했다.

## 정해 둔 동작

- 기간: 오늘 포함 7일(오늘 ~ 오늘+6일) 안에 청약이 시작되거나 진행 중인 종목.
- 공모가: 확정 공모가가 있으면 그것을, 없으면 `70,400-90,500원 희망밴드`로 쓴다.
- 출처 간 불일치: 청약일과 확정 공모가만 비교한다. 공모가 확정 전의 신고서 가액은 비교하지 않는다.
- OpenDART 조회가 실패해도 38커뮤니케이션 결과는 보낸다. 실패 사실은 본문 아래에 적는다.
- 재시도: 첫 시도 실패 후 30초 간격으로 최대 3번. 그래도 실패하면 실패 메일을 보낸다.
- 공휴일은 따로 빼지 않는다. 월요일이 공휴일이어도 보낸다.
