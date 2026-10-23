# AI_TOP_100 예선 카카오톡 챗봇 에이전트 서버
# 빌드: docker build -t aitop100-bot .
# 실행: docker run -p 8000:8000 -e ANTHROPIC_API_KEY=... aitop100-bot
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PORT=8000

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# .env·logs·테스트 파일이 이미지에 들어가지 않도록 실행 파일만 복사
COPY app.py agent.py kakao_adapter.py system_prompt.md ./

EXPOSE 8000
# PORT 환경변수 지원 (클라우드 플랫폼이 PORT를 주입하는 경우 대응)
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
