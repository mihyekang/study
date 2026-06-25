# LLM 기반 화자 분리(Diarization) 음성 인식

## 1. 기본 정보

### 목적
- OpenAI Whisper API로 음성을 타임스탬프와 함께 텍스트로 전사하고, 최신 GPT 모델이 전사 텍스트의  
  의미·맥락을 근거로 화자를 분리(LLM-based diarization)하는 프로그램임.
- 음향 임베딩 기반 화자 분리(WhisperX/pyannote)와 달리 GPU·ffmpeg 없이 동작하는 것이 특징임.

### 주요 기능
- `hands-on/audio/` 폴더의 첫 번째 오디오 파일을 자동 선택하여 처리함.
- `whisper-1` 모델로 세그먼트별 타임스탬프(start)와 텍스트를 전사함.
- 최신 GPT 모델(기본 `gpt-4o`)로 화자를 화자A/화자B... 로 분리함.
- 화자별 샘플 발화를 보여주고 실명을 인터랙티브하게 입력받음(비대화형 환경은 자동 건너뜀).
- 결과를 `result.txt` / `result_chunks.csv` / `result.json` 세 파일로 저장함.

## 2. 소스 코드 설명

### 주요 함수
| 함수 | 역할 |
| --- | --- |
| `load_openai_api_key()` | `.env`에서 `OPENAI_API_KEY` 로딩(`KEY=value`/`KEY: value` 모두 허용) |
| `find_first_audio()` | `hands-on/audio/`의 정렬 후 첫 오디오 파일 선택 |
| `transcribe()` | `whisper-1` + `verbose_json` + segment 타임스탬프로 전사 |
| `build_timestamped_transcript()` | 세그먼트를 `[MM:SS] 텍스트` 줄로 결합 |
| `diarize()` | GPT 모델 chat.completions로 화자 분리 호출 |
| `parse_diarized()` | 정규식으로 `[MM:SS] 화자X: 텍스트` 라인만 추출 |
| `prompt_speaker_names()` | 화자 실명 입력(비대화형/EOF 안전 처리) |
| `write_outputs()` | 결과 파일 3종 생성 |
| `main()` | 전체 파이프라인 실행 진입점 |

### 처리 흐름
1. 전사(Transcription): `whisper-1`로 오디오를 세그먼트별 start 시간 + 텍스트로 변환함.
2. 타임스탬프 구성: 세그먼트마다 start(초)를 `MM:SS`로 바꿔 `[MM:SS] 텍스트` 한 줄로 만듦.
3. LLM 분리: 타임스탬프 transcript를 `<transcript>` 태그로 감싸 GPT 모델에 전달, 화자별로 분리함.
4. 파싱: LLM 응답에서 `^\[(\d{1,2}:\d{2})\]\s*(화자[A-Z])\s*:\s*(.+)$` 정규식으로 형식에 맞는  
   라인만 채택하여 부가 설명·잡음을 제거함.
5. 실명 입력: 화자별 샘플 발화를 보여주고 실명을 입력받음. Enter 시 기본 라벨 유지.  
   stdin이 tty가 아니거나 EOF면 입력을 건너뛰고 기본 라벨을 유지함.
6. 출력: 원본(`result.txt`)·실명 반영(`result_chunks.csv`/`result.json`) 파일을 생성함.

### 설계 메모(정직성)
- LLM이 형식 밖 텍스트를 덧붙여도 정규식 파싱이 형식에 맞는 라인만 채택하여 환각·잡음 유입을 차단함.
- 전사 0건, 파싱 0건, API 실패, 오디오 없음 등은 거짓 완료 대신 명확한 한글 오류로 즉시 중단함.
- 각 단계의 실제 결과(세그먼트 수, 화자 수, 저장 경로)를 진행 로그로 출력함.

## 3. WhisperX와의 비교

| 구분 | WhisperX (pyannote.audio) | 본 프로젝트 (LLM diarization) |
| --- | --- | --- |
| 화자 분리 방식 | 음향 임베딩(목소리 특징) 기반 클러스터링 | 전사 텍스트의 의미·맥락 기반 LLM 추론 |
| 음향적 정확도 | 높음(실제 목소리로 구분) | 낮음(텍스트 단서에만 의존) |
| GPU 필요 여부 | 권장(임베딩/정렬 연산) | 불필요(API 호출만 수행) |
| ffmpeg 의존 | 필요(로컬 디코딩) | 불필요(파일 업로드 방식) |
| 비용 | 모델 구동/GPU 비용 | Whisper + GPT API 호출 비용(상대적으로 낮음) |
| 동시 발화/짧은 맞장구 | 음향으로 구분 가능 | 텍스트만으로는 구분이 어려울 수 있음 |
| 구축 난이도 | 높음(모델·의존성 설치) | 낮음(`openai`, `pandas`만 필요) |

요약: 음향 정밀도가 중요하면 WhisperX가 유리하나, GPU·ffmpeg 없이 가볍게 맥락 기반 분리를 원하면  
본 프로젝트가 적합함.

## 4. 가상환경 구성

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## 5. 실행 방법

```bat
python stt.py
```

- 실행 시 화자별 샘플 발화를 보여주고 실명을 입력받음(대화형). Enter 입력 시 기본 라벨(화자A/B)을 유지함.
- 파이프 등 비대화형 환경에서는 실명 입력을 자동으로 건너뛰고 기본 라벨을 유지함.
- 화자 분리 모델은 환경변수 `OPENAI_DIARIZE_MODEL`로 교체 가능(기본값 `gpt-4o`).
- `OPENAI_API_KEY`는 상위 경로의 `.env`(`KEY=value` 또는 `KEY: value`)에서 자동 로딩함.

## 6. 출력 파일 형식

### result.txt
- 원본 변환 결과를 보존함. 실명 미반영(기본 라벨 화자A/B 유지). UTF-8 인코딩.
- 형식: `[MM:SS] 화자X: 텍스트` (한 줄당 한 발화)

### result_chunks.csv
- 구분자 `|`, 헤더 포함, 인덱스 없음, `utf-8-sig` 인코딩(Excel 한글 호환).
- 컬럼: `time|speaker|text` (speaker는 실명 반영)
- 예: `00:03|아내|어디야 지금`

### result.json
- 실명 반영 JSON. `ensure_ascii=False`, `indent=2`.
- 구조:

```json
{
  "audio_file": "phone-with-wife.mp3",
  "diarize_model": "gpt-4o",
  "transcribe_model": "whisper-1",
  "speakers": { "화자A": "남편", "화자B": "아내" },
  "utterances": [
    { "time": "00:00", "speaker": "남편", "text": "여보세요" }
  ]
}
```
