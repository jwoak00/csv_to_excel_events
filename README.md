# CSV to Excel Events Converter

CSV 파일에서 이벤트 데이터를 읽어 속도 카메라 정보와 매칭하여 Excel 파일로 변환하는 Python 스크립트입니다.

## 주요 기능

- **이벤트 데이터 집계**: CSV 파일에서 특정 이벤트 코드(81-85)를 필터링하고 집계
- **속도 카메라 매칭**: SQLite DB 또는 CSV에서 카메라 정보를 로드하여 GPS 좌표 및 진행 방향 기반 매칭
- **과속 분류**: 제한 속도 대비 실제 속도를 분석하여 4단계로 분류
- **월별 시트 분리**: Excel 파일을 월별로 자동 분리하여 저장
- **배치 처리**: 디렉터리 내 여러 CSV 파일을 일괄 처리

## 시스템 요구사항

### 필수 라이브러리
```bash
pip install pandas openpyxl
```

### 선택 사항 (SpatiaLite 지원)
- SQLite 카메라 DB 사용 시 SpatiaLite 확장 모듈 필요
- Windows: `mod_spatialite.dll`
- Linux: `mod_spatialite.so` / `libspatialite.so`
- macOS: `mod_spatialite.dylib`

## 설치 및 설정

### 1. 기본 구조
```
프로젝트_폴더/
├── csv_to_excel_events.py
├── SQLite/
│   └── 20250602.sqlite          # 기본 카메라 DB
├── input_table.csv              # 기본 카메라 CSV
├── BTO_output/                  # 기본 출력 폴더
└── 입력_CSV_파일들/
```

### 2. SpatiaLite 설정 (선택)
환경 변수 설정:
```bash
# Windows
set SPATIALITE_LIBRARY_PATH=C:\path\to\mod_spatialite.dll

# Linux/Mac
export SPATIALITE_LIBRARY_PATH=/path/to/libspatialite.so
```

## 사용 방법

### 기본 사용법

#### 단일 파일 처리
```bash
python csv_to_excel_events.py --input input.csv
```

#### 디렉터리 일괄 처리
```bash
python csv_to_excel_events.py --input-dir ./csv_folder
```

### 고급 옵션

#### 카메라 정보 지정
```bash
# SQLite DB 사용
python csv_to_excel_events.py --input data.csv --cam-db ./SQLite/cameras.sqlite

# CSV 파일 사용
python csv_to_excel_events.py --input data.csv --cam-csv cameras.csv

# 테이블 명시
python csv_to_excel_events.py --input data.csv --cam-db db.sqlite --cam-table 250602
```

#### 출력 디렉터리 지정
```bash
python csv_to_excel_events.py --input data.csv --output-dir ./results
```

#### SpatiaLite 확장 모듈 지정
```bash
python csv_to_excel_events.py --input data.csv --spatialite /path/to/mod_spatialite.dll
```

### 명령행 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--input`, `-i` | 단일 CSV 파일 경로 | - |
| `--input-dir` | CSV 파일이 있는 디렉터리 | - |
| `--output-dir`, `-o` | 출력 디렉터리 | BTO_output |
| `--cam-db` | 카메라 정보 SQLite DB 경로 | 20250602.sqlite |
| `--cam-csv` | 카메라 정보 CSV 경로 | input_table.csv |
| `--cam-table` | SQLite 테이블 이름 | `250602` |
| `--spatialite` | SpatiaLite 확장 모듈 경로 | 자동 탐색 |

## 입력 파일 형식

### CSV 이벤트 파일
필수 컬럼:
- `Num_event`: 이벤트 번호
- `DateTime`: 날짜/시간 (예: `250101123045`)
- `eventcode`: 이벤트 코드 (81-85만 처리)
- `Speed`: 속도 (km/h)
- `GPS_X`: 경도
- `GPS_Y`: 위도
- `GPS_Degree`: 진행 방향 (0-360도)
- `_source_file`: 원본 파일명

### 카메라 정보 파일

#### SQLite DB
- 테이블에 필요한 컬럼:
  - `idx` / `ogc_fid`: 행 인덱스
  - `cam_id`: 카메라 ID
  - `speed`: 제한 속도
  - `heading`: 카메라 방향 (0-360도)
  - `code`: 카메라 코드 (예: `1-130`)
  - `type`: `EP` (단속 카메라)
  - `GEOMETRY`: 공간 데이터 (SpatiaLite Point)

#### CSV 파일
필수 컬럼:
- `cam_id`: 카메라 ID
- `longitude` / `lon` / `GPS_X`: 경도
- `latitude` / `lat` / `GPS_Y`: 위도
- `heading` / `cam_heading`: 카메라 방향
- `speed` / `limit_speed`: 제한 속도
- `type`: `EP`
- `code`: 카메라 코드

## 출력 형식

### Excel 파일 구조
- 월별 시트로 자동 분리 (예: `01월`, `02월`, ...)
- `기타` 시트: 월을 파악할 수 없는 데이터

### 출력 컬럼
| 컬럼명 | 설명 |
|--------|------|
| `Num_event` | 이벤트 번호 |
| `DateTime` | 이벤트 발생 시간 |
| `eventcode` | 이벤트 코드 |
| `GPS_X` | 경도 |
| `GPS_Y` | 위도 |
| `GPS_Degree` | 진행 방향 |
| `camera_id` | 매칭된 카메라 ID |
| `row_idx` | 카메라 DB 행 인덱스 |
| `과속속도` | 해당 위치 제한 속도 |
| `t0` | 이벤트 발생 시점 속도 |
| `t+5s` | 5초 후 속도 |
| `t+10s` | 10초 후 속도 |
| `t0_과속속도_분류` | 과속 분류 (0-3) |
| `_source_file` | 원본 파일명 |

### 과속 분류 기준
- **0**: 과속 없음 (t0 - 제한속도 < 20km/h)
- **1**: 5초 과속 (t+5s만 20km/h 이상 초과)
- **2**: 지속 과속 (t+5s, t+10s 모두 20km/h 이상 초과)
- **3**: 감속 (t0는 과속이나 t+5s, t+10s는 정상)

## 카메라 매칭 알고리즘

### 1. 거리 기반 필터링
- 반경 **1,000m (1km)** 이내 카메라 검색
- Haversine 공식으로 정확한 거리 계산

### 2. 방향 일치 확인
- 이벤트 진행 방향과 카메라 방향 차이: **±20도** 이내
- 이벤트 위치에서 카메라로의 방위각도 확인

### 3. 우선순위
1. 두 번째 이벤트 좌표로 매칭 시도 (가장 정확)
2. 첫 번째 이벤트 좌표로 매칭
3. 세 번째 이벤트 좌표로 매칭
4. 방향 무시하고 거리만으로 매칭

### 4. 카메라 필터링
- `type = "EP"` (단속 카메라)만 사용
- 허용된 `code`: `1-130`, `1-0`, `1-12`, `1-13`, `1-2`, `1-9`, `1-139`, `7-130`, `7-0`, `7-9`, `7-139`, `48-0`

## 설정 상수

스크립트 상단에서 수정 가능:

```python
ALLOW_EVENTCODES = {81, 82, 83, 84, 85}      # 처리할 이벤트 코드
CAMERA_SEARCH_RADIUS_M = 1000.0              # 카메라 검색 반경 (m)
HEADING_TOLERANCE_DEG = 20.0                 # 방향 허용 오차 (도)
ALLOWED_CAMERA_CODES = {...}                 # 허용 카메라 코드
DEFAULT_CAM_TABLE = "250602"                 # 기본 테이블명
```

## 문제 해결

### CSV 인코딩 오류
스크립트는 자동으로 다음 순서로 인코딩 시도:
1. UTF-8 with BOM
2. CP949 (한국어 Windows)
3. EUC-KR

### SpatiaLite 로드 실패
```
RuntimeError: Failed to load SpatiaLite extension.
```
**해결 방법:**
1. SpatiaLite 모듈 설치 확인
2. `--spatialite` 옵션으로 직접 경로 지정
3. `SPATIALITE_LIBRARY_PATH` 환경 변수 설정

### 카메라 매칭 안 됨
- GPS 좌표 형식 확인 (소수점 6자리 권장)
- 진행 방향(`GPS_Degree`) 값 확인 (0-360)
- 카메라 DB/CSV의 `heading` 값 확인
- 검색 반경 상수 조정 고려

### 메모리 부족
대용량 파일 처리 시:
```python
# 청크 단위로 읽기 (코드 수정 필요)
df = pd.read_csv(csv_path, chunksize=10000)
```

## 성능 최적화

- **배치 처리**: `--input-dir`로 여러 파일 동시 처리
- **인덱싱**: CameraIndex 클래스가 거리 기반 사전 필터링 수행
- **중복 제거**: 동일 `cam_id`는 우선순위에 따라 자동 제거

## 라이선스

이 프로젝트는 내부 사용을 위한 것입니다.

---

**버전**: 1.3  
**최종 수정**: 2025-10-02
