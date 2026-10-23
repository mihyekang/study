"""
AI_TOP_100 [2026] 온라인 예선 - 카카오톡 챗봇 에이전트 서버 (FastAPI)
실행: uvicorn app:app --host 0.0.0.0 --port 8000

- GET  /health  : 상태·모델명·가동시간
- POST /webhook : 카카오 봇 요청 수신 → Claude 답변 → 카카오 응답 JSON
  * 원본 요청(헤더 일부+바디)을 logs/payloads.jsonl에 한 줄씩 기록 (당일 실제 요청 형식 확인용)
  * 어떤 예외에도 HTTP 500을 내지 않고 대체 응답 반환 (봇 무응답 방지)
"""
import sys
import os
import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔 한글 출력

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
# 환경변수 우선순위: OS 환경변수 > server/.env > hands-on/.env (load_dotenv는 기존 값을 덮어쓰지 않음)
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR.parent.parent / ".env")

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import agent
import kakao_adapter

logging.basicConfig(
    level=os.getenv("BOT_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("app")

LOG_DIR = Path(os.getenv("BOT_LOG_DIR", str(BASE_DIR / "logs")))
PAYLOAD_LOG = LOG_DIR / "payloads.jsonl"
MAX_LOGGED_BODY = 64 * 1024  # 기록할 바디 최대 바이트
# 값을 기록하지 않을 민감 헤더 (존재 여부만 "***"로 표시)
MASKED_HEADERS = {"authorization", "proxy-authorization", "cookie", "x-api-key"}
# 대체 응답 생성까지 실패할 때의 최후 응답 (오픈빌더 v2.0 형식 가정)
SAFE_REPLY = {
    "version": "2.0",
    "template": {"outputs": [{"simpleText": {"text": agent.MSG_ERROR}}]},
}

STARTED_AT = time.time()

# 기동 시점에 설정 확인 (system_prompt.md가 없으면 여기서 기동 실패 → 배포 단계에서 바로 발견)
logger.info(
    "설정 | model=%s effort=%s timeout=%.1fs fallbacks=%s | 시스템 프롬프트 %d자 | API 키 %s",
    agent.CLAUDE_MODEL, agent.CLAUDE_EFFORT, agent.REPLY_TIMEOUT_SEC, agent.USE_FALLBACKS,
    len(agent._get_system_prompt()), "설정됨" if os.getenv("ANTHROPIC_API_KEY") else "없음",
)

app = FastAPI(title="AI_TOP_100 예선 카카오톡 챗봇 에이전트", version="0.1.0")


def _parse_body(raw: bytes):
    """바디 → JSON 객체. JSON이 아니면 문자열, 비어 있으면 None."""
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def _log_payload(request: Request, raw: bytes, body) -> None:
    """수신 요청 1건을 JSONL 한 줄로 기록. 기록 실패는 응답에 영향 주지 않음."""
    try:
        headers = {
            k: ("***" if k.lower() in MASKED_HEADERS else v)
            for k, v in request.headers.items()
        }
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "method": request.method,
            "path": request.url.path,
            "query": str(request.url.query),
            "client": request.client.host if request.client else None,
            "headers": headers,
            "body": body if len(raw) <= MAX_LOGGED_BODY else raw[:MAX_LOGGED_BODY].decode("utf-8", "replace"),
            "body_bytes": len(raw),
        }
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with PAYLOAD_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error("payload 기록 실패 | %s: %s", type(e).__name__, e)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": agent.CLAUDE_MODEL,
        "effort": agent.CLAUDE_EFFORT,
        "timeout_sec": agent.REPLY_TIMEOUT_SEC,
        "uptime_sec": round(time.time() - STARTED_AT, 1),
        "started_at": datetime.fromtimestamp(STARTED_AT, timezone.utc).isoformat(),
        "api_key_set": bool(os.getenv("ANTHROPIC_API_KEY")),
    }


@app.post("/webhook")
async def webhook(request: Request):
    started = time.monotonic()
    user_id, answer = "", agent.MSG_ERROR
    try:
        raw = await request.body()
        body = _parse_body(raw)
        _log_payload(request, raw, body)

        # 1. 요청 파싱 (형식 의존부는 kakao_adapter에 격리)
        user_id, text = kakao_adapter.parse_incoming(body)
        # 2. Claude 답변 (타임아웃·오류 시 대체 메시지)
        answer = await agent.reply(user_id, text)
    except Exception:
        logger.exception("webhook 처리 실패")

    # 3. 응답 생성 — 실패해도 항상 HTTP 200 + 대체 응답
    try:
        payload = kakao_adapter.build_reply(answer)
    except Exception:
        logger.exception("응답 생성 실패")
        payload = SAFE_REPLY

    logger.info("webhook | %.2fs | user=%s", time.monotonic() - started, user_id or "-")
    return JSONResponse(payload)
