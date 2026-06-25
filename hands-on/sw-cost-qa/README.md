# SW사업 대가산정 Q&A 에이전트

2025년 개정판 「SW사업 대가산정 가이드」(464p) 기반 RAG Q&A 에이전트.  
FastAPI + ChromaDB + sentence-transformers + Claude claude-opus-4-8 구성.

---

## 구성 파일

| 파일 | 설명 |
|------|------|
| `ingest.py` | PDF → 텍스트 추출 → 청킹 → ChromaDB 저장 (최초 1회 실행) |
| `app.py` | FastAPI 서버. `/health`, `/chat` 엔드포인트 |
| `requirements.txt` | Python 의존성 목록 |
| `chroma_db/` | 벡터 DB (ingest.py 실행 후 생성) |

시스템 프롬프트: `../../prompts/2.sw-cost-qa-agent.md`

---

## 환경 설정

### 1. ANTHROPIC_API_KEY 추가

`hands-on/.env` 파일에 아래 항목 추가:

```
ANTHROPIC_API_KEY=sk-ant-...
```

### 2. 가상환경 생성 및 의존성 설치

```powershell
cd hands-on\sw-cost-qa
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> `sentence-transformers` 첫 실행 시 임베딩 모델(`paraphrase-multilingual-MiniLM-L12-v2`, ~420MB)을  
> 자동 다운로드함. 인터넷 연결 필요.

---

## 실행 방법

### Step 1: PDF 인제스트 (최초 1회)

```powershell
python ingest.py
# 또는 PDF 경로 직접 지정
python ingest.py "C:\path\to\(2025년_개정판)_SW사업_대가산정_가이드.pdf"
```

완료 후 `chroma_db/` 디렉터리 생성 확인.

### Step 2: FastAPI 서버 실행

```powershell
uvicorn app:app --reload --port 8000
```

### Step 3: API 테스트

```powershell
# 헬스 체크
Invoke-RestMethod http://localhost:8000/health

# 질문
$body = @{ question = "기능점수 방식으로 SW 개발비를 산정하는 절차가 어떻게 되나요?" } | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri http://localhost:8000/chat -Body $body -ContentType "application/json"
```

또는 Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## API 명세

### `GET /health`

```json
{
  "status": "ok",
  "model": "claude-opus-4-8",
  "chunks": 1234
}
```

### `POST /chat`

**요청**
```json
{ "question": "ISP 수립비 산정 방법이 궁금합니다." }
```

**응답**
```json
{
  "answer": "## ISP 수립비 산정 방법\n\n**핵심 답변**\n...",
  "sources": [45, 46, 47]
}
```

- `answer`: Claude가 생성한 마크다운 답변
- `sources`: 참조된 가이드 페이지 번호 목록

---

## 아키텍처

```
사용자 질문
    │
    ▼
[임베딩] paraphrase-multilingual-MiniLM-L12-v2
    │
    ▼
[검색] ChromaDB 코사인 유사도 Top-5 청크
    │
    ▼
[생성] Claude claude-opus-4-8
      시스템 프롬프트: prompts/2.sw-cost-qa-agent.md
      컨텍스트: 검색된 가이드 원문 + 페이지 번호
    │
    ▼
[출력] 마크다운 답변 + 출처 페이지 목록
```

---

## 청킹 전략

- 청크 크기: 1,000자 (~512 토큰, 한국어 기준)
- 오버랩: 100자
- 메타데이터: 원본 페이지 번호 (출처 인용용)
