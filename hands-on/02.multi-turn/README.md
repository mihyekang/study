# 🧳 멀티턴 여행 플래너 (Multi-turn Travel Planner)

Groq LPU(`openai/gpt-oss-120b`) 기반 **멀티턴 대화형 여행 일정 플래너**.  
사용자와 여러 턴에 걸쳐 도시·기간을 수집하고, **슬라이딩 윈도우 + 요약** 기법으로  
대화 컨텍스트를 효율적으로 관리하면서 맞춤 일정을 생성함.

---

## 1. 앱 개요

### 1.1 목적

- 멀티턴(Multi-turn) 대화의 컨텍스트 유지 원리 학습용 실습 앱
- '슬라이딩 윈도우 + 요약' 클라이언트 측 컨텍스트 관리 기법 적용 예제

### 1.2 주요 기능

| 기능 | 설명 |
|------|------|
| **멀티턴 정보 수집** | ① 여행 도시 → ② 몇 박 며칠 일정을 대화로 순차 수집 |
| **AI 일정 생성** | Groq LPU + `openai/gpt-oss-120b`로 09:00~18:00 일별 일정 생성 |
| **식사 추천** | 매일 아침·점심·저녁 맛집을 각 1곳씩 추천 (TripAdvisor 검색 연결) |
| **대중교통 안내** | 주요 관광지 인근 지하철·기차·버스 정류장 안내 |
| **검색 링크 자동 생성** | 관광지/맛집/정류장을 **항상 동작하는 공식 검색 링크**로 변환 |
| **슬라이딩 윈도우+요약** | 최근 3턴만 유지하고 오래된 대화는 요약하여 토큰 효율화 |
| **컨텍스트 시각화** | 사이드바에서 도시·기간·윈도우·요약 상태를 실시간 확인 |
| **실시간 스트리밍** | AI 응답을 토큰 단위로 스트리밍 표시 |

### 1.3 정직성 설계 (환각 방지)

- 실시간 교통 시간표·평점 전용 API 키가 없으므로, **LLM이 시간표·평점·URL을 지어내지 못하도록** 시스템 프롬프트로 강제함
- 장소·맛집·정류장 링크는 **LLM이 아닌 코드가 직접 생성**(Google 지도/TripAdvisor 검색 URL)하여 깨진 링크·허위 링크를 원천 차단
- 정확한 운행 시각·영업시간·요금은 "현장/공식 정보 확인 필요"로 안내

---

## 2. 주요 소스 설명 (`travel-planner.py`)

### 2.1 컨텍스트 관리 (멀티턴 핵심)

| 함수 / 구성 | 역할 |
|-------------|------|
| `WINDOW_SIZE = 3` | 슬라이딩 윈도우 크기. 최근 유지할 대화 '턴'(user+assistant 쌍) 수 |
| `BASE_SYSTEM_PROMPT` | 여행 조건·링크 규칙·정직성 규칙을 정의한 시스템 프롬프트 |
| `build_messages()` | system(여행조건+요약 **고정**) + 최근 3턴 + 현재 입력으로 API 메시지 구성 |
| `summarize_overflow()` | 윈도우를 벗어난 오래된 턴을 LLM으로 요약해 누적 |
| `_generate_and_render()` | LLM 스트리밍 호출 → 링크 변환 렌더 → 턴 기록 → 초과분 요약 |

> 시스템 프롬프트의 **여행 조건(도시·기간)과 요약본은 윈도우 밖에 고정(pin)** 되므로,  
> 대화가 길어져 최근 3턴만 남아도 핵심 맥락을 잃지 않음.

### 2.2 검색 링크 생성 (환각 방지 핵심)

| 함수 | 역할 |
|------|------|
| `gmaps_link()` | Google 지도 검색 URL 생성 (관광지·정류장용) |
| `tripadvisor_link()` | TripAdvisor 검색 URL 생성 (맛집용) |
| `linkify()` | LLM이 출력한 `[[place\|..]]` / `[[food\|..]]` / `[[transit\|..]]` 마커를 **코드가 만든 검색 링크**로 치환 |
| `strip_model_urls()` | LLM이 임의로 만든 링크·평문 URL을 **제거**(환각 링크 차단). `linkify()` 직전 실행 |
| `render_text()` | `strip_model_urls()` → `linkify()` 순서로 표시용 텍스트 생성 |

> LLM은 URL을 직접 만들지 않고 **마커만** 출력함.  
> 실제 URL은 `linkify()`가 도시명과 결합해 생성하므로 항상 유효한 검색 결과로 연결됨.  
> LLM이 규칙을 어기고 임의 URL을 출력해도 `strip_model_urls()`가 제거하여 환각 링크를 원천 차단함.

### 2.3 API Key 로딩

| 함수 | 역할 |
|------|------|
| `find_env_file()` | 스크립트 상위 경로로 올라가며 `.env` 탐색 |
| `load_groq_api_key()` | 환경변수 → `.env` 순으로 키 로딩. `KEY=value`·`KEY: value` **두 구분자 모두 지원** |

### 2.4 UI / 입력

| 함수 | 역할 |
|------|------|
| `render_app()` | Streamlit UI 본체. session_state 초기화, 사이드바, 대화 렌더, 단계별 입력 처리 |
| `parse_duration()` | `3박 4일`·`당일치기` 등에서 일수 추출 |

대화 단계: **`city`(도시 수집) → `duration`(기간 수집·즉시 일정 생성) → `chat`(자유 수정)**

---

## 3. 가상환경 설정 방법

> Windows PowerShell 기준. (GitBash/macOS/Linux는 활성화 명령만 다름)

```powershell
# 1) 프로젝트 디렉터리로 이동
cd hands-on\02.multi-turn

# 2) 가상환경 생성
python -m venv venv

# 3) 가상환경 활성화 (PowerShell)
venv\Scripts\Activate.ps1
#   GitBash:        source venv/Scripts/activate
#   macOS / Linux:  source venv/bin/activate

# 4) 의존성 설치
pip install -r requirements.txt
```

### API Key 설정

`hands-on/.env` 파일에 Groq API Key 설정. (콜론·등호 구분자 모두 인식)

```
GROQ_API_KEY: gsk_xxxxxxxxxxxxxxxxxxxx
```

> Groq API Key는 <https://console.groq.com/> 에서 발급.

---

## 4. 실행 방법

```powershell
# 가상환경이 활성화된 상태에서
streamlit run travel-planner.py
```

- 실행 시 브라우저에서 `http://localhost:8501` 자동 오픈
- 채팅창에 **도시 입력 → 기간 입력** 순으로 진행하면 일정이 스트리밍 생성됨
- 이후 "2일차에 미술관 추가해줘"처럼 자유롭게 수정 요청 가능
- 사이드바에서 컨텍스트 상태(윈도우·요약)를 실시간 확인 가능

### 사용 예시 (대화 흐름)

```
🤖 어느 도시로 여행을 떠나시나요?
👤 도쿄
🤖 도쿄 좋네요! 몇 박 며칠 일정인가요?
👤 2박 3일
🤖 (1~3일차 09:00~18:00 일정 + 아침/점심/저녁 맛집 + 교통 안내 + 검색 링크 생성)
👤 2일차 저녁을 라멘 맛집으로 바꿔줘
🤖 (맥락 유지하며 2일차 저녁만 수정)
```

---

## 5. 기술 스택

| 구분 | 사용 기술 |
|------|-----------|
| 언어 | Python 3.13 |
| LLM | Groq LPU · `openai/gpt-oss-120b` |
| 컨텍스트 관리 | 슬라이딩 윈도우(size=3) + 요약 (클라이언트 측) |
| UI | Streamlit (chat elements, 스트리밍) |
| 의존성 | `streamlit`, `groq` (`requirements.txt` 참조) |
