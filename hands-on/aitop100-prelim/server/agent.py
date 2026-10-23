"""
Claude 호출 모듈 - 사용자별 대화 이력(메모리) + 타임아웃·오류 시 대체 메시지

- 시스템 프롬프트: system_prompt.md (당일 과제에 맞게 교체하는 지점, 서버 재시작 시 반영)
- reply()는 어떤 경우에도 예외를 던지지 않고 문자열을 반환
"""
import os
import re
import time
import asyncio
import logging
from collections import OrderedDict, deque
from pathlib import Path

import anthropic

logger = logging.getLogger("agent")

# 환경변수는 BOT_ 접두어 사용: Claude Code 터미널이 CLAUDE_EFFORT 등을 자체 설정하므로 충돌 방지
CLAUDE_MODEL = os.getenv("BOT_MODEL", "claude-opus-5-5")
# Claude Opus 5.5는 thinking 비활성화 불가 → effort로 지연시간 제어 (low가 가장 빠름)
CLAUDE_EFFORT = os.getenv("BOT_EFFORT", "low")
# thinking 토큰도 max_tokens에 포함됨. 응답 1000자 제한(가정) 기준으로 여유 있게 설정
MAX_TOKENS = int(os.getenv("BOT_MAX_TOKENS", "2048"))
# 근거: 카카오 i 오픈빌더 스킬 응답 제한 5초 (대회용 봇 API 기준은 미확인)
REPLY_TIMEOUT_SEC = float(os.getenv("BOT_REPLY_TIMEOUT_SEC", "4.5"))
HISTORY_TURNS = int(os.getenv("BOT_HISTORY_TURNS", "10"))   # 사용자별 최근 N턴(질문+답변 쌍)만 유지
MAX_USERS = int(os.getenv("BOT_MAX_USERS", "1000"))         # 메모리 보호: 초과 시 오래된 사용자 이력부터 삭제
# 안전 분류기 거절(refusal) 시 서버 측에서 다른 모델로 재시도. 이 옵션으로 400 오류 발생 시 off로 끔
USE_FALLBACKS = os.getenv("BOT_FALLBACKS", "on").strip().lower() != "off"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT_PATH = Path(__file__).parent / "system_prompt.md"

MSG_EMPTY = "메시지를 입력해 주세요."
MSG_TIMEOUT = "답변 생성이 지연되고 있습니다. 잠시 후 다시 질문해 주세요."
MSG_ERROR = "일시적인 오류로 답변하지 못했습니다. 잠시 후 다시 시도해 주세요."
MSG_REFUSAL = "죄송합니다. 해당 요청에는 답변드리기 어렵습니다."

# 사용자ID → 최근 메시지 deque (user/assistant 쌍 단위로 추가하므로 첫 메시지는 항상 user)
_histories: "OrderedDict[str, deque]" = OrderedDict()
_client: anthropic.AsyncAnthropic | None = None
_system_prompt: str | None = None


def load_system_prompt() -> str:
    """system_prompt.md 로드. HTML 주석(<!-- -->)은 작성자 메모로 보고 제거."""
    content = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->", "", content, flags=re.S).strip()


def _get_system_prompt() -> str:
    global _system_prompt
    if _system_prompt is None:
        _system_prompt = load_system_prompt()
    return _system_prompt


def _get_client() -> anthropic.AsyncAnthropic:
    # 자격증명은 SDK가 환경변수(ANTHROPIC_API_KEY 등)에서 해석
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


def _get_history(user_id: str) -> deque:
    history = _histories.get(user_id)
    if history is None:
        history = deque(maxlen=HISTORY_TURNS * 2)
        _histories[user_id] = history
        while len(_histories) > MAX_USERS:
            _histories.popitem(last=False)
    else:
        _histories.move_to_end(user_id)
    return history


def reset_history(user_id: str | None = None) -> None:
    """특정 사용자(또는 전체) 대화 이력 삭제."""
    if user_id is None:
        _histories.clear()
    else:
        _histories.pop(user_id, None)


async def call_claude(messages: list[dict]) -> str | None:
    """Claude 호출 → 답변 텍스트. 거절(refusal)이면 None, 텍스트 없음은 예외."""
    kwargs = dict(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=_get_system_prompt(),
        messages=messages,
        output_config={"effort": CLAUDE_EFFORT},
        cache_control={"type": "ephemeral"},  # 자동 캐싱: 반복되는 시스템 프롬프트·이력 비용/지연 절감
    )
    client = _get_client()
    if USE_FALLBACKS:
        response = await client.beta.messages.create(
            betas=[FALLBACK_BETA], fallbacks="default", **kwargs
        )
    else:
        response = await client.messages.create(**kwargs)

    if response.stop_reason == "refusal":
        logger.warning("Claude 거절 | stop_details=%s", response.stop_details)
        return None

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise RuntimeError(f"응답 텍스트 없음 (stop_reason={response.stop_reason})")
    return text


async def reply(user_id: str, text: str) -> str:
    """사용자 발화 → 답변 텍스트. 타임아웃·오류 시 대체 메시지 반환 (예외 없음)."""
    text = (text or "").strip()
    if not text:
        return MSG_EMPTY

    # 사용자ID를 알 수 없으면 이력 공유를 막기 위해 단발성 대화로 처리
    history = _get_history(user_id) if user_id else deque()
    # 이력에는 text만 저장 (thinking 블록 미재전송 → 이력 잘라내기 시 prefix 불일치 오류 방지)
    messages = list(history) + [{"role": "user", "content": text}]

    started = time.monotonic()
    try:
        # SDK 재시도를 포함한 전체 소요시간 상한을 강제
        answer = await asyncio.wait_for(call_claude(messages), timeout=REPLY_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        logger.warning("Claude 타임아웃 | %.2fs 초과 | user=%s", REPLY_TIMEOUT_SEC, user_id)
        return MSG_TIMEOUT
    except Exception as e:
        logger.error("Claude 호출 오류 | %s: %s | user=%s", type(e).__name__, e, user_id)
        return MSG_ERROR

    if answer is None:
        return MSG_REFUSAL  # 거절된 질문은 이력에 남기지 않음

    elapsed = time.monotonic() - started
    logger.info("Claude 응답 | %.2fs | user=%s | %d자", elapsed, user_id, len(answer))
    if user_id:
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": answer})
    return answer
