"""
카카오톡 봇 요청/응답 어댑터

※ 대회용 봇 API 형식 미확인 — 당일 문서 확인 후 이 파일만 수정
   - developers.kakao.com/docs/ko/bot/* 문서는 참가자 계정 로그인 후 열람 가능(사전 확인 불가)
   - 아래 기본 구현은 카카오 i 오픈빌더 스킬 v2.0 형식을 "가정"한 것임
     요청: {"userRequest": {"utterance": "...", "user": {"id": "..."}}}
     응답: {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "..."}}]}}
   - 형식이 다르면 parse_incoming / build_reply 두 함수만 수정
     (실제 수신 형식은 logs/payloads.jsonl에서 확인)
"""
from typing import Any

# 가정: 오픈빌더 simpleText 텍스트 최대 1000자 (대회 API 기준 미확인)
MAX_REPLY_CHARS = 1000

# 알 수 없는 형식일 때 발화 텍스트를 찾을 키 (앞쪽이 우선)
TEXT_KEYS = ("utterance", "text", "message", "content", "msg", "query", "question", "input", "prompt")
# 알 수 없는 형식일 때 사용자 ID를 찾을 키 (앞쪽이 우선)
USER_KEYS = (
    "botUserKey", "plusfriendUserKey", "appUserId",
    "user_id", "userId", "userKey", "user_key", "sender_id", "senderId",
)
MAX_SEARCH_DEPTH = 5


def _find_value(obj: Any, key: str, depth: int = 0) -> str:
    """중첩 dict/list에서 key에 해당하는 첫 번째 문자열(또는 숫자) 값 탐색. 없으면 ""."""
    if depth > MAX_SEARCH_DEPTH:
        return ""
    if isinstance(obj, dict):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return ""
    for child in children:
        found = _find_value(child, key, depth + 1)
        if found:
            return found
    return ""


def _find_first(obj: Any, keys: tuple[str, ...]) -> str:
    for key in keys:
        found = _find_value(obj, key)
        if found:
            return found
    return ""


def parse_incoming(body: Any) -> tuple[str, str]:
    """요청 바디 → (사용자ID, 발화 텍스트). 찾지 못한 값은 ""."""
    if isinstance(body, str):
        # JSON이 아닌 평문 바디는 그대로 발화로 간주
        return "", body.strip()
    if not isinstance(body, (dict, list)):
        return "", ""

    user_id, text = "", ""

    # 1) 오픈빌더 스킬 v2.0 형식 (가정)
    user_request = body.get("userRequest") if isinstance(body, dict) else None
    if isinstance(user_request, dict):
        utterance = user_request.get("utterance")
        if isinstance(utterance, str):
            text = utterance.strip()
        user = user_request.get("user")
        if isinstance(user, dict):
            uid = user.get("id")
            if isinstance(uid, (str, int)) and str(uid).strip():
                user_id = str(uid)

    # 2) 방어적 파싱: 흔한 키를 중첩 구조 전체에서 탐색
    if not text:
        text = _find_first(body, TEXT_KEYS).strip()
    if not user_id:
        user_id = _find_first(body, USER_KEYS)
    if not user_id and isinstance(body, dict):
        user = body.get("user") or body.get("sender") or body.get("from")
        if isinstance(user, dict):
            user_id = _find_value(user, "id")
        elif isinstance(user, (str, int)) and not isinstance(user, bool):
            user_id = str(user)

    return user_id.strip(), text


def build_reply(text: str) -> dict:
    """답변 텍스트 → 카카오 응답 JSON (오픈빌더 스킬 v2.0 simpleText 형식 가정)."""
    text = (text or "").strip() or "답변을 생성하지 못했습니다."
    if len(text) > MAX_REPLY_CHARS:
        text = text[: MAX_REPLY_CHARS - 1] + "…"
    return {
        "version": "2.0",
        "template": {"outputs": [{"simpleText": {"text": text}}]},
    }
