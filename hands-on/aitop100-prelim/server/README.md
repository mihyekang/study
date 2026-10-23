# AI_TOP_100 [2026] 예선 카카오톡 챗봇 서버

2026-10-31(토) 온라인 예선 제출용 카카오톡 챗봇 에이전트의 백엔드 서버 골격.  
FastAPI + Claude(`claude-opus-5-5`) 구성. 과제는 당일 공개되므로 어떤 과제든 붙일 수 있는 골격만 사전 준비함.

> **주의: 카카오 봇 API 형식은 미확인 상태임.**  
> 요청/응답 형식은 카카오 i 오픈빌더 스킬 v2.0을 "가정"하여 구현함.  
> 대회용 봇 API 문서(developers.kakao.com/docs/ko/bot/*)는 참가자 로그인 후에만 열람 가능하여 사전 확인 불가.  
> 당일 문서 확인 후 `kakao_adapter.py` 한 파일만 수정하는 구조임.

---

## 구성 파일

| 파일 | 설명 |  
|------|------|  
| `app.py` | FastAPI 서버. `GET /health`, `POST /webhook`. 수신 요청을 `logs/payloads.jsonl`에 기록 |  
| `kakao_adapter.py` | 카카오 요청 파싱(`parse_incoming`)·응답 생성(`build_reply`). **형식 의존부는 이 파일에만 존재** |  
| `agent.py` | Claude 호출. 사용자별 대화 이력(메모리, 최근 N턴), 타임아웃·오류 시 대체 메시지 |  
| `system_prompt.md` | 시스템 프롬프트(범용 placeholder). **당일 과제에 맞게 교체하는 지점** |  
| `test_app.py` | pytest. Claude 호출 mock 처리 후 엔드포인트·대체 응답 검증 |  
| `requirements.txt` | 의존성(버전 고정) |  
| `Dockerfile` | 컨테이너 이미지. `PORT` 환경변수 지원 |  
| `.env.example` | 환경변수 예시(자리표시자만 포함) |

---

## 실행 방법 (Windows PowerShell)

### 1. 가상환경 생성 및 의존성 설치

```powershell
cd hands-on\aitop100-prelim\server
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. API 키 설정

```powershell
Copy-Item .env.example .env
notepad .env   # ANTHROPIC_API_KEY=sk-ant-... 실제 값 기입
```

- 로드 우선순위: OS 환경변수 > `server/.env` > `hands-on/.env` (기존 sw-cost-qa와 같은 `hands-on/.env`도 사용 가능)  
- **`.env`·`venv/`·`logs/`는 커밋 금지.** 현재 저장소에 `.gitignore`가 없으므로 "push" 전 반드시 확인

### 3. 테스트 및 서버 실행

```powershell
pytest -q
uvicorn app:app --host 0.0.0.0 --port 8000
```

기동 로그에 `설정 | model=... effort=low timeout=4.5s ...` 한 줄이 출력되면 정상.

### 4. 동작 확인

```powershell
# 헬스 체크
Invoke-RestMethod http://localhost:8000/health

# 웹훅 (오픈빌더 형식 가정). 한글 깨짐 방지를 위해 UTF-8 바이트로 전송
$body = @{ userRequest = @{ utterance = "안녕"; user = @{ id = "u1" } } } | ConvertTo-Json -Depth 5
$bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
Invoke-RestMethod -Method POST -Uri http://localhost:8000/webhook `
  -Body $bytes -ContentType "application/json; charset=utf-8"
```

API 키가 없거나 Claude 호출이 실패해도 HTTP 200 + 대체 메시지가 반환되어야 정상.

---

## API 명세

### `GET /health`

```json
{
  "status": "ok",
  "model": "claude-opus-5-5",
  "effort": "low",
  "timeout_sec": 4.5,
  "uptime_sec": 3.7,
  "started_at": "2026-10-23T13:57:40.847119+00:00",
  "api_key_set": true
}
```

- 외부 모니터링(업타임 체크) 대상 URL로 사용  
- `api_key_set`은 키 설정 여부(true/false)만 표시하며 키 값은 노출하지 않음

### `POST /webhook`

**요청 (가정: 오픈빌더 스킬 v2.0)**

```json
{ "userRequest": { "utterance": "안녕", "user": { "id": "u1" } } }
```

**응답 (가정: 오픈빌더 스킬 v2.0 simpleText)**

```json
{ "version": "2.0", "template": { "outputs": [ { "simpleText": { "text": "안녕하세요! ..." } } ] } }
```

- 항상 HTTP 200 반환. 예외·타임아웃·API 키 없음 시에도 대체 메시지를 위 형식으로 반환  
- 알 수 없는 형식이면 흔한 키(`utterance`/`text`/`message`/`content` 등)를 중첩 구조 전체에서 탐색  
- JSON이 아닌 평문 바디는 바디 전체를 발화로 간주  
- 사용자 ID를 찾지 못하면 이력 없이 단발성 대화로 처리 (사용자 간 이력 혼선 방지)  
- 수신 원본(헤더+바디)을 `logs/payloads.jsonl`에 한 줄씩 기록. `Authorization`·`Cookie` 값은 `***`로 마스킹

| 상황 | 응답 텍스트 |  
|------|------------|  
| 발화 없음(빈 바디 등) | 메시지를 입력해 주세요. |  
| Claude 응답 지연(기본 4.5초 초과) | 답변 생성이 지연되고 있습니다. 잠시 후 다시 질문해 주세요. |  
| API 키 없음·API 오류·내부 예외 | 일시적인 오류로 답변하지 못했습니다. 잠시 후 다시 시도해 주세요. |  
| Claude 안전 정책상 거절 | 죄송합니다. 해당 요청에는 답변드리기 어렵습니다. |

---

## 환경변수

`BOT_` 접두어 사용. Claude Code 터미널은 `CLAUDE_EFFORT` 등을 자체 설정하므로 같은 이름을 쓰면 값이 섞임  
(실제로 검증 중 `CLAUDE_EFFORT=xhigh`가 서버 설정에 유입되는 문제를 확인하여 접두어로 분리함).

| 변수 | 기본값 | 설명 |  
|------|--------|------|  
| `ANTHROPIC_API_KEY` | (필수) | Claude API 키 |  
| `BOT_MODEL` | `claude-opus-5-5` | Claude 모델 ID |  
| `BOT_EFFORT` | `low` | 추론 강도(`low`~`max`). Opus 5.5는 thinking 비활성화 불가 → 지연시간은 이 값으로 제어 |  
| `BOT_MAX_TOKENS` | `2048` | 최대 출력 토큰(thinking 포함) |  
| `BOT_REPLY_TIMEOUT_SEC` | `4.5` | Claude 호출 전체 제한시간(초). 근거: 오픈빌더 5초 제한(대회 API 기준 미확인) |  
| `BOT_HISTORY_TURNS` | `10` | 사용자별 유지할 최근 대화 턴 수 |  
| `BOT_MAX_USERS` | `1000` | 메모리에 이력을 유지할 최대 사용자 수(초과 시 오래된 사용자부터 삭제) |  
| `BOT_FALLBACKS` | `on` | 안전 분류기 거절 시 서버 측 대체 모델 재시도(`fallbacks: "default"`). 400 오류 시 `off` |  
| `BOT_LOG_LEVEL` | `INFO` | 로그 레벨 |  
| `BOT_LOG_DIR` | `logs` | payload 기록 디렉토리 |  
| `PORT` | `8000` | Docker 실행 시 리스닝 포트 |

---

## 배포 방식 비교: 로컬 터널 vs 클라우드

심사 완료(11/1 자정)까지 약 9시간(제출 15:00 이후) 이상 무중단 응답이 필요하며, 다운 책임은 참가자에게 있음.

| 항목 | 로컬 + cloudflared 터널 | 로컬 + ngrok | 클라우드 컨테이너(예: Cloud Run) |  
|------|------------------------|--------------|-------------------------------|  
| 준비 시간 | 수 분 (계정 불필요) | 수 분 (계정·토큰 필요) | 30분~1시간 (계정·결제 수단 필요) |  
| 공개 URL | 재시작마다 변경(Quick Tunnel) | 계정 플랜에 따름 | 고정 HTTPS URL |  
| 가용성 리스크 | PC 절전·재부팅·Windows 업데이트·네트워크 끊김 | 동일 + 무료 플랜 제한 | 낮음 (단, 콜드스타트 주의) |  
| 응답 지연 | 터널 경유 지연 추가 | 터널 경유 지연 추가 | 서울 리전 선택 시 낮음 |  
| 비용 | 무료 | 무료~유료 | 상시 1인스턴스 유지 시 소액 과금 |  
| 디버깅 | 로컬 로그 즉시 확인 | 동일 + 요청 검사 UI | 플랫폼 로그 콘솔 |

### 권장안

- **본선 운영: 클라우드 컨테이너 배포** (상시 1인스턴스, 서울 리전)  
  - 근거: 9시간 이상 무중단 요구 + PC 의존 리스크 제거 + 고정 URL로 재등록 불필요  
  - 무료 플랜의 유휴 슬립·콜드스타트는 5초 제한(가정) 초과 위험 → **최소 인스턴스 1 필수**  
  - 대화 이력이 프로세스 메모리에 있으므로 **인스턴스·워커 1개로 고정**  
- **당일 개발·형식 확인: 로컬 + cloudflared 터널**  
  - 10:00 문서 공개 직후 실제 요청 형식을 `logs/payloads.jsonl`로 빠르게 확인하는 용도  
  - 클라우드 장애 시 대체 경로(백업)로도 사용 가능  
- **사전 준비(10/30까지): 현재 골격을 클라우드에 1회 배포해 외부에서 `/health` 응답 확인**

### 로컬 터널 예시

```powershell
# 서버 실행 후 별도 창에서
cloudflared tunnel --url http://localhost:8000
# 출력된 https://xxxx.trycloudflare.com + /webhook 을 봇 설정에 등록
```

### Docker / 클라우드 배포 예시

```powershell
docker build -t aitop100-bot .
docker run -p 8000:8000 --env-file .env aitop100-bot
```

```powershell
# Google Cloud Run 예시 — 이 환경에서 미실행. gcloud 설치·로그인 전제, 옵션은 배포 전 공식 문서로 재확인
gcloud run deploy aitop100-bot --source . --region asia-northeast3 `
  --min-instances 1 --max-instances 1 --allow-unauthenticated `
  --set-env-vars ANTHROPIC_API_KEY=sk-ant-...
```

- API 키는 플랫폼의 시크릿/환경변수 기능으로 주입 (이미지·저장소에 포함 금지).  
  Cloud Run은 `--set-env-vars` 대신 Secret Manager 연동(`--set-secrets`) 권장  
- Docker 빌드는 이 작업 환경에서 확인함 (`PORT=9000` 지정 시 9000 포트 기동, `/health` 200 응답)  
- `Dockerfile`은 실행 파일만 복사하므로 `.env`·`logs/`는 이미지에 포함되지 않음  
- 컨테이너 재시작 시 `logs/payloads.jsonl`과 대화 이력은 소실됨

---

## 당일(10/31) 해야 할 일

| 시간(목표) | 단계 | 작업 | 완료 기준 |  
|-----------|------|------|----------|  
| 10:00~10:30 | 1. 문서 확인 | 봇 API 문서에서 요청/응답 JSON, 웹훅 여부, 응답 제한시간, 인증 헤더 확인 | 아래 "미확인 사항" 표 전부 확인 |  
| 10:30~11:00 | 2. adapter 수정 | `kakao_adapter.py` 두 함수 수정, `test_app.py` 기대값 수정 | `pytest -q` 전체 통과 |  
| 11:00~13:00 | 3. 프롬프트 교체 | `system_prompt.md`를 과제용으로 교체, 필요 시 `BOT_EFFORT`·타임아웃 조정 | 로컬 웹훅 호출로 과제 응답 확인 |  
| 13:00~14:00 | 4. 배포 | 클라우드 재배포(또는 터널 기동) | 외부 URL `/health` 200 |  
| 14:00~14:30 | 5. 봇 연결 테스트 | 봇 설정에 웹훅 URL 등록 → 카카오톡 채널에서 메시지 전송 | 카톡 답변 수신 + `payloads.jsonl` 기록 확인 |  
| 14:30~15:00 | 6. 제출 | 제출 후 `/health` 외부 업타임 체크 설정 | 11/1 자정까지 모니터링 |

- 1단계에서 형식이 오픈빌더와 다르면 로컬 터널로 봇을 먼저 연결해 실제 요청을 `logs/payloads.jsonl`에서 확인  
- 응답 제한시간이 5초보다 길면 `BOT_REPLY_TIMEOUT_SEC`를 (제한시간 - 0.5초)로 조정

---

## 미확인 사항 (가정 목록)

| 항목 | 현재 가정 | 확인 후 수정 위치 |  
|------|----------|------------------|  
| 요청 JSON 형식 | 오픈빌더 스킬 v2.0 (`userRequest.utterance`, `userRequest.user.id`) | `kakao_adapter.parse_incoming` |  
| 응답 JSON 형식 | `{"version":"2.0","template":{"outputs":[{"simpleText":{"text":...}}]}}` | `kakao_adapter.build_reply` |  
| 연동 방식 | 카카오 → 서버 HTTP POST 웹훅 | 방식이 다르면 `app.py` 구조 변경 |  
| 응답 제한시간 | 5초 (오픈빌더 기준) → 타임아웃 4.5초 | `BOT_REPLY_TIMEOUT_SEC` |  
| 응답 텍스트 길이 제한 | 1000자 (오픈빌더 simpleText 기준) | `kakao_adapter.MAX_REPLY_CHARS` |  
| 인증·서명 검증 | 없음 (모든 요청 수락) | 요구 시 `app.py` 웹훅 핸들러에 검증 추가 |  
| 콜백(비동기 응답) | 미사용. 오픈빌더에는 AI 챗봇 콜백 기능이 있으나 대회 API 지원 여부 미확인 | 지원 시 5초 제한 완화 가능 |  
| 엔드포인트 요구사항 | HTTPS URL 1개, 경로 자유 지정 | URL 검증용 GET 요청 등이 있으면 `app.py`에 추가 |  
| Claude API 옵션 | `fallbacks: "default"`·자동 캐싱 요청 형식은 로컬 가짜 API로만 확인 | 실제 키로 1회 호출 확인 필요 |

---

## 운영 시 주의

- Claude Opus 5.5는 thinking을 끌 수 없어 응답 지연이 생길 수 있음. 타임아웃 빈발 시 로그(`Claude 타임아웃`)로 빈도 확인 후  
  `BOT_EFFORT`·프롬프트 길이·모델 선택을 조정  
- 대화 이력에는 텍스트만 저장(thinking 블록 미재전송). 최근 N턴 잘라내기로 인한 이력 불일치 오류 방지 목적  
- `logs/payloads.jsonl`에는 사용자 발화 원문이 저장되므로 대회 종료 후 삭제
