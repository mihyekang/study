"""
SW사업 대가산정 Q&A 에이전트 - FastAPI 서버
실행: uvicorn app:app --reload --port 8000
"""
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')

from pathlib import Path
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import anthropic
import chromadb
from sentence_transformers import SentenceTransformer

load_dotenv(Path(__file__).parent.parent / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CHROMA_DIR = Path(__file__).parent / "chroma_db"
COLLECTION_NAME = "sw_cost_guide"
EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
SYSTEM_PROMPT_PATH = (
    Path(__file__).parent.parent.parent / "prompts" / "2.sw-cost-qa-agent.md"
)
TOP_K = 5
CLAUDE_MODEL = "claude-opus-4-8"

# Global state (initialized at startup)
_state: dict = {}


def load_system_prompt() -> str:
    """prompts/2.sw-cost-qa-agent.md에서 [역할] 이후를 시스템 프롬프트로 변환."""
    if not SYSTEM_PROMPT_PATH.exists():
        raise FileNotFoundError(f"시스템 프롬프트 파일 없음: {SYSTEM_PROMPT_PATH}")
    content = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

    # [목표] 메타 섹션 제외, [역할]부터 사용
    role_start = content.find("[역할]")
    prompt_body = content[role_start:] if role_start != -1 else content

    # RAG 컨텍스트 활용 지침 추가
    rag_instruction = (
        "\n\n[RAG 컨텍스트 활용 지침]\n"
        "사용자 메시지에 <검색된_가이드_내용> 태그로 가이드 원문이 제공됩니다. "
        "이 내용을 1차 근거로 삼아 답변하고, 각 단락의 페이지 번호(p.N)를 출처로 인용하세요. "
        "검색된 내용으로 답변이 불충분한 경우 [제약조건]의 '확인 불가 처리' 지침을 따르세요."
    )

    return prompt_body + rag_instruction


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY가 설정되지 않았습니다. hands-on/.env를 확인하세요.")
    if not CHROMA_DIR.exists():
        raise RuntimeError(f"ChromaDB 없음: {CHROMA_DIR}\n먼저 python ingest.py를 실행하세요.")

    print("임베딩 모델 로딩...")
    _state["model"] = SentenceTransformer(EMBED_MODEL)

    print("ChromaDB 연결...")
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    _state["collection"] = client.get_collection(name=COLLECTION_NAME)

    print("시스템 프롬프트 로딩...")
    _state["system_prompt"] = load_system_prompt()

    _state["claude"] = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    count = _state["collection"].count()
    print(f"✅ 서버 준비 완료 | 청크: {count}개 | 모델: {CLAUDE_MODEL}")
    yield

    # Shutdown
    _state.clear()


app = FastAPI(
    title="SW사업 대가산정 Q&A 에이전트",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[int]


@app.get("/health")
def health():
    collection = _state.get("collection")
    return {
        "status": "ok",
        "model": CLAUDE_MODEL,
        "chunks": collection.count() if collection else 0,
    }


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="질문을 입력하세요.")

    collection = _state["collection"]
    model: SentenceTransformer = _state["model"]
    system_prompt: str = _state["system_prompt"]
    claude: anthropic.Anthropic = _state["claude"]

    # 1. 쿼리 임베딩
    query_embedding = model.encode([req.question]).tolist()

    # 2. ChromaDB 유사도 검색
    results = collection.query(
        query_embeddings=query_embedding,
        n_results=TOP_K,
        include=["documents", "metadatas"],
    )

    docs = results["documents"][0]
    metas = results["metadatas"][0]

    # 3. 컨텍스트 구성 (페이지 번호 포함)
    context_parts = []
    source_pages = []
    for doc, meta in zip(docs, metas):
        page_num = meta.get("page", 0)
        source_pages.append(int(page_num))
        context_parts.append(f"[p.{page_num}]\n{doc}")

    context = "\n\n---\n\n".join(context_parts)

    user_message = (
        "다음은 「SW사업 대가산정 가이드」에서 검색된 관련 내용입니다.\n\n"
        "<검색된_가이드_내용>\n"
        f"{context}\n"
        "</검색된_가이드_내용>\n\n"
        f"[질문]\n{req.question}"
    )

    # 4. Claude 호출
    message = claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    answer = message.content[0].text
    return ChatResponse(answer=answer, sources=sorted(set(source_pages)))
