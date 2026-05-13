# CLAUDE.md

Claude Code 작업 시 자동으로 읽히는 프로젝트 컨텍스트.

## 프로젝트 개요

LACEROOM (네이버 스마트스토어) 광고 운영 데이터 + 판매 데이터를 통합 분석해
정비 의사결정을 돕는 도구.

- **광고 분석 탭**: 네이버 검색광고 엑셀/CSV 분석 → 광고 정비 권장 (중복/다채널 자동 분류)
- **스마트스토어 분석 탭**: 통계 8개 .xlsx 일괄 업로드 → 정비 액션 카드 + 베스트셀러 드릴다운
- **API 동기화**: `tools/fetch_naver_ads.py` (네이버 SA API 비동기 호출)

## 파일 구조

```
ad-analytics-app/
├── index.html                     ← 메인 SPA (React 18 CDN + Tailwind, 빌드 없음)
├── tools/
│   └── fetch_naver_ads.py         ← 네이버 SA API 동기화 스크립트
├── naver_credentials.template.json ← 자격증명 템플릿
├── naver_credentials.json         ← 실제 키 (gitignored, 절대 커밋 금지)
├── samples/                       ← API 출력 + 사용자 데이터 (gitignored)
└── .gitignore
```

## 핵심 규칙 (반드시 지킬 것)

### 1. 버전 관리 (`vYYYYMMDD.NN` 형식)

`index.html`의 `APP_VERSION` 상수는 매 수정 시 +1.
- `YYYYMMDD`: 수정한 날짜
- `NN`: 그 날짜의 N번째 수정 (01부터 zero-pad)
- 날짜 바뀌면 NN을 01로 리셋

**카운트 대상:** `index.html` 만. README/CLAUDE.md/.gitignore/tools 등은 카운트 안 함.

### 2. 매 응답에 로컬 URL + 버전 + 링크 표시

`index.html` 수정/추가 완료 시 응답 끝에:

```
| 항목 | 값 |
|------|-----|
| 로컬 | http://localhost:8000/ |
| 버전 | vYYYYMMDD.NN |
| 링크 | [열기](http://localhost:8000/) |
```

코드 변경 없이 메타 파일만 수정한 경우 생략 가능.

### 3. 보안: 자격증명 절대 커밋 금지

- `naver_credentials.json` (실제 API 키)
- `samples/*.xlsx` (사용자 매출 데이터)
- `samples/naver_api_*.json/csv/tsv` (네이버 API 응답)

모두 `.gitignore`로 보호 중. 새 파일 추가 시 `.gitignore` 확인.

### 4. 디자인 시스템 (LACEROOM 스타일)

- 흑백 미니멀: `bg-black text-white` 활성 / `bg-neutral-100` hover
- 폰트: `font-black tracking-tight`, 대문자에 `tracking-widest`
- 모서리: `style={{borderRadius:'2px'}}` (거의 직각)
- 컬러 강조: 녹색=좋음, 황색=점검, 적색=문제
- 광고 타입별 뱃지 색상: 파워링크(회색) / 쇼핑검색(파랑) / 쇼핑브랜드(보라) / 카탈로그(분홍) / 브랜드검색(황)

## 매일 사용 워크플로우

### 광고 데이터 (자동)
```powershell
python tools/fetch_naver_ads.py --days 12 --ad-report
```
→ `samples/naver_api_export_YYYYMMDD.csv` 생성 → 광고 분석 탭에 드래그

### 스마트스토어 데이터 (수동)
스마트스토어센터 → 통계 → 8개 보고서 (.xlsx) 다운로드 → 스마트스토어 분석 탭에 드래그

### 첫 사용 / 새 PC
1. `naver_credentials.template.json` → `naver_credentials.json` 복사
2. `searchad.naver.com → 도구 → API 사용 관리` 에서 키 가져와 입력
3. 사이드바 설정 탭에서 Anthropic API 키 등록 (스크린샷 분석용, 선택)

## 진행 상황 (v20260513.20 기준)

- ✅ 광고 분석: 광고그룹 효율 분석 + 드릴다운 + 광고 정비 권장 (중복/다채널)
- ✅ 권장 액션: 체크박스 적용 + 효과 자동 평가 (snapshot vs 현재)
- ✅ 스마트스토어 분석: 정비 액션 카드 + 베스트셀러 + 인구통계 + 시간대
- ✅ 분석 히스토리: 저장 / 적용 권장 액션 이력
- ✅ 네이버 SA API: 캠페인/그룹/키워드 + 키워드 통계 + 광고그룹 통계 + AD 보고서 (디바이스 분리)
- ⏳ Phase 3: 스크린샷 AI 분석 (Claude Vision, 미구현)
- ⏳ Phase 4: 광고 효율 리포트 탭 (미구현 — 현재 광고 분석 탭에 통합)

## 알려진 한계

- AD 보고서 13/14열 의미 미확인 (raw TSV는 `samples/` 저장)
- 쇼핑검색 검색어 텍스트 API 미공개 — 광고센터 UI에서 수동 다운로드 후 업로드
- localStorage는 브라우저별 분리 — 동기화 필요 시 [내보내기] / [가져오기] 사용

## 배포

- GitHub Pages: https://yslike1399-maker.github.io/ad-analytics-app/
- 로컬: `python -m http.server 8000` → `http://localhost:8000/`
