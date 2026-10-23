"""
서버 골격 테스트 (Claude 호출은 mock)
실행: pytest -q
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import agent
import app as app_module
import kakao_adapter

client = TestClient(app_module.app)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """테스트마다 대화 이력 초기화, payload 로그는 임시 디렉토리에 기록."""
    agent.reset_history()
    monkeypatch.setattr(app_module, "LOG_DIR", tmp_path)
    monkeypatch.setattr(app_module, "PAYLOAD_LOG", tmp_path / "payloads.jsonl")
    yield
    agent.reset_history()


def fake_claude(answer="테스트 답변"):
    """call_claude 대체: 호출 시 전달된 messages를 calls에 기록."""
    calls = []

    async def _fake(messages):
        calls.append([dict(m) for m in messages])
        return answer

    return _fake, calls


def reply_text(resp) -> str:
    data = resp.json()
    assert data["version"] == "2.0"
    return data["template"]["outputs"][0]["simpleText"]["text"]


# ---------- /health ----------

def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["model"] == agent.CLAUDE_MODEL
    assert data["uptime_sec"] >= 0


# ---------- /webhook: 형식별 파싱 ----------

def test_webhook_openbuilder_format(monkeypatch, tmp_path):
    fake, calls = fake_claude("안녕하세요!")
    monkeypatch.setattr(agent, "call_claude", fake)

    body = {"userRequest": {"utterance": "안녕", "user": {"id": "u1"}}}
    resp = client.post("/webhook", json=body)

    assert resp.status_code == 200
    assert resp.json() == {
        "version": "2.0",
        "template": {"outputs": [{"simpleText": {"text": "안녕하세요!"}}]},
    }
    assert calls[0][-1] == {"role": "user", "content": "안녕"}

    # 원본 요청이 JSONL 한 줄로 기록되어야 함
    lines = (tmp_path / "payloads.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["body"] == body
    assert record["path"] == "/webhook"
    assert "content-type" in record["headers"]


def test_webhook_unknown_format(monkeypatch):
    fake, calls = fake_claude("알 수 없는 형식 답변")
    monkeypatch.setattr(agent, "call_claude", fake)

    body = {"event": "message", "sender": {"id": "u9"}, "message": {"text": "날씨 알려줘"}}
    resp = client.post("/webhook", json=body)

    assert resp.status_code == 200
    assert reply_text(resp) == "알 수 없는 형식 답변"
    assert calls[0][-1]["content"] == "날씨 알려줘"
    assert kakao_adapter.parse_incoming(body) == ("u9", "날씨 알려줘")


def test_webhook_plain_text_body(monkeypatch):
    fake, calls = fake_claude()
    monkeypatch.setattr(agent, "call_claude", fake)

    resp = client.post("/webhook", content="그냥 텍스트", headers={"content-type": "text/plain"})

    assert resp.status_code == 200
    assert calls[0][-1]["content"] == "그냥 텍스트"


def test_webhook_empty_body(monkeypatch):
    fake, calls = fake_claude()
    monkeypatch.setattr(agent, "call_claude", fake)

    resp = client.post("/webhook", content=b"")

    assert resp.status_code == 200
    assert reply_text(resp) == agent.MSG_EMPTY
    assert calls == []  # 발화가 없으면 Claude를 호출하지 않음


def test_webhook_masks_sensitive_headers(monkeypatch, tmp_path):
    fake, _ = fake_claude()
    monkeypatch.setattr(agent, "call_claude", fake)

    client.post("/webhook", json={"text": "hi"}, headers={"Authorization": "Bearer secret"})

    record = json.loads((tmp_path / "payloads.jsonl").read_text(encoding="utf-8"))
    assert record["headers"]["authorization"] == "***"


# ---------- /webhook: Claude 오류·타임아웃 시 대체 응답 ----------

def test_webhook_claude_error(monkeypatch):
    async def boom(messages):
        raise RuntimeError("API 오류 가정")

    monkeypatch.setattr(agent, "call_claude", boom)

    resp = client.post("/webhook", json={"userRequest": {"utterance": "질문", "user": {"id": "u1"}}})

    assert resp.status_code == 200
    assert reply_text(resp) == agent.MSG_ERROR


def test_webhook_claude_timeout(monkeypatch):
    async def slow(messages):
        await asyncio.sleep(2)
        return "늦은 답변"

    monkeypatch.setattr(agent, "call_claude", slow)
    monkeypatch.setattr(agent, "REPLY_TIMEOUT_SEC", 0.2)

    resp = client.post("/webhook", json={"userRequest": {"utterance": "질문", "user": {"id": "u1"}}})

    assert resp.status_code == 200
    assert reply_text(resp) == agent.MSG_TIMEOUT


def test_webhook_unexpected_exception(monkeypatch):
    """어댑터 등 agent 밖에서 예외가 나도 500 대신 대체 응답."""
    def broken(body):
        raise ValueError("파싱 실패 가정")

    monkeypatch.setattr(kakao_adapter, "parse_incoming", broken)

    resp = client.post("/webhook", json={"text": "hi"})

    assert resp.status_code == 200
    assert reply_text(resp) == agent.MSG_ERROR


def test_webhook_refusal(monkeypatch):
    async def refused(messages):
        return None

    monkeypatch.setattr(agent, "call_claude", refused)

    resp = client.post("/webhook", json={"userRequest": {"utterance": "질문", "user": {"id": "u1"}}})

    assert reply_text(resp) == agent.MSG_REFUSAL
    assert len(agent._histories["u1"]) == 0  # 거절된 질문은 이력에 남기지 않음


# ---------- 대화 이력 ----------

def test_history_per_user(monkeypatch):
    fake, calls = fake_claude("답")
    monkeypatch.setattr(agent, "call_claude", fake)

    def send(uid, text):
        client.post("/webhook", json={"userRequest": {"utterance": text, "user": {"id": uid}}})

    send("u1", "첫 질문")
    send("u2", "다른 사용자")
    send("u1", "두번째 질문")

    assert calls[1] == [{"role": "user", "content": "다른 사용자"}]
    assert calls[2] == [
        {"role": "user", "content": "첫 질문"},
        {"role": "assistant", "content": "답"},
        {"role": "user", "content": "두번째 질문"},
    ]


def test_history_limit(monkeypatch):
    fake, calls = fake_claude("답")
    monkeypatch.setattr(agent, "call_claude", fake)
    monkeypatch.setattr(agent, "HISTORY_TURNS", 2)

    for i in range(5):
        asyncio.run(agent.reply("u1", f"질문{i}"))

    # 최근 2턴(4개 메시지) + 현재 질문, 첫 메시지는 user
    assert len(calls[-1]) == 5
    assert calls[-1][0] == {"role": "user", "content": "질문2"}


# ---------- call_claude: 요청 구성·응답 처리 (SDK 클라이언트 mock) ----------

def make_fake_client(response):
    create = AsyncMock(return_value=response)
    fake = SimpleNamespace(
        beta=SimpleNamespace(messages=SimpleNamespace(create=create)),
        messages=SimpleNamespace(create=create),
    )
    return fake, create


def test_call_claude_request_and_text(monkeypatch):
    response = SimpleNamespace(
        stop_reason="end_turn",
        stop_details=None,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=" 답변 ")],
    )
    fake, create = make_fake_client(response)
    monkeypatch.setattr(agent, "_client", fake)

    result = asyncio.run(agent.call_claude([{"role": "user", "content": "q"}]))

    assert result == "답변"
    kwargs = create.call_args.kwargs
    assert kwargs["model"] == agent.CLAUDE_MODEL
    assert kwargs["output_config"] == {"effort": agent.CLAUDE_EFFORT}
    assert kwargs["system"]  # system_prompt.md 로드
    assert "<!--" not in kwargs["system"]  # 작성자 메모 주석 제거
    if agent.USE_FALLBACKS:
        assert kwargs["fallbacks"] == "default"
        assert kwargs["betas"] == [agent.FALLBACK_BETA]


def test_call_claude_refusal(monkeypatch):
    response = SimpleNamespace(stop_reason="refusal", stop_details=None, content=[])
    fake, _ = make_fake_client(response)
    monkeypatch.setattr(agent, "_client", fake)

    assert asyncio.run(agent.call_claude([{"role": "user", "content": "q"}])) is None


# ---------- kakao_adapter ----------

def test_build_reply_truncates():
    data = kakao_adapter.build_reply("가" * 1500)
    text = data["template"]["outputs"][0]["simpleText"]["text"]
    assert len(text) == kakao_adapter.MAX_REPLY_CHARS


def test_parse_incoming_garbage():
    assert kakao_adapter.parse_incoming(None) == ("", "")
    assert kakao_adapter.parse_incoming(123) == ("", "")
    assert kakao_adapter.parse_incoming({"foo": {"bar": 1}}) == ("", "")
