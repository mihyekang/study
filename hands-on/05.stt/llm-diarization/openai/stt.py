# -*- coding: utf-8 -*-
"""
LLM 기반 화자 분리(Diarization) 음성 인식 프로그램 (OpenAI)
==========================================================

목적
- OpenAI Whisper API(`whisper-1`)로 음성을 '타임스탬프가 붙은 세그먼트'로 전사한다.
- 최신 GPT 모델(chat.completions)이 전사 텍스트의 의미·맥락을 근거로 화자를 분리한다.
  (음향 임베딩이 아닌 'LLM 추론' 기반 diarization → GPU 불필요, ffmpeg 불필요)

처리 구조
  1) 전사(Transcription)   : whisper-1 + verbose_json + segment 타임스탬프
  2) 타임스탬프 transcript : 세그먼트마다 `[MM:SS] 텍스트` 한 줄로 결합
  3) LLM 화자 분리         : chat.completions(기본 gpt-4o)로 화자별 분리
  4) 파싱                  : 정규식으로 `[MM:SS] 화자X: 텍스트` 라인만 추출
  5) 화자 실명 입력        : 화자별 샘플 발화를 보여주고 실명 입력(인터랙티브)
  6) 출력 파일 생성        : result.txt / result_chunks.csv / result.json

정직성 설계
- '실행하지 않은 것을 완료라고 보고하지 않는다'는 원칙에 따라, 각 단계의 실제 결과를
  진행 로그로 출력한다(세그먼트 수, 화자 수, 저장 경로).
- LLM이 형식을 벗어난 부가 설명을 붙여도, 코드가 정규식으로 '형식에 맞는 라인'만 채택하여
  환각/잡음이 결과 파일에 섞이지 않게 한다.
- 파싱 결과가 0건이거나 API 호출이 실패하면, 거짓 완료 대신 명확한 한글 오류로 즉시 중단한다.
- 비대화형(stdin이 tty 아님/EOF) 환경에서는 실명 입력을 건너뛰고 기본 라벨을 유지하여
  자동화/테스트 시에도 안전하게 동작한다.
"""

import os
import re
import sys
import json
import glob
from pathlib import Path

import pandas as pd
from openai import OpenAI

# ──────────────────────────────────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────────────────────────────────
# 전사 모델: 세그먼트 타임스탬프를 지원하는 whisper-1 고정
#  (gpt-4o-transcribe 계열은 세그먼트 타임스탬프 미지원이므로 사용하지 않음)
TRANSCRIBE_MODEL = "whisper-1"

# 화자 분리 모델: 환경변수로 교체 가능, 기본값은 gpt-4o
DIARIZE_MODEL = os.getenv("OPENAI_DIARIZE_MODEL", "gpt-4o")

# 화자 분리 작업 temperature (분리 일관성을 위해 낮게 권장)
DIARIZE_TEMPERATURE = 0.2

# 스크립트와 같은 폴더(= 출력 폴더)
SCRIPT_DIR = Path(__file__).resolve().parent

# 오디오 검색 폴더: 프로젝트의 hands-on/audio
#  (stt.py → openai → llm-diarization → 05.stt → hands-on → audio)
AUDIO_DIR = SCRIPT_DIR.parents[2] / "audio"

# 오디오로 인정할 확장자 (Whisper API 업로드 지원 형식)
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".mp4", ".webm", ".mpga", ".mpeg", ".flac", ".ogg", ".oga"}

# 화자 라벨 파싱 정규식: `[MM:SS] 화자X: 텍스트` 형식 라인만 채택
DIARIZED_LINE_RE = re.compile(r"^\[(\d{1,2}:\d{2})\]\s*(화자[A-Z])\s*:\s*(.+)$", re.MULTILINE)


# ──────────────────────────────────────────────────────────────────────────
# 화자 분리 프롬프트 (한글)
# ──────────────────────────────────────────────────────────────────────────
DIARIZE_SYSTEM_PROMPT = """당신은 통화/대화 녹음 전사본을 화자별로 분리하는 전문가입니다.
타임스탬프가 붙은 전사본을 입력받아, 각 발화를 말한 화자별로 구분합니다.

[규칙]
- 화자는 등장 순서대로 화자A, 화자B, 화자C ... 로 라벨링합니다.
- 대화의 맥락(질문-답변, 말투, 호칭, 주제 전환)을 근거로 화자 전환을 판단합니다.
- 같은 화자가 연속해서 말하면 같은 라벨을 유지합니다.
- 감탄사, 추임새(어, 음, 아, 응, 그래, 네, 어어, 음음 등)와 자연스러운 구어체 표현을 그대로 살립니다. 임의로 다듬거나 생략하지 마세요.
- 타임스탬프는 입력 전사본의 각 구간 시작 시간([MM:SS])을 사용합니다.
- 출력은 오직 아래 형식의 라인들만 출력합니다. 설명, 요약, 머리말, 코드블록을 절대 추가하지 마세요.

[출력 형식]
[MM:SS] 화자X: 발화 내용
[MM:SS] 화자X: 발화 내용"""

# 유저 프롬프트 템플릿: transcript는 <transcript> 태그로 감싸 입력 데이터를 명확히 구분
DIARIZE_USER_PROMPT_TEMPLATE = """다음은 타임스탬프가 붙은 음성 전사본입니다. 화자별로 분리하여 지정된 형식으로만 출력하세요.

<transcript>
{timestamped_transcript}
</transcript>"""


# ──────────────────────────────────────────────────────────────────────────
# API Key 로딩  (.env 가 'KEY=value' 와 'KEY: value' 두 형식 모두 허용)
# ──────────────────────────────────────────────────────────────────────────
def find_env_file():
    """현재 스크립트 위치에서 상위로 올라가며 .env 파일을 탐색한다.

    반환: 발견 시 Path, 없으면 None.
    """
    here = SCRIPT_DIR
    for directory in [here, *here.parents]:
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
    return None


def load_openai_api_key():
    """OPENAI_API_KEY를 환경변수 → .env 순으로 로딩한다.

    .env는 'KEY=value' 와 'KEY: value' 두 구분자를 모두 허용한다.
    (예제 02.multi-turn 의 .env 파싱 방식과 동일)
    반환: API 키 문자열. 찾지 못하면 명확한 한글 메시지와 함께 예외 발생.
    """
    key = os.getenv("OPENAI_API_KEY")
    if key:
        return key.strip()

    env_path = find_env_file()
    if not env_path:
        raise RuntimeError(
            "OPENAI_API_KEY를 찾을 수 없습니다. 환경변수 또는 상위 경로의 .env 파일을 확인하세요."
        )

    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"OPENAI_API_KEY\s*[:=]\s*(.+)", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")

    raise RuntimeError(
        f"{env_path} 에서 OPENAI_API_KEY 항목을 찾지 못했습니다. (KEY=value 또는 KEY: value 형식)"
    )


# ──────────────────────────────────────────────────────────────────────────
# 오디오 파일 선택
# ──────────────────────────────────────────────────────────────────────────
def find_first_audio():
    """hands-on/audio 폴더에서 정렬 후 '첫 번째' 오디오 파일을 자동 선택한다.

    오디오 확장자(AUDIO_EXTS)만 대상으로 하며, 파일명 정렬 순서의 첫 파일을 반환한다.
    반환: 선택된 오디오 파일의 Path. 없으면 명확한 한글 메시지와 함께 예외 발생.
    """
    if not AUDIO_DIR.exists():
        raise FileNotFoundError(f"오디오 폴더가 존재하지 않습니다: {AUDIO_DIR}")

    candidates = [
        p for p in AUDIO_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    ]
    if not candidates:
        raise FileNotFoundError(
            f"오디오 파일이 없습니다: {AUDIO_DIR} (지원 확장자: {sorted(AUDIO_EXTS)})"
        )

    candidates.sort(key=lambda p: p.name)
    return candidates[0]


# ──────────────────────────────────────────────────────────────────────────
# 1) 전사 (Transcription)
# ──────────────────────────────────────────────────────────────────────────
def transcribe(client, audio_path):
    """whisper-1 모델로 오디오를 전사하여 세그먼트 리스트를 반환한다.

    - response_format="verbose_json" + timestamp_granularities=["segment"] 로 호출하여
      세그먼트별 start(초)와 text를 얻는다.
    - ffmpeg 의존 없음: 파일을 그대로 API에 업로드(서버 측 디코딩).
    반환: [{"start": float, "text": str}, ...] (start 기준 정렬).
    """
    print(f"[1/6] 전사 시작: {audio_path.name} (모델={TRANSCRIBE_MODEL})")
    try:
        with audio_path.open("rb") as f:
            resp = client.audio.transcriptions.create(
                model=TRANSCRIBE_MODEL,
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
    except Exception as e:  # noqa: BLE001 - API 실패를 명확한 한글 메시지로 전달
        raise RuntimeError(f"전사(Whisper) API 호출에 실패했습니다: {e}") from e

    # SDK 객체 → 세그먼트 정규화 (객체/딕셔너리 양쪽 모두 안전 처리)
    raw_segments = getattr(resp, "segments", None)
    if raw_segments is None and isinstance(resp, dict):
        raw_segments = resp.get("segments")

    segments = []
    for seg in (raw_segments or []):
        start = getattr(seg, "start", None) if not isinstance(seg, dict) else seg.get("start")
        text = getattr(seg, "text", None) if not isinstance(seg, dict) else seg.get("text")
        if start is None or text is None:
            continue
        cleaned = str(text).strip()
        if not cleaned:
            continue
        segments.append({"start": float(start), "text": cleaned})

    if not segments:
        raise RuntimeError(
            "전사 결과 세그먼트가 0건입니다. 오디오 내용/형식 또는 무음 여부를 확인하세요."
        )

    segments.sort(key=lambda s: s["start"])
    print(f"      전사 완료: 세그먼트 {len(segments)}개")
    return segments


# ──────────────────────────────────────────────────────────────────────────
# 2) 타임스탬프 transcript 구성
# ──────────────────────────────────────────────────────────────────────────
def _sec_to_mmss(seconds):
    """초(float)를 MM:SS 문자열로 변환한다. (예: 75 → '01:15')

    분이 100을 넘는 장시간 녹음도 자릿수를 유지한다(예: 6075 → '101:15').
    """
    total = int(round(float(seconds)))
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


def build_timestamped_transcript(segments):
    """세그먼트 리스트를 `[MM:SS] 텍스트` 한 줄씩 결합한 transcript 문자열로 만든다.

    반환: LLM 입력용 멀티라인 transcript 텍스트.
    """
    lines = [f"[{_sec_to_mmss(seg['start'])}] {seg['text']}" for seg in segments]
    transcript = "\n".join(lines)
    print(f"[2/6] 타임스탬프 transcript 구성 완료: {len(lines)}줄")
    return transcript


# ──────────────────────────────────────────────────────────────────────────
# 3) LLM 화자 분리 (Diarization)
# ──────────────────────────────────────────────────────────────────────────
def diarize(client, timestamped_transcript):
    """최신 GPT 모델(chat.completions)로 transcript를 화자별로 분리한다.

    시스템/유저 프롬프트는 한글 고정 프롬프트를 사용하며, transcript는 <transcript>
    태그로 감싸 입력 데이터로 명확히 구분한다.
    반환: LLM의 원문 응답 텍스트(파싱 전).
    """
    print(f"[3/6] LLM 화자 분리 시작 (모델={DIARIZE_MODEL}, temperature={DIARIZE_TEMPERATURE})")
    user_prompt = DIARIZE_USER_PROMPT_TEMPLATE.format(
        timestamped_transcript=timestamped_transcript
    )
    try:
        resp = client.chat.completions.create(
            model=DIARIZE_MODEL,
            messages=[
                {"role": "system", "content": DIARIZE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=DIARIZE_TEMPERATURE,
        )
    except Exception as e:  # noqa: BLE001 - API 실패를 명확한 한글 메시지로 전달
        raise RuntimeError(f"화자 분리(LLM) API 호출에 실패했습니다: {e}") from e

    content = (resp.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("화자 분리 응답이 비어 있습니다. 모델/입력을 확인하세요.")
    print("      LLM 화자 분리 응답 수신 완료")
    return content


# ──────────────────────────────────────────────────────────────────────────
# 4) 파싱
# ──────────────────────────────────────────────────────────────────────────
def parse_diarized(diarized_text):
    """LLM 응답에서 `[MM:SS] 화자X: 텍스트` 형식 라인만 정규식으로 추출한다.

    설명/요약/머리말/코드블록 등 형식을 벗어난 부가 텍스트는 모두 무시한다.
    반환: [{"time": "MM:SS", "speaker": "화자X", "text": "..."}, ...]
    파싱 결과가 0건이면 명확한 한글 메시지와 함께 예외 발생.
    """
    utterances = []
    for m in DIARIZED_LINE_RE.finditer(diarized_text):
        time_str, speaker, text = m.group(1), m.group(2), m.group(3).strip()
        if not text:
            continue
        utterances.append({"time": time_str, "speaker": speaker, "text": text})

    if not utterances:
        raise RuntimeError(
            "화자 분리 결과 파싱 결과가 0건입니다. "
            "LLM이 '[MM:SS] 화자X: 텍스트' 형식을 따르지 않았을 수 있습니다."
        )

    speakers = sorted({u["speaker"] for u in utterances})
    print(f"[4/6] 파싱 완료: 발화 {len(utterances)}건, 화자 {len(speakers)}명 ({', '.join(speakers)})")
    return utterances


# ──────────────────────────────────────────────────────────────────────────
# 5) 화자 실명 입력 (인터랙티브)
# ──────────────────────────────────────────────────────────────────────────
def prompt_speaker_names(utterances):
    """등장하는 각 화자에 대해 샘플 발화를 보여주고 실명을 입력받는다.

    - Enter(빈 입력)이면 기본 라벨(화자A/B...)을 유지한다.
    - 견고성: stdin이 tty가 아니면(파이프/비대화형) 프롬프트를 건너뛰고 기본 라벨을 유지한다.
    - EOFError(입력 스트림 종료)도 안전하게 처리하여 기본 라벨로 폴백한다.
    반환: {"화자A": "실명 또는 화자A", ...} 매핑.
    """
    # 등장 순서를 보존하여 화자 목록 구성
    ordered_speakers = []
    for u in utterances:
        if u["speaker"] not in ordered_speakers:
            ordered_speakers.append(u["speaker"])

    # 기본 매핑(라벨 그대로)
    name_map = {sp: sp for sp in ordered_speakers}

    print(f"[5/6] 화자 실명 입력 (총 {len(ordered_speakers)}명)")

    # 비대화형 환경 감지: tty가 아니면 입력을 건너뛴다
    if not sys.stdin or not sys.stdin.isatty():
        print("      (비대화형 입력 환경 감지 → 실명 입력을 건너뛰고 기본 라벨을 유지합니다)")
        return name_map

    for sp in ordered_speakers:
        # 해당 화자의 샘플 발화 1~2개 미리보기
        samples = [u["text"] for u in utterances if u["speaker"] == sp][:2]
        print(f"\n  - {sp} 샘플 발화:")
        for s in samples:
            preview = s if len(s) <= 60 else s[:60] + "..."
            print(f"      · {preview}")
        try:
            entered = input(f"    {sp} 의 실명을 입력하세요 (Enter=기본 라벨 유지): ").strip()
        except EOFError:
            # 입력 스트림이 중간에 닫혀도 안전하게 기본 라벨로 폴백
            print("\n      (입력 종료 감지 → 이후 화자는 기본 라벨을 유지합니다)")
            break
        if entered:
            name_map[sp] = entered

    return name_map


# ──────────────────────────────────────────────────────────────────────────
# 6) 출력 파일 생성
# ──────────────────────────────────────────────────────────────────────────
def write_outputs(audio_path, utterances, name_map):
    """result.txt / result_chunks.csv / result.json 세 파일을 생성한다.

    - result.txt      : 원본 변환 결과(기본 라벨 화자A/B 유지, 실명 미반영). UTF-8.
    - result_chunks.csv: 구분자 '|', index 없음, utf-8-sig. 컬럼 time|speaker|text(실명 반영).
    - result.json     : 실명 반영 JSON. ensure_ascii=False, indent=2.
    반환: 생성된 파일 경로 리스트.
    """
    print("[6/6] 출력 파일 생성")

    # (1) result.txt — 원본 보존: 기본 라벨 그대로 저장
    txt_path = SCRIPT_DIR / "result.txt"
    txt_lines = [f"[{u['time']}] {u['speaker']}: {u['text']}" for u in utterances]
    txt_path.write_text("\n".join(txt_lines) + "\n", encoding="utf-8")
    print(f"      저장: {txt_path}")

    # (2) result_chunks.csv — 실명 반영, 구분자 '|'
    csv_path = SCRIPT_DIR / "result_chunks.csv"
    df = pd.DataFrame(
        [
            {
                "time": u["time"],
                "speaker": name_map.get(u["speaker"], u["speaker"]),
                "text": u["text"],
            }
            for u in utterances
        ],
        columns=["time", "speaker", "text"],
    )
    df.to_csv(csv_path, sep="|", index=False, encoding="utf-8-sig")
    print(f"      저장: {csv_path}")

    # (3) result.json — 실명 반영
    json_path = SCRIPT_DIR / "result.json"
    result = {
        "audio_file": audio_path.name,
        "diarize_model": DIARIZE_MODEL,
        "transcribe_model": TRANSCRIBE_MODEL,
        "speakers": {sp: name_map.get(sp, sp) for sp in name_map},
        "utterances": [
            {
                "time": u["time"],
                "speaker": name_map.get(u["speaker"], u["speaker"]),
                "text": u["text"],
            }
            for u in utterances
        ],
    }
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"      저장: {json_path}")

    return [txt_path, csv_path, json_path]


# ──────────────────────────────────────────────────────────────────────────
# 메인 파이프라인
# ──────────────────────────────────────────────────────────────────────────
def main():
    """전체 파이프라인 실행: 전사 → 타임스탬프 구성 → LLM 분리 → 파싱 → 실명 입력 → 출력."""
    print("=" * 70)
    print(" LLM 기반 화자 분리 음성 인식 (OpenAI Whisper + GPT)")
    print("=" * 70)

    try:
        api_key = load_openai_api_key()
        audio_path = find_first_audio()
        print(f"선택된 오디오: {audio_path}")

        client = OpenAI(api_key=api_key)

        segments = transcribe(client, audio_path)
        transcript = build_timestamped_transcript(segments)
        diarized_text = diarize(client, transcript)
        utterances = parse_diarized(diarized_text)
        name_map = prompt_speaker_names(utterances)
        paths = write_outputs(audio_path, utterances, name_map)
    except (RuntimeError, FileNotFoundError) as e:
        # 단계별 정직성: 거짓 완료 대신 명확한 한글 오류로 즉시 중단
        print(f"\n[오류] {e}", file=sys.stderr)
        sys.exit(1)

    print("\n완료: 아래 파일이 생성되었습니다.")
    for p in paths:
        print(f"  - {p}")


# ──────────────────────────────────────────────────────────────────────────
# 실행 진입점: import 만으로는 실행되지 않음
# ──────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    main()
