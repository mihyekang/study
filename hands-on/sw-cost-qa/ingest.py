"""
SW사업 대가산정 가이드 PDF → ChromaDB 인제스트 스크립트
실행: python ingest.py [PDF 경로]
"""
import sys
import os
import argparse
sys.stdout.reconfigure(encoding='utf-8')

from pathlib import Path
from dotenv import load_dotenv
import pdfplumber
import chromadb
from sentence_transformers import SentenceTransformer

load_dotenv(Path(__file__).parent.parent / ".env")

DEFAULT_PDF = os.getenv(
    "PDF_PATH",
    r"C:\Users\User\Downloads\(2025년_개정판)_SW사업_대가산정_가이드.pdf",
)
CHROMA_DIR = Path(__file__).parent / "chroma_db"
COLLECTION_NAME = "sw_cost_guide"
EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
CHUNK_SIZE = 1000   # characters ≈ 512 tokens (Korean)
CHUNK_OVERLAP = 100


def extract_pages(pdf_path: str) -> list[dict]:
    """pdfplumber로 페이지별 텍스트 추출."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages, start=1):
            if i % 50 == 0:
                print(f"  {i}/{total} 페이지 추출 중...")
            text = page.extract_text()
            if text and text.strip():
                pages.append({"page": i, "text": text.strip()})
    print(f"  → 유효 페이지: {len(pages)}개")
    return pages


def split_into_chunks(pages: list[dict]) -> list[dict]:
    """페이지 텍스트를 오버랩 청킹으로 분할, 페이지 메타데이터 보존."""
    chunks = []
    for page_data in pages:
        text = page_data["text"]
        page_num = page_data["page"]

        if len(text) <= CHUNK_SIZE:
            chunks.append({
                "id": f"p{page_num:04d}_c00",
                "text": text,
                "page": page_num,
            })
        else:
            start = 0
            idx = 0
            while start < len(text):
                chunk_text = text[start:start + CHUNK_SIZE]
                chunks.append({
                    "id": f"p{page_num:04d}_c{idx:02d}",
                    "text": chunk_text,
                    "page": page_num,
                })
                start += CHUNK_SIZE - CHUNK_OVERLAP
                idx += 1

    print(f"  → 생성된 청크: {len(chunks)}개")
    return chunks


def build_vector_db(chunks: list[dict]):
    """sentence-transformers 임베딩 → ChromaDB 저장."""
    print("  임베딩 모델 로딩...")
    model = SentenceTransformer(EMBED_MODEL)

    texts = [c["text"] for c in chunks]
    print(f"  임베딩 생성 중 ({len(texts)}개)...")
    embeddings = model.encode(texts, batch_size=64, show_progress_bar=True).tolist()

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        client.delete_collection(COLLECTION_NAME)
        print("  기존 컬렉션 삭제")
    except Exception:
        pass

    collection = client.create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    batch_size = 200
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        collection.add(
            ids=[c["id"] for c in batch],
            documents=[c["text"] for c in batch],
            embeddings=embeddings[i:i + batch_size],
            metadatas=[{"page": c["page"]} for c in batch],
        )
        print(f"  저장: {min(i + batch_size, len(chunks))}/{len(chunks)}")

    print(f"  → ChromaDB 저장 완료: {CHROMA_DIR}")
    return collection


def main():
    parser = argparse.ArgumentParser(description="SW대가산정 가이드 PDF 인제스트")
    parser.add_argument("pdf", nargs="?", default=DEFAULT_PDF, help="PDF 파일 경로")
    args = parser.parse_args()

    pdf_path = args.pdf
    if not Path(pdf_path).exists():
        print(f"[오류] PDF 파일 없음: {pdf_path}")
        sys.exit(1)

    print(f"\n[1/3] PDF 추출: {pdf_path}")
    pages = extract_pages(pdf_path)

    print("\n[2/3] 청킹...")
    chunks = split_into_chunks(pages)

    print("\n[3/3] 벡터 DB 구축...")
    build_vector_db(chunks)

    print("\n✅ 인제스트 완료!")


if __name__ == "__main__":
    main()
