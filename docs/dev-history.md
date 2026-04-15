# doQment 개발 히스토리

YouTube transcript를 로컬 LLM으로 분석해 상세 Markdown 노트를 생성하는 서비스.  
각 작업은 **문제 인식 → 해결 방법 → 결과** 순으로 기록.

---

## 2026-04-15

### 프로젝트 초기 구성
**문제:** YouTube transcript를 LLM으로 노트로 변환하는 파이프라인이 필요했음.  
**해결:** FastAPI 웹 서버 + Ollama(gemma4:e4b) + SSE 실시간 스트리밍 구조로 설계.  
**결과:**
- `transcript.py`: YouTube 자막 수집 (수동 자막 우선, 자동생성 자막 폴백)
- `chunker.py`: 슬라이딩 윈도우 방식으로 청크 분할 (overlap_ratio=0.20)
- `processor.py`: Ollama 연동 LLM 노트 추출
- `aggregator.py`: 청크 노트 병합
- `renderer.py`: Markdown 파일 렌더링
- `web.py`: FastAPI SSE 스트리밍 서버
- `static/index.html`: 단일 파일 웹 UI

---

### 시맨틱 청킹 구현 (임베딩 기반 주제 경계 감지)
**문제:** 단어 수 기반 기계적 분할 → 주제 흐름과 무관하게 잘림 → 섹션 제목이 내용과 맞지 않음.  
**해결:** `sentence-transformers`의 `all-MiniLM-L6-v2` 모델로 임베딩 생성.
- 슬라이딩 윈도우(k=5)로 좌우 평균 임베딩의 코사인 거리 계산
- 85th percentile 초과 지점 = 주제 경계 → 여기서 청크 분할
- 퍼센타일 기반 임계값으로 영상마다 자동 적응
- `sentence-transformers` 미설치 시 단어 수 방식으로 자동 폴백

**결과:**
- `chunker.py` 전면 개선: `find_semantic_boundaries()` 신규 함수
- `TranscriptChunk`에 `split_reason` 필드 추가 (`"semantic"` / `"max_words"` / `"end"`)
- `pyproject.toml`에 `sentence-transformers>=3.0`, `numpy>=1.24` 추가

---

### 청킹 진행상황 UI 개선
**문제:** 시맨틱 vs 단어수 방식 중 어느 걸 사용했는지 UI에서 파악 불가.  
**해결:** Step 2 SSE 이벤트를 두 단계로 분리.
- "시맨틱 임베딩 중..." → 완료 시 `chunking_method`, `chunk_stats` 포함한 done 이벤트
- 청크별 이벤트에 `split_reason`, `word_count` 추가
- UI에서 `bd-semantic`, `bd-forced`, `bd-fallback` 배지 표시

---

### "Untitled Section" 실패 대응 — JSON 복구 + 재시도 정책
**문제:** LLM 반복 루프 → `num_predict` 제한으로 JSON 잘림 → 파싱 실패 → `_EMPTY_NOTES` 폴백 → "Untitled Section" 출력.  
**해결:**
1. `_rescue_partial_json()`: 정규식으로 잘린 JSON에서 핵심 필드 직접 추출
2. 3단계 파싱: 표준 JSON → rescue → raw 텍스트 보존 폴백
3. `_is_failed()` 감지 시 `repeat_penalty` 상향(1.3 → 1.5 → 1.7)으로 최대 2회 재시도
4. **근본 해결**: `num_predict=-1` → EOS 토큰까지 자연 생성 (JSON 잘림 원인 제거)

---

### 청크 크기 입력 제거
**문제:** 시맨틱 청킹으로 전환했는데 UI에 여전히 "청크 크기" 입력 필드가 존재 → 혼란.  
**해결:** UI에서 청크 크기 입력 제거, 내부 상수 `_MAX_CHUNK_WORDS=4500`으로 고정.  
**이유:** 컨텍스트 윈도우(8192) 기준, 프롬프트+출력 공간 제외 후 ~4500단어가 안전 상한.

---

### 노트 품질 개선 — 압축 금지 / 발화 충실 변환
**문제:** LLM이 내용을 요약·압축해 중요한 세부사항이 사라짐. 경쟁사(LilysAI) 수준에 미치지 못함.  
**핵심 인사이트:** LilysAI는 요약하지 않고 각 발화를 1:1로 한국어 문장으로 변환.  
**해결:** `prompts/extract_notes.txt` 전면 개편.
- "Do NOT summarize. Do NOT skip anything. If the speaker said 10 things, write 10 bullets."
- `####` 소주제 헤더 → 볼드 그룹 → 4칸 들여쓰기 서브 불릿 구조
- `summary` 필드: 구간 흐름 1~2문장 단락 (별도 섹션 제목 아래 표시)
- `renderer.py`에서 summary 단락 렌더링 추가

**UI 수정:**
- `####` H4 CSS 스타일 추가 (`border-left: 3px solid var(--accent)`)
- `md2html()`에서 `####` 체크를 `###` 보다 먼저 처리 (중요: 순서 오류 시 `####`가 `###`로 렌더링됨)

---

### 2시간 영상 지원 점검
**점검 결과 — 알려진 위험:**
1. **SSE 타임아웃**: 청크당 LLM 처리 3~5분 동안 이벤트 없음 → 브라우저/프록시 60초 idle 타임아웃으로 연결 끊김 (미해결)
2. **num_ctx 출력 예산**: 4500단어 청크 → 입력 토큰 ~6000 → num_ctx=8192에서 출력 ~2192토큰만 남음 (부분적 위험)
3. **재시작 불가**: 중간 실패 시 처음부터 재처리 (체크포인트 미구현)

---

## 향후 과제

- [ ] SSE keepalive 이벤트: LLM 처리 중 30초마다 ping 이벤트 전송 (타임아웃 방지)
- [ ] 청크 체크포인트: `output/.cache/VIDEO_ID/` 에 청크별 결과 저장 → 재시작 시 복원
- [ ] num_ctx 동적 조정: 청크 단어 수 기반으로 num_ctx 확장 (예: 단어 수 × 1.5 + 2048)
- [ ] 병렬 처리: 현재 순차 처리, context_summary 의존성으로 완전 병렬화 불가 (파이프라인 구조 검토)
