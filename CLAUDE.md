# doQment — Claude Code 개발 가이드

YouTube transcript를 로컬 LLM(Gemma3/4)으로 분석해 상세 Markdown 노트를 생성하는 서비스.  
lilysAI처럼 압축 없이 라인별 상세 노트를 만드는 것이 핵심 목표.

---

## 프로젝트 구조

```
doQment/
├── doqment/               # 핵심 Python 패키지
│   ├── cli.py             # Typer CLI 진입점 (python -m doqment)
│   ├── web.py             # FastAPI 웹 UI 서버 (SSE 실시간 스트리밍)
│   ├── transcript.py      # YouTube transcript 수집·전처리
│   ├── chunker.py         # 슬라이딩 윈도우 청킹
│   ├── processor.py       # Ollama LLM 노트 추출 (핵심 로직)
│   ├── aggregator.py      # 청크별 노트 병합
│   └── renderer.py        # Markdown 파일 렌더링
├── prompts/
│   └── extract_notes.txt  # LLM 노트 추출 프롬프트 (수정으로 품질 개선 가능)
├── static/
│   └── index.html         # 웹 UI (단일 HTML + CSS + JS 파일)
├── output/                # 생성된 .md 파일 저장
├── pyproject.toml         # 의존성 및 패키지 설정
└── .venv/                 # 가상환경 (git 제외)
```

---

## 핵심 파이프라인 흐름

```
URL 입력
  → transcript.extract_video_id()       # URL → video ID
  → transcript.fetch_transcript()       # YouTube API → 세그먼트 목록
  → chunker.chunk_transcript()          # 슬라이딩 윈도우 분할 (기본 1500 단어/청크)
  → processor.NoteProcessor.process_all_chunks()  # Gemma LLM 순차 호출
  → aggregator.aggregate()              # 청크 노트 병합
  → renderer.render_markdown()          # Markdown 문자열 생성
  → renderer.save_markdown()            # output/ 에 .md 저장
```

---

## 로컬 개발 환경 설정

```bash
# 1. 가상환경 생성 및 활성화
python3 -m venv .venv
source .venv/bin/activate

# 2. 패키지 설치 (웹 의존성 포함)
pip install -e ".[web]"

# 3. Ollama 서버 시작 (별도 터미널)
ollama serve

# 4. 모델 설치 (최초 1회, 3.3GB)
ollama pull gemma3:4b
# ollama pull gemma4:4b  ← Ollama 최신 버전으로 업그레이드 후 사용 가능

# 5. 실행
python -m doqment.web          # 웹 UI → http://localhost:8000
python -m doqment "URL"        # CLI
```

---

## 실행 방법

### 웹 UI (권장)
```bash
python -m doqment.web
# → http://localhost:8000 브라우저에서 열기
```

### CLI
```bash
# 기본 사용
python -m doqment "https://www.youtube.com/watch?v=VIDEO_ID"

# 한국어 영상
python -m doqment "URL" --lang ko

# 노트 제목 지정
python -m doqment "URL" --title "내 노트 제목"

# 모델 변경
python -m doqment "URL" --model gemma4:4b

# 사용 가능한 언어 확인
python -m doqment "URL" --list-langs
```

---

## 모듈별 역할 및 수정 포인트

### `prompts/extract_notes.txt` — 노트 품질의 핵심
LLM에게 전달하는 프롬프트. **노트 품질을 개선하려면 여기를 먼저 수정.**

- `{start_ts}`, `{end_ts}`: 청크 시작/종료 타임스탬프
- `{chunk_index}`, `{total_chunks}`: 현재/전체 청크 번호
- `{transcript_text}`: 실제 transcript 텍스트
- `{previous_context}`: 이전 청크 요약 (연속성 유지용)
- JSON 스키마: `section_title`, `key_points`, `notable_quotes`, `definitions`, `examples_mentioned`, `context_summary`

### `doqment/processor.py` — LLM 연동
- `NoteProcessor.__init__()`: 모델, 온도(temperature), 컨텍스트 크기 설정
- `temperature=0.2`: 낮을수록 사실 추출에 집중 (창의성 낮춤)
- `num_ctx=8192`: Gemma3:4b 기준 적정값
- `_parse_json()`: LLM 출력이 깨진 JSON일 때 복구 로직

### `doqment/chunker.py` — 청킹 전략 (시맨틱)
- `all-MiniLM-L6-v2` (33MB) 임베딩 모델로 각 세그먼트를 임베딩
- 슬라이딩 윈도우(window=5)로 좌우 평균 임베딩 코사인 거리 계산
- 거리 상위 15%(85th percentile) 위치 = 주제 경계 → 여기서 분할
- `min_words=300`: 이 미만이면 경계에서도 분할하지 않음
- `max_words=2000`: 경계가 안 나오면 강제 분할
- `split_reason` 필드: `"semantic"` | `"max_words"` | `"end"` (디버깅용)
- `sentence-transformers` 미설치 시 단어 수 방식으로 자동 폴백
- 오버랩 제거: 시맨틱 경계 자체가 자연스러운 맥락 단절점이므로 불필요

### `doqment/renderer.py` — Markdown 출력
- 섹션별 핵심 포인트, 인용, 예시, 용어 정리를 포함한 구조화된 .md 생성
- 파일명: `YYYY-MM-DD_video-title-slug.md`
- 저장 위치: `output/` 디렉토리

### `doqment/web.py` — FastAPI 웹 서버
- `GET /` → `static/index.html` 서빙
- `POST /api/languages` → transcript 언어 목록 반환
- `GET /api/models` → 설치된 Ollama 모델 목록 반환
- `POST /api/generate` → **SSE 스트리밍**으로 노트 생성 진행상황 실시간 전달
  - `progress` 이벤트: 각 단계 완료 상태
  - `chunk` 이벤트: 청크별 처리 진행 (진행 바 업데이트)
  - `done` 이벤트: 최종 Markdown + 다운로드 URL + 통계
  - `error` 이벤트: 에러 메시지

### `static/index.html` — 웹 UI
- 단일 파일 (HTML + CSS + JS, 외부 의존성 없음)
- 좌측: 설정 패널 (URL, 언어, 모델, 제목, 청크 크기)
- 우측: 실시간 Markdown 미리보기 / Raw 탭
- SSE로 진행 상황 실시간 업데이트, Markdown 다운로드 버튼

### `doqment/transcript.py` — YouTube API
- `youtube-transcript-api v1.0+` 사용 (인스턴스 방식: `YouTubeTranscriptApi()`)
- 수동 자막 우선, 없으면 자동생성 자막 사용
- `[Music]`, `(inaudible)` 등 노이즈 자동 제거

---

## 자주 하는 작업

### 노트 품질 개선
1. `prompts/extract_notes.txt` 수정
2. JSON 스키마에 새 필드 추가 시 `doqment/renderer.py`에서 해당 필드 렌더링 추가
3. `doqment/aggregator.py`에서 새 필드를 `AggregatedNotes`에 추가

### 새 Ollama 모델 지원
```bash
ollama pull <model-name>
python -m doqment "URL" --model <model-name>
# 또는 processor.py의 기본값 변경
```

### 웹 UI 수정
- `static/index.html`: 단일 파일로 모든 UI 포함 (HTML + CSS + JS)
- `doqment/web.py`: FastAPI 엔드포인트

### 출력 형식 추가 (예: JSON, HTML)
1. `doqment/renderer.py`에 새 `render_*()` 함수 추가
2. `doqment/web.py`의 `/generate` 엔드포인트에 format 파라미터 추가
3. 웹 UI에 포맷 선택 드롭다운 추가

---

## 의존성

| 패키지 | 용도 |
|--------|------|
| `youtube-transcript-api` | YouTube 자막 수집 |
| `ollama` | 로컬 LLM 연동 (Gemma3/4) |
| `typer` + `rich` | CLI 인터페이스 |
| `fastapi` + `uvicorn` | 웹 서버 |
| `python-slugify` | 파일명 생성 |

---

## 알려진 제약사항

- **Transcript 없는 영상 불가**: transcript가 비활성화된 영상은 처리 불가
- **Ollama 0.20.0**: `gemma4:4b` 미지원 → `gemma3:4b` 사용. Ollama 업그레이드 후 gemma4 사용 가능
- **처리 시간**: 1시간 영상 기준 gemma3:4b로 약 10~20분 소요 (GPU 성능에 따라 다름)
- **언어**: transcript 언어와 LLM 출력 언어는 별개. 한국어 영상 + 영어 노트가 기본

---

## 테스트

```bash
# 짧은 영상으로 빠른 동작 확인 (19초 영상)
python -m doqment "https://www.youtube.com/watch?v=jNQXAC9IVRw"

# 웹 서버 시작 후 브라우저 확인
python -m doqment.web
```
