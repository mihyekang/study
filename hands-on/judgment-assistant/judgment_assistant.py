# -*- coding: utf-8 -*-
"""
업무 판단 어시스턴트 (CLI)
==========================
아키텍처 타당성, 연동 규격의 목적, 설계 트레이드오프 등 반복적인 기술 판단을
구조화된 근거·확신도와 함께 신속히 제시하는 REPL 도구.

시스템 프롬프트: ../../prompts/4.work-judgment-assistant.md
실행: python judgment_assistant.py
"""
import os
import sys
from pathlib import Path

import anthropic

CLAUDE_MODEL = os.getenv("JUDGMENT_ASSISTANT_MODEL", "claude-opus-4-8")
SYSTEM_PROMPT_PATH = (
    Path(__file__).parent.parent.parent / "prompts" / "4.work-judgment-assistant.md"
)


def find_env_file() -> Path | None:
    """현재 스크립트 위치에서 상위로 올라가며 .env 파일을 탐색한다."""
    here = Path(__file__).resolve().parent
    for directory in [here, *here.parents]:
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
    return None


def load_api_key() -> str:
    """ANTHROPIC_API_KEY를 환경변수 → .env 순으로 로딩한다.
    .env는 'KEY=value'와 'KEY: value' 두 구분자를 모두 허용한다.
    """
    key = os.getenv("ANTHROPIC_API_KEY")
    if key:
        return key.strip()

    env_path = find_env_file()
    if env_path:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            for sep in ("=", ":"):
                if sep in line:
                    name, _, value = line.partition(sep)
                    if name.strip() == "ANTHROPIC_API_KEY":
                        return value.strip()
    return ""


def load_system_prompt() -> str:
    """prompts/4.work-judgment-assistant.md에서 [역할] 이후를 시스템 프롬프트로 변환."""
    if not SYSTEM_PROMPT_PATH.exists():
        raise FileNotFoundError(f"시스템 프롬프트 파일 없음: {SYSTEM_PROMPT_PATH}")
    content = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

    role_start = content.find("[역할]")
    return content[role_start:] if role_start != -1 else content


def ask(client: "anthropic.Anthropic", system_prompt: str, question: str) -> str:
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": question}],
    )
    return message.content[0].text


def main() -> int:
    try:
        system_prompt = load_system_prompt()
    except FileNotFoundError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    api_key = load_api_key()
    if not api_key:
        print(
            "오류: ANTHROPIC_API_KEY가 설정되지 않았습니다.\n"
            "환경변수로 설정하거나 hands-on/.env 파일에 'ANTHROPIC_API_KEY=sk-ant-...'를 추가하세요.",
            file=sys.stderr,
        )
        return 1

    client = anthropic.Anthropic(api_key=api_key)

    # 명령줄 인자로 한 번만 질문하는 모드
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        try:
            print(ask(client, system_prompt, question))
        except anthropic.APIError as exc:
            print(f"오류: Claude API 호출 실패 — {exc}", file=sys.stderr)
            return 1
        return 0

    # 대화형 REPL 모드
    print(f"업무 판단 어시스턴트 (모델: {CLAUDE_MODEL})")
    print("판단이 필요한 상황을 입력하세요. 종료하려면 'exit' 또는 Ctrl+C.\n")
    while True:
        try:
            question = input("판단 요청> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료합니다.")
            return 0
        if not question:
            continue
        if question.lower() in {"exit", "quit"}:
            print("종료합니다.")
            return 0
        print()
        try:
            print(ask(client, system_prompt, question))
        except anthropic.APIError as exc:
            print(f"오류: Claude API 호출 실패 — {exc}", file=sys.stderr)
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
