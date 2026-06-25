# VLM 메뉴판 설명 앱

## 개요

### 목적
메뉴판 사진을 업로드하면 Google Gemini Vision이 메뉴를 자동 분석하여  
워드(docx) 형식의 메뉴 설명서를 생성하는 VLM 학습용 앱.

### 주요 기능
| 기능 | 설명 |
|------|------|
| 이미지 분석 | Gemini 2.5 Flash 모델로 메뉴판 이미지 인식 |
| 정보 추출 | 메뉴명·가격·설명 자동 파싱 |
| 설명 생성 | 재밌고 센스있는 메뉴 설명 자동 작성 |
| 문서 출력 | `results/{이미지파일명}.docx` 저장 |

---

## 소스 구성

```
VLM/
├── vlm.py           # 메인 실행 파일
├── requirements.txt # 의존성 목록
├── README.md        # 본 문서
└── results/         # 생성된 docx 출력 디렉토리
```

### `vlm.py` 주요 함수

| 함수 | 역할 |
|------|------|
| `get_mime_type()` | 파일 확장자로 MIME 타입 결정 |
| `analyze_menu()` | Gemini Vision API 호출, JSON 응답 파싱 |
| `create_docx()` | python-docx로 메뉴 설명서 생성 |
| `main()` | CLI 진입점 — 경로 입력 → 분석 → docx 저장 |

---

## 가상환경 설정

```bash
# 1. 가상환경 생성
python -m venv .venv

# 2. 활성화
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# 3. 의존성 설치
pip install -r requirements.txt
```

### API 키 설정
`hands-on/.env` 파일에 Gemini API 키 추가:

```env
GEMINI_API_KEY=your_gemini_api_key_here
```

> Gemini API 키 발급: https://aistudio.google.com/app/apikey

---

## 실행 방법

### 방법 1: 인수로 경로 전달
```bash
python vlm.py 이미지경로.jpg
```

### 방법 2: 대화형 입력
```bash
python vlm.py
# 프롬프트에 경로 입력
이미지 경로를 입력하세요: C:\Users\...\menu.jpg
```

### 실행 예시
```
이미지 분석 중: menu.jpg
  Gemini Vision API 호출 중...

완료! 설명서 저장: results/menu.docx
발견된 메뉴 8개:
  - 된장찌개  (8,000원)
  - 삼겹살   (15,000원)
  ...
```

### 출력 파일 위치
```
VLM/results/{이미지파일명}.docx
```

---

## 지원 이미지 형식
`jpg`, `jpeg`, `png`, `gif`, `webp`
