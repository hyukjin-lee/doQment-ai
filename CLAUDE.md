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
│   ├── chunker.py         # 시맨틱 임베딩 기반 청킹
│   ├── processor.py       # Ollama LLM 노트 추출 + 재시도 정책
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
  → transcript.extract_video_id()          # URL → video ID
  → transcript.fetch_transcript()          # YouTube API → 세그먼트 목록
  → chunker.chunk_transcript()             # 시맨틱 임베딩 기반 주제 단위 분할
  → processor.NoteProcessor.process_chunk()  # Gemma LLM 청크별 호출 (재시도 포함)
  → aggregator.aggregate()                 # 청크 노트 병합
  → renderer.render_markdown()             # Markdown 문자열 생성
  → renderer.save_markdown()               # output/ 에 .md 저장
```

---

## 로컬 개발 환경 설정

```bash
# 1. 가상환경 생성 및 활성화
python3 -m venv .venv
source .venv/bin/activate

# 2. 패키지 설치 (웹 의존성 포함)
pip install -e ".[web]"
# 첫 실행 시 all-MiniLM-L6-v2 임베딩 모델 자동 다운로드 (~90MB)

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
- JSON 스키마: `section_title`, `key_points`, `context_summary`
- 서버 재시작 없이 파일 수정으로 즉시 반영됨 (핫리로드)

### `doqment/processor.py` — LLM 연동 + 재시도 정책
- `NoteProcessor.__init__()`: 모델, temperature, num_ctx, repeat_penalty 설정
- `temperature=0.2`: 낮을수록 사실 추출에 집중
- `num_ctx=8192`: Gemma3:4b 기준 적정값
- `repeat_penalty=1.3`, `repeat_last_n=128`: 반복 루프 억제
- `_call_model()`: LLM 호출 (format=json 실패 시 일반 모드로 재시도)
- `_rescue_partial_json()`: JSON이 잘린 경우 정규식으로 핵심 필드 추출
- `_parse_json()`: 3단계 파싱 (정상 → 잘린 JSON 복구 → raw 텍스트 폴백)
- `_is_failed()`: 실패 감지 (`section_title == "Untitled Section"` OR 빈 `key_points`)
- `process_chunk()`: **최대 3회 시도** — 실패 시 `repeat_penalty` 강화 (1.3 → 1.5 → 1.7)

### `doqment/chunker.py` — 시맨틱 청킹
- `all-MiniLM-L6-v2` (~90MB) 임베딩 모델로 각 세그먼트를 벡터화
- 슬라이딩 윈도우(window=5)로 좌우 평균 임베딩 코사인 거리 계산
- 거리 상위 15%(85th percentile) 위치 = 주제 경계 → 여기서 분할
- `min_words=300`: 이 미만이면 경계가 있어도 분할하지 않음
- `max_words=2000`: 경계가 없으면 강제 분할
- `split_reason` 필드: `"semantic"` | `"max_words"` | `"end"` (디버깅 및 UI 표시용)
- `sentence-transformers` 미설치 시 단어 수 방식으로 자동 폴백
- 오버랩 없음 (시맨틱 경계에서 자르므로 문맥 중단 없음)

### `doqment/renderer.py` — Markdown 출력
- 섹션별 핵심 포인트를 계층적 불렛 구조로 렌더링
- 파일명: `YYYY-MM-DD_video-title-slug.md`
- 저장 위치: `output/` 디렉토리

### `doqment/web.py` — FastAPI 웹 서버
- `GET /` → `static/index.html` 서빙
- `POST /api/languages` → transcript 언어 목록 반환
- `GET /api/models` → 설치된 Ollama 모델 목록 반환
- `POST /api/generate` → **SSE 스트리밍**으로 노트 생성 진행상황 실시간 전달
  - `progress` 이벤트: 각 단계 완료 상태 (`step`, `message`, `detail`, `done`)
    - step 2 완료 시 `chunking_method`(`"semantic"` | `"fallback"`), `chunk_stats`(breakdown) 포함
  - `chunk` 이벤트: 청크별 처리 진행 — `index`, `total`, `start_ts`, `end_ts`, `split_reason`, `word_count`
  - `done` 이벤트: 최종 Markdown + 다운로드 URL + 통계
  - `error` 이벤트: 에러 메시지

### `static/index.html` — 웹 UI
- 단일 파일 (HTML + CSS + JS, 외부 의존성 없음)
- 좌측: 설정 패널 (URL, 언어, 모델, 제목, 청크 최대 크기)
- 우측: 실시간 Markdown 미리보기 / Raw 탭
- **청킹 진행 상황 상세 표시**:
  - Step 2에서 임베딩 시작 메시지 + 완료 후 방식 배지 표시
  - `[시맨틱 경계 N]` `[단어 수 초과 N]` 배지로 청킹 결과 한눈에 확인
  - 각 청크 처리 시 `시맨틱 경계 / 단어 수 초과 / 마지막` 태그 + 단어 수 표시
  - 폴백 시 `[단어 수 분할 N (임베딩 모델 없음)]` 배지

### `doqment/transcript.py` — YouTube API
- `youtube-transcript-api v1.0+` 사용 (인스턴스 방식: `YouTubeTranscriptApi()`)
- 수동 자막 우선, 없으면 자동생성 자막 사용
- `[Music]`, `(inaudible)` 등 노이즈 자동 제거

---

## 자주 하는 작업

### 노트 품질 개선
1. `prompts/extract_notes.txt` 수정 (서버 재시작 불필요)
2. JSON 스키마에 새 필드 추가 시 `doqment/renderer.py`에서 해당 필드 렌더링 추가
3. `doqment/aggregator.py`에서 새 필드를 `AggregatedNotes`에 추가

### 재시도 정책 조정
`processor.py`의 `process_chunk()` 내 루프 조건 수정:
- 재시도 횟수: `range(3)` → 원하는 값
- 강화 폭: `1.3 + attempt * 0.2` 수식 조정

### 청킹 민감도 조정
`chunker.chunk_transcript()` 파라미터:
- `percentile=85.0` → 낮추면 경계 증가(섹션 많음), 높이면 경계 감소(섹션 적음)
- `min_words=300`, `max_words=2000` 조정

### 새 Ollama 모델 지원
```bash
ollama pull <model-name>
python -m doqment "URL" --model <model-name>
```

### 웹 UI 수정
- `static/index.html`: 단일 파일로 모든 UI 포함 (HTML + CSS + JS)
- `doqment/web.py`: FastAPI 엔드포인트

---

## 의존성

| 패키지 | 용도 |
|--------|------|
| `youtube-transcript-api` | YouTube 자막 수집 |
| `ollama` | 로컬 LLM 연동 (Gemma3/4) |
| `sentence-transformers` | all-MiniLM-L6-v2 임베딩 (시맨틱 청킹) |
| `numpy` | 코사인 거리 계산 |
| `typer` + `rich` | CLI 인터페이스 |
| `fastapi` + `uvicorn` | 웹 서버 |
| `python-slugify` | 파일명 생성 |

---

## 알려진 제약사항

- **Transcript 없는 영상 불가**: transcript가 비활성화된 영상은 처리 불가
- **Ollama 0.20.0**: `gemma4:4b` 미지원 → `gemma3:4b` 사용. Ollama 업그레이드 후 gemma4 사용 가능
- **처리 시간**: 1시간 영상 기준 gemma3:4b로 약 10~20분 소요 (GPU 성능에 따라 다름)
- **첫 실행 시 임베딩 모델 다운로드**: `all-MiniLM-L6-v2` (~90MB), `~/.cache/huggingface/`에 캐시
- **언어**: transcript 언어와 LLM 출력 언어는 별개. 프롬프트에 한국어 강제 지시 포함

---

## 테스트

```bash
# 짧은 영상으로 빠른 동작 확인 (19초 영상)
python -m doqment "https://www.youtube.com/watch?v=jNQXAC9IVRw"

# 시맨틱 청킹 결과 확인 (split_reason 및 단어 수 출력)
.venv/bin/python -c "
from doqment.transcript import fetch_transcript, extract_video_id
from doqment.chunker import chunk_transcript

video_id = extract_video_id('https://www.youtube.com/watch?v=VIDEO_ID')
segs = fetch_transcript(video_id, ['en'])
chunks = chunk_transcript(segs)
for c in chunks:
    print(f'[{c.split_reason:10s}] {c.start_ts}→{c.end_ts}  {len(c.text.split())}단어')
"

# 웹 서버 시작 후 브라우저 확인
python -m doqment.web
```
