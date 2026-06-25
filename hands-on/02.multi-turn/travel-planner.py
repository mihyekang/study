# -*- coding: utf-8 -*-
"""
멀티턴 여행 플래너 (Streamlit + Groq LPU / openai-gpt-oss-120b)
================================================================

학습 목표
- 멀티턴(Multi-turn) 대화로 여행 도시 → 일정(N박 M일)을 점진적으로 수집
- '슬라이딩 윈도우 + 요약' 기법으로 클라이언트 측 컨텍스트를 효율 관리
  - WINDOW_SIZE = 3 : 최근 유지할 대화 '턴'(user+assistant 쌍) 수
  - 윈도우를 벗어난 오래된 턴은 LLM이 요약하여 system 프롬프트에 고정(pin)

정직성 설계
- 실시간 교통 시간표 / TripAdvisor 평점 전용 API 키가 없으므로,
  LLM이 시간표·수치를 '지어내지' 않도록 시스템 프롬프트로 강제하고,
  장소/맛집/정류장은 항상 동작하는 '공식 검색 링크'를 코드가 직접 생성한다.
"""

import os
import re
import urllib.parse
from pathlib import Path

import streamlit as st
from groq import Groq

# ──────────────────────────────────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────────────────────────────────
WINDOW_SIZE = 3  # 슬라이딩 윈도우 크기: 최근 유지할 대화 턴(user+assistant 쌍) 수
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")          # 본문 생성 모델
SUMMARY_MODEL = os.getenv("GROQ_SUMMARY_MODEL", MODEL)          # 요약 모델

# 시스템 프롬프트 (여행 조건과 이전 대화 요약은 호출 시 동적으로 합쳐 고정)
BASE_SYSTEM_PROMPT = """너는 전문 여행 일정 플래너야. 사용자의 여행 도시와 기간에 맞춘 현실적이고 균형 잡힌 일정을 한국어로 작성해.

[여행 조건] (대화 내내 반드시 기억)
- 도시: {city}
- 기간: {duration}

[일정 작성 규칙]
- 날짜별로 09:00 ~ 18:00 시간대를 시간 순서로 구성한다.
- 매일 '아침/점심/저녁' 식사 맛집을 각 1곳씩 추천한다. (TripAdvisor에서 평이 좋은 곳 위주)
- 각 일정 항목에 이동 동선과 대략 소요시간을 간단히 표기한다.
- 각 날짜 끝에 '🚇 대중교통 안내' 섹션을 두고, 주요 관광지 인근의 지하철/기차/버스 정류장 '이름'을 안내한다.

[링크 규칙 - 매우 중요]
- 관광지 이름은 반드시  [[place|관광지이름]]  형식으로 표기한다.
- 맛집(식당) 이름은 반드시  [[food|맛집이름]]  형식으로 표기한다.
- 교통 정류장(역/터미널) 이름은 반드시  [[transit|정류장이름]]  형식으로 표기한다.
- 위 마커는 시스템이 실제 '검색 링크'로 자동 변환하므로, 너는 URL을 직접 만들지 마라.
- 마커 안의 이름에 '|' 또는 ']' 문자를 넣지 마라. (링크 변환이 깨질 수 있음)

[정직성 규칙 - 필수]
- 실시간 교통 시간표, 정확한 운행 시각, 실시간 평점/리뷰 수치는 너가 알 수 없다. 절대 지어내지 마라.
- 평점·리뷰 수를 숫자(예: 4.7점, 1234개)로 적지 마라. 평점은 "TripAdvisor 링크에서 확인"으로만 안내한다.
- 시간표가 필요하면 "실시간 시간표는 위 교통 링크에서 확인하세요"라고 안내만 한다.
- 확실하지 않은 영업시간/요금/휴무일은 "방문 전 공식 정보 확인 필요"로 표기한다.

[대화 규칙]
- 사용자가 일정 수정을 요청하면, 기존 일정의 맥락을 유지하면서 요청한 부분만 자연스럽게 반영한다.
- 답변은 마크다운으로 가독성 있게 작성한다.
"""


# ──────────────────────────────────────────────────────────────────────────
# API Key 로딩  (hands-on/.env 가 'KEY: value' 콜론 형식이어도 파싱)
# ──────────────────────────────────────────────────────────────────────────
def find_env_file():
    """현재 스크립트 위치에서 상위로 올라가며 .env 파일을 탐색한다."""
    here = Path(__file__).resolve().parent
    for directory in [here, *here.parents]:
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
    return None


def load_groq_api_key():
    """GROQ_API_KEY를 환경변수 → .env 순으로 로딩한다.
    .env는 'KEY=value' 와 'KEY: value' 두 구분자를 모두 허용한다.
    """
    key = os.getenv("GROQ_API_KEY")
    if key:
        return key.strip()

    env_path = find_env_file()
    if not env_path:
        return None

    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"GROQ_API_KEY\s*[:=]\s*(.+)", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return None


# ──────────────────────────────────────────────────────────────────────────
# 검색 링크 생성  (장소/맛집/정류장 → 항상 동작하는 공식 검색 URL)
# ──────────────────────────────────────────────────────────────────────────
def gmaps_link(query: str) -> str:
    """Google 지도 검색 링크."""
    return f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(query)}"


def tripadvisor_link(query: str) -> str:
    """TripAdvisor 검색 링크 (실제 평점/리뷰는 링크에서 확인)."""
    return f"https://www.tripadvisor.com/Search?q={urllib.parse.quote(query)}"


def linkify(text: str, city: str) -> str:
    """LLM이 출력한 [[place|..]] / [[food|..]] / [[transit|..]] 마커를
    실제 검색 링크가 달린 마크다운으로 치환한다. (URL은 코드가 생성 → 환각 방지)
    """
    city = (city or "").strip()

    def repl(m: "re.Match") -> str:
        kind = m.group(1)
        name = m.group(2).strip()
        query = f"{name} {city}".strip()
        if kind == "food":
            url = tripadvisor_link(query)
        elif kind == "transit":
            url = gmaps_link(f"{name} 역 {city}".strip())
        else:  # place
            url = gmaps_link(query)
        return f"[{name}]({url})"

    # 비탐욕 매칭으로 이름에 '|', ']'가 일부 섞여도 가능한 한 견고하게 치환
    return re.sub(r"\[\[(place|food|transit)\|(.+?)\]\]", repl, text)


def strip_model_urls(text: str) -> str:
    """모델이 직접 만든 링크/URL을 제거한다. (정직성: 링크는 코드만 생성)
    반드시 linkify() '이전'에 호출되어야 우리가 생성한 검색 링크가 보존된다.
    """
    # 마크다운 링크 [텍스트](http...) → 텍스트만 남김
    text = re.sub(r"\[([^\]]+)\]\(\s*https?://[^)]+\)", r"\1", text)
    # 남은 평문 URL → 안내 문구로 대체 (모델이 지어낸 링크 무력화)
    text = re.sub(r"https?://\S+", "🔎(위 검색 링크 이용)", text)
    return text


def render_text(text: str, city: str) -> str:
    """모델 원문을 표시용으로 변환: 모델 URL 제거 후 마커를 검색 링크로 치환."""
    return linkify(strip_model_urls(text), city)


# ──────────────────────────────────────────────────────────────────────────
# 입력 파싱
# ──────────────────────────────────────────────────────────────────────────
def parse_duration(text: str):
    """'3박 4일', '2박3일', '5일', '당일' 등에서 (일수, 원문)을 추출한다."""
    text = text.strip()
    m = re.search(r"(\d+)\s*박\s*(\d+)\s*일", text)
    if m:
        return int(m.group(2)), text
    m = re.search(r"(\d+)\s*일", text)
    if m:
        return int(m.group(1)), text
    if "당일" in text:
        return 1, text
    m = re.search(r"(\d+)", text)
    if m:
        return int(m.group(1)), text
    return None, text


# ──────────────────────────────────────────────────────────────────────────
# 컨텍스트 관리  (슬라이딩 윈도우 + 요약)
# ──────────────────────────────────────────────────────────────────────────
def build_messages(city, duration, summary, turns, user_content):
    """system(여행조건 + 요약 고정) + 최근 WINDOW_SIZE 턴 + 이번 입력으로
    API 호출용 messages 배열을 구성한다.
    """
    system_content = BASE_SYSTEM_PROMPT.format(city=city, duration=duration)
    if summary:
        system_content += f"\n\n## 이전 대화 요약 (윈도우를 벗어난 오래된 맥락)\n{summary}"

    messages = [{"role": "system", "content": system_content}]
    for turn in turns[-WINDOW_SIZE:]:  # 최근 N턴만 윈도우에 유지
        messages.append({"role": "user", "content": turn["user"]})
        messages.append({"role": "assistant", "content": turn["assistant"]})
    messages.append({"role": "user", "content": user_content})
    return messages


def summarize_overflow(client, prev_summary, turn):
    """윈도우를 벗어난 오래된 1개 턴을 기존 요약과 합쳐 갱신한다."""
    convo = f"사용자: {turn['user']}\n어시스턴트: {turn['assistant']}"
    prompt = (
        f"[기존 요약]\n{prev_summary or '(없음)'}\n\n"
        f"[새로 추가할 오래된 대화]\n{convo}\n\n"
        "위 둘을 합쳐 한국어로 핵심만 갱신한 요약을 작성하라. "
        "확정·변경된 일정, 사용자 선호/제약, 도시·기간을 보존하고 5줄 이내로 압축하라."
    )
    resp = client.chat.completions.create(
        model=SUMMARY_MODEL,
        messages=[
            {"role": "system", "content": "너는 대화 내용을 손실 없이 압축하는 요약기다."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=400,
    )
    return resp.choices[0].message.content.strip()


# ──────────────────────────────────────────────────────────────────────────
# Streamlit UI
# ──────────────────────────────────────────────────────────────────────────
def render_app():
    st.set_page_config(page_title="멀티턴 여행 플래너", page_icon="🧳", layout="centered")
    st.title("🧳 멀티턴 여행 플래너")
    st.caption("Groq LPU · openai/gpt-oss-120b · 슬라이딩 윈도우(3) + 요약")

    # ── API Key / Client 초기화 (MUST: Client 객체를 session_state에 저장·유지) ──
    if "client" not in st.session_state:
        api_key = load_groq_api_key()
        if not api_key:
            st.error("GROQ_API_KEY를 찾을 수 없습니다. hands-on/.env 파일을 확인하세요.")
            st.stop()
        st.session_state.client = Groq(api_key=api_key)

    # ── 대화 상태 초기화 ──
    if "history" not in st.session_state:
        st.session_state.history = [{
            "role": "assistant",
            "content": "안녕하세요! 여행 일정을 만들어 드릴게요. 🗺️\n\n"
                       "먼저, **어느 도시로 여행**을 떠나시나요? (예: 도쿄, 파리, 부산)",
        }]
        st.session_state.stage = "city"   # city → duration → chat
        st.session_state.city = ""
        st.session_state.duration_text = ""
        st.session_state.days = None
        st.session_state.turns = []       # 윈도우용: [{"user":..,"assistant":..(raw)}]
        st.session_state.summary = ""     # 윈도우 밖 오래된 맥락 요약

    # ── 사이드바: 컨텍스트 상태 시각화 (멀티턴/슬라이딩 윈도우 동작 확인용) ──
    with st.sidebar:
        st.subheader("🧠 컨텍스트 상태")
        st.markdown(f"- **도시**: {st.session_state.city or '미정'}")
        st.markdown(f"- **기간**: {st.session_state.duration_text or '미정'}")
        st.markdown(f"- **윈도우 크기**: {WINDOW_SIZE} 턴")
        st.markdown(f"- **현재 윈도우 보관 턴**: {len(st.session_state.turns[-WINDOW_SIZE:])}")
        st.markdown(f"- **누적 대화 턴**: {len(st.session_state.turns)}")
        with st.expander("이전 대화 요약 (윈도우 밖)"):
            st.markdown(st.session_state.summary or "(아직 요약 없음)")
        if st.button("🔄 대화 초기화"):
            for k in ["history", "stage", "city", "duration_text", "days", "turns", "summary"]:
                st.session_state.pop(k, None)
            st.rerun()  # 초기화 버튼은 명시적 재실행 (입력 처리 흐름과 무관)

    # ── 기존 대화 렌더링 ──
    for msg in st.session_state.history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # ── 입력 처리 (입력 후 st.rerun() 호출 금지 → 같은 실행 내에서 렌더) ──
    prompt = st.chat_input("메시지를 입력하세요...")
    if not prompt:
        return

    # 사용자 메시지 표시 + 기록
    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.history.append({"role": "user", "content": prompt})

    stage = st.session_state.stage

    # [1단계] 도시 수집
    if stage == "city":
        city = prompt.strip()
        if not city:
            reply = "도시 이름을 입력해 주세요. (예: 도쿄, 파리, 부산)"
            with st.chat_message("assistant"):
                st.markdown(reply)
            st.session_state.history.append({"role": "assistant", "content": reply})
            return
        st.session_state.city = city
        st.session_state.stage = "duration"
        reply = (f"**{city}** 좋네요! 🎉\n\n"
                 "이번 여행은 **몇 박 며칠** 일정인가요? (예: 2박 3일, 당일치기)")
        with st.chat_message("assistant"):
            st.markdown(reply)
        st.session_state.history.append({"role": "assistant", "content": reply})
        return

    # [2단계] 기간 수집 → 즉시 전체 일정 생성
    if stage == "duration":
        days, duration_text = parse_duration(prompt)
        if days is None:  # 숫자/기간을 못 읽으면 재질의
            reply = "여행 일수를 숫자로 알려주세요. (예: 2박 3일, 3일, 당일치기)"
            with st.chat_message("assistant"):
                st.markdown(reply)
            st.session_state.history.append({"role": "assistant", "content": reply})
            return
        st.session_state.duration_text = duration_text
        st.session_state.days = days

        user_content = (
            f"{st.session_state.city} {duration_text} 여행 일정을 만들어줘. "
            f"총 {days}일 동안 매일 09:00~18:00 일정과 아침·점심·저녁 맛집, "
            "그리고 주요 관광지 인근 대중교통 안내를 포함해줘."
        )
        ok = _generate_and_render(user_content)
        # 성공 시에만 자유 수정 단계로 전환 (실패 시 기간 재입력으로 재시도)
        st.session_state.stage = "chat" if ok else "duration"
        return

    # [3단계] 자유 멀티턴 (일정 수정/추가 질의)
    _generate_and_render(prompt)


def _generate_and_render(user_content: str) -> bool:
    """LLM 호출(스트리밍) → 링크 변환 렌더 → 턴 기록 → 윈도우 초과분 요약.
    성공 시 True, LLM 호출 실패 시 False 반환.
    """
    client = st.session_state.client
    city = st.session_state.city
    messages = build_messages(
        city=city,
        duration=st.session_state.duration_text,
        summary=st.session_state.summary,
        turns=st.session_state.turns,
        user_content=user_content,
    )

    with st.chat_message("assistant"):
        placeholder = st.empty()  # MUST: st.empty() + placeholder.markdown() 스트리밍
        full = ""
        try:
            stream = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=0.7,
                max_tokens=2600,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    full += delta
                    placeholder.markdown(render_text(full, city) + " ▌")
            placeholder.markdown(render_text(full, city))
        except Exception as e:  # noqa: BLE001 - 사용자에게 오류를 명확히 표시
            err = f"⚠️ 일정 생성 중 오류가 발생했습니다: {e}"
            placeholder.markdown(err)
            st.session_state.history.append({"role": "assistant", "content": err})
            return False

    # 표시용 히스토리(링크 변환본)와 컨텍스트용 턴(원본 마커 유지)을 분리 보관
    st.session_state.history.append({"role": "assistant", "content": render_text(full, city)})
    st.session_state.turns.append({"user": user_content, "assistant": full})

    # 슬라이딩 윈도우: 초과분을 '요약 성공 후 제거'로 흡수 (실패 시 턴 보존 → 다음에 재시도)
    while len(st.session_state.turns) > WINDOW_SIZE:
        old = st.session_state.turns[0]
        try:
            st.session_state.summary = summarize_overflow(
                client, st.session_state.summary, old
            )
        except Exception:  # noqa: BLE001 - 요약 실패 시 턴을 잃지 않도록 보존 후 중단
            st.warning("이전 대화 요약에 실패했습니다. 다음 응답 때 다시 시도합니다.")
            break
        st.session_state.turns.pop(0)
    return True


# ──────────────────────────────────────────────────────────────────────────
# 실행 진입점: Streamlit 런타임에서만 UI 실행 (테스트 import 시 비실행)
# ──────────────────────────────────────────────────────────────────────────
def _in_streamlit_runtime() -> bool:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:  # noqa: BLE001
        return False


if _in_streamlit_runtime():
    render_app()
