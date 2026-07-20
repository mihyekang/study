# 업무 판단 어시스턴트

아키텍처 타당성, 연동 규격의 목적, 설계 트레이드오프 등 실무에서 반복적으로 소모되는  
기술 판단을 구조화된 근거·확신도·권고안과 함께 신속히 제시하는 CLI 도구.

시스템 프롬프트: `../../prompts/4.work-judgment-assistant.md`

---

## 구성 파일

| 파일 | 설명 |
|------|------|
| `judgment_assistant.py` | CLI 본체. 단발 질의 모드와 대화형 REPL 모드 지원 |
| `requirements.txt` | Python 의존성 목록 |

## 환경 설정

### 1. ANTHROPIC_API_KEY 추가

`hands-on/.env` 파일에 아래 항목 추가:

```
ANTHROPIC_API_KEY=sk-ant-...
```

### 2. 의존성 설치

```powershell
cd hands-on\judgment-assistant
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

---

## 실행 방법

### 단발 질의 모드 (명령줄 인자로 바로 질문)

```powershell
python judgment_assistant.py "MSA로 전환하려는데 서비스 10개를 API Gateway 하나로 묶는 구조가 괜찮을까?"
```

### 대화형 REPL 모드

```powershell
python judgment_assistant.py
```

```
업무 판단 어시스턴트 (모델: claude-opus-4-8)
판단이 필요한 상황을 입력하세요. 종료하려면 'exit' 또는 Ctrl+C.

판단 요청> 이 연동 규격은 왜 OAuth2 대신 mTLS를 요구하는 걸까?
```

`exit`, `quit` 입력 또는 Ctrl+C로 종료.

---

## 출력 형식

시스템 프롬프트 규격에 따라 매 응답에 아래 구조가 포함됨:

```
## [판단 요청 한 줄 요약]

**핵심 판단**
**근거** (항목마다 확신도: 확실/추정/불확실 표기)
**리스크 및 불확실 요소**
**권고안**
**추가 확인이 필요한 질문**
```

---

## 아키텍처

```
사용자 판단 요청 (CLI 인자 또는 REPL 입력)
    │
    ▼
[시스템 프롬프트 로딩] prompts/4.work-judgment-assistant.md ([역할] 이후)
    │
    ▼
[생성] Claude claude-opus-4-8
    │
    ▼
[출력] 근거·확신도·리스크·권고안·추가확인질문이 포함된 마크다운 응답
```

## 한계 (정직한 보고)

- 외부 지식베이스 검색(RAG) 없이 Claude 모델의 판단만으로 응답을 생성함  
  — 사내 고유 아키텍처·비공개 규격 문서에 대한 사실 확인은 하지 않음
- 확신도 표기는 모델이 프롬프트 규칙에 따라 자체 판단한 결과이며, 외부 검증 수단은 없음
