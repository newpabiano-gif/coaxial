"""수집 -> 본문 작성 -> Gmail 발송. 실행 진입점.

    python send.py            # 수집해서 메일 발송
    python send.py --dry-run  # 메일 대신 화면에 출력

필요한 환경변수(.env)
    GMAIL_ADDRESS       보내는 Gmail 주소
    GMAIL_APP_PASSWORD  Gmail 앱 비밀번호 (2단계 인증 계정에서 발급)
    MAIL_TO             받는 주소. 비우면 GMAIL_ADDRESS 로 보낸다
    DART_API_KEY        OpenDART 인증키. 비우면 교차 확인을 건너뛴다
"""

import argparse
import logging
import os
import smtplib
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

import format as fmt
from collect import CollectError, collect

KST = ZoneInfo("Asia/Seoul")
HERE = Path(__file__).resolve().parent
LOG_DIR = HERE / "logs"
MAX_RETRIES = 3  # 첫 시도 후 재시도 횟수
RETRY_WAIT_SEC = 30

log = logging.getLogger("ipo-alert")


def setup_logging(today_str: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / f"{today_str}.log", encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def send_mail(subject: str, body: str) -> None:
    # 붙여넣기할 때 섞이기 쉬운 공백과 줄바꿈을 없앤다. 앱 비밀번호는 4자리씩 띄어 보여 준다.
    sender = os.environ["GMAIL_ADDRESS"].strip()
    password = "".join(os.environ["GMAIL_APP_PASSWORD"].split())
    to = (os.environ.get("MAIL_TO") or "").strip() or sender
    log.info("보내는 주소 형식 %s, 앱 비밀번호 %d자", _mask(sender), len(password))

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)
    log.info("메일 발송 완료: %s", subject)


def _mask(address: str) -> str:
    """로그에는 주소 대신 형식만 남긴다. 예: ***@gmail.com"""
    return "***@" + address.split("@")[-1] if "@" in address else "@ 없음"


def collect_with_retry(today, dart_api_key):
    last_error = None
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            return collect(today, dart_api_key)
        except (requests.RequestException, CollectError) as e:
            last_error = e
            log.exception("수집 실패 (%d/%d)", attempt, MAX_RETRIES + 1)
            if attempt <= MAX_RETRIES:
                time.sleep(RETRY_WAIT_SEC)
    raise CollectError(_reason(last_error)) from last_error


def _reason(e: Exception) -> str:
    if isinstance(e, CollectError):
        return str(e)
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"{e.response.url} 응답 코드 {e.response.status_code}"
    return f"{type(e).__name__}: {e}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="메일을 보내지 않고 출력만")
    args = parser.parse_args(argv)

    load_dotenv(HERE / ".env")
    today = datetime.now(KST).date()
    setup_logging(today.isoformat())

    try:
        result = collect_with_retry(today, os.environ.get("DART_API_KEY"))
        subject, body = fmt.subject(today), fmt.body(result)
        log.info("수집 완료: %d개 종목", len(result.offerings))
    except CollectError as e:
        subject, body = fmt.failure_subject(today), fmt.failure_body(str(e))
    except Exception as e:  # 예상 못 한 오류도 실패 메일로 알린다
        log.exception("예상하지 못한 오류")
        subject, body = fmt.failure_subject(today), fmt.failure_body(f"{type(e).__name__}: {e}")

    if args.dry_run:
        print(subject)
        print()
        print(body)
        return 0

    try:
        send_mail(subject, body)
    except Exception:
        log.exception("메일 발송 실패")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
