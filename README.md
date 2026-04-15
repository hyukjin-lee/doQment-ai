# doQment

> YouTube 영상을 **압축 없이**, 각 발화를 라인별로 충실하게 변환하는 로컬 LLM 노트 생성기

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)](https://www.python.org/)
[![Ollama](https://img.shields.io/badge/Ollama-gemma4%3Ae4b-black?logo=ollama)](https://ollama.com/)
[![FastAPI](https://img.shields.io/badge/FastAPI-SSE%20Streaming-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

![doQment 웹 UI 스크린샷](docs/screenshot.png)

---

## 소개

doQment는 YouTube URL 하나만 입력하면, 로컬 LLM(Gemma4)이 영상의 transcript를 읽고 **요약 없이 발화 전체를 한국어 상세 노트**로 변환해주는 도구입니다.

- **완전 로컬 실행** — API 키 불필요, 인터넷에 데이터 전송 없음
- **요약하지 않음** — 발표자가 말한 모든 내용을 1:1로 문장 변환
- **실시간 스트리밍** — SSE(Server-Sent Events)로 처리 과정을 실시간 확인
- **Markdown 출력** — 목차, 섹션 헤더, 계층형 불릿 구조로 즉시 활용 가능

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| **YouTube Transcript 수집** | 수동 자막 우선, 없으면 자동 생성 자막 사용 |
| **시맨틱 청킹** | `all-MiniLM-L6-v2` 임베딩 기반 주제 경계 감지 후 분할 |
| **LLM 노트 추출** | Ollama(Gemma4) 로컬 LLM으로 청크별 노트 추출 |
| **자동 재시도** | JSON 파싱 실패 시 `repeat_penalty` 강화 후 최대 3회 재시도 |
| **실시간 스트리밍** | 각 처리 단계를 WebUI에 실시간으로 피드백 |
| **Markdown 저장** | `output/YYYY-MM-DD_제목.md` 형식으로 자동 저장 및 다운로드 |
