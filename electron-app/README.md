# File Tidier Electron

기존 `file_tidier.py`는 유지하고, Electron 화면만 별도 폴더로 분리한 버전입니다.

## 실행

```powershell
cd electron-app
pnpm.cmd install
pnpm.cmd start
```

설치가 끝난 뒤에는 프로젝트 루트의 `FileTidier-Electron.vbs`를 더블클릭해 실행할 수 있습니다.

## 구조

- `main.js`: Electron 메인 프로세스, 폴더 선택과 Python 백엔드 실행
- `preload.js`: 렌더러와 메인 프로세스 사이의 안전한 API
- `renderer/`: 토스 스타일 화면
- `../file_tidier_backend.py`: 기존 Python 기능을 JSON으로 내보내는 백엔드

## 현재 연결된 기능

- 폴더 선택
- 파일 목록 스캔
- 크기 기준 중복 찾기
- 내용 해시 기준 중복 찾기
- 문장 중복 찾기
- 참조 zip 문장 확인
- 제목 중복 스캔
- 이름 변경 미리보기
- 확장자 표시 및 필터
- zip/cbz 내부 포함 옵션
- 하위 폴더 포함 옵션
- 표시 최대 개수
- 스캔 중지
- 스킵된 압축파일 목록 표시
- 제목 규칙 미리보기

## 참조 zip 문장 확인

1. `참조 zip`에서 기준으로 삼을 zip 파일을 선택합니다.
2. `선택 폴더`에서 검사할 폴더를 선택합니다.
3. 모드를 `참조 문장 확인`으로 바꿉니다.
4. `zip/cbz 내부 포함`을 켜면 검사 폴더 안의 압축파일 내부 epub/txt까지 확인합니다.
5. 결과의 `일치 문장`, `일치율`, `일치 문장 프리뷰`를 확인합니다.
