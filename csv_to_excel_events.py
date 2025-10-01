
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSV → Excel 변환 (개정 요구사항)

변경점 요약
- 그룹 기준: (_source_file, Num_event) 별로 t0 / t+5s / t+10s 3개를 묶음
  * 그룹 내 행이 3개를 초과하면 "가장 이른 3개"를 사용 (부족하면 공란)
- 필터: eventcode ∈ {81,82,83,84,85}만 사용 (그 외는 버림)
- 대표값(DateTime, eventcode, GPS*)은 t0 행 기준
- camera_id, 과속속도, t0_과속속도_분류: 공란(분류 로직 STUB 유지)
- 시트: DateTime의 월(MM) 기준으로 '[월]월' 시트에 저장
- 출력 경로 없으면 오류

추가:
- 출력에 '_source_file' 컬럼을 포함 (같은 Num_event라도 소스별 분리 확인 목적)
"""
import os
import sys
import argparse
import pandas as pd

DEFAULT_OUTPUT_DIR = r"C:\Users\82105\OneDrive\바탕 화면\스카이오토넷\BTO\BTO_output"

ALLOW_EVENTCODES = {81,82,83,84,85}

def read_csv_smart(csv_path: str) -> pd.DataFrame:
    last_err = None
    for enc in ["cp949", "utf-8-sig", "euc-kr"]:
        try:
            df = pd.read_csv(csv_path, encoding=enc, dtype={"DateTime": str})
            break
        except Exception as e:
            last_err = e
            df = None
    if df is None:
        raise RuntimeError(f"CSV를 읽지 못했습니다. 마지막 오류: {last_err}")

    # 컬럼 표준화
    if "Num_event" not in df.columns and "Num_Event" in df.columns:
        df = df.rename(columns={"Num_Event": "Num_event"})

    # 필수 컬럼 확인
    required = ["Num_event","DateTime","eventcode","Speed","GPS_X","GPS_Y","GPS_Degree","_source_file"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"필수 컬럼이 없습니다: {missing}")

    # eventcode 정수화 (숫자 아닌 것은 NaN → 필터에서 제거)
    df["eventcode_int"] = pd.to_numeric(df["eventcode"], errors="coerce").astype("Int64")

    # DateTime 보조 컬럼 (숫자만)
    df["_digits"] = df["DateTime"].astype(str).str.replace(r"\D","",regex=True)

    # Speed 수치화
    df["Speed_num"] = pd.to_numeric(df["Speed"], errors="coerce")

    return df

def month_from_digits(s: str):
    s = str(s)
    if len(s) >= 4:
        try:
            m = int(s[2:4])
            if 1 <= m <= 12:
                return m
        except Exception:
            return None
    return None

def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    # 1) eventcode 필터
    df_f = df[df["eventcode_int"].isin(list(ALLOW_EVENTCODES))].copy()

    # 2) (_source_file, Num_event) 그룹
    recs = []
    for (src, num_event), g in df_f.groupby(["_source_file","Num_event"], sort=False):
        # 시간 오름차순
        g_sorted = g.sort_values("_digits", kind="stable")

        # 그룹에서 가장 이른 3개만 선택 (부족하면 있는 만큼)
        g3 = g_sorted.head(3)

        # t0/p5/p10 추출
        speeds = g3["Speed_num"].tolist()
        t0 = speeds[0] if len(speeds) >= 1 else None
        p5 = speeds[1] if len(speeds) >= 2 else None
        p10 = speeds[2] if len(speeds) >= 3 else None

        # 대표값 (t0 행)
        row0 = g3.iloc[0] if len(g3) >= 1 else None
        DateTime = row0["DateTime"] if row0 is not None else ""
        eventcode = row0["eventcode"] if row0 is not None else ""
        GPS_X = row0["GPS_X"] if row0 is not None else ""
        GPS_Y = row0["GPS_Y"] if row0 is not None else ""
        GPS_Degree = row0["GPS_Degree"] if row0 is not None else ""
        month_val = month_from_digits(row0["_digits"]) if row0 is not None else None

        recs.append({
            "_source_file": src,
            "Num_event": num_event,
            "DateTime": DateTime,
            "eventcode": eventcode,
            "GPS_X": GPS_X,
            "GPS_Y": GPS_Y,
            "GPS_Degree": GPS_Degree,
            "camera_id": "",
            "과속속도": "",
            "t0": t0 if t0 is not None else "",
            "t+5s": p5 if p5 is not None else "",
            "t+10s": p10 if p10 is not None else "",
            "t0_과속속도_분류": "",
            "_month": month_val,
        })

    out = pd.DataFrame.from_records(recs)

    # 열 순서
    cols = [
        "Num_event","DateTime","eventcode",
        "GPS_X","GPS_Y","GPS_Degree","camera_id",
        "과속속도","t0","t+5s","t+10s","t0_과속속도_분류","_month", "_source_file"
    ]
    for c in cols:
        if c not in out.columns:
            out[c] = ""
    out = out[cols]

    return out

def write_by_month(out_df: pd.DataFrame, xlsx_path: str):
    out_df_sorted = out_df.sort_values(by=["_source_file","Num_event"]).copy()
    months_present = sorted(set([m for m in out_df_sorted["_month"].tolist() if isinstance(m, int)]))
    others_df = out_df_sorted[out_df_sorted["_month"].isna() | ~out_df_sorted["_month"].apply(lambda x: isinstance(x, int))]

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        for mm in months_present:
            sheet = f"{mm}월"
            tmp = out_df_sorted[out_df_sorted["_month"]==mm].drop(columns=["_month"])
            tmp.to_excel(writer, sheet_name=sheet, index=False)
        if len(others_df) > 0:
            others_df.drop(columns=["_month"]).to_excel(writer, sheet_name="기타", index=False)

def build_output_path(input_csv: str, output_dir: str) -> str:
    base = os.path.basename(input_csv)
    stem = os.path.splitext(base)[0]
    return os.path.join(output_dir, f"{stem}_output.xlsx")

def convert(input_csv: str, output_dir: str) -> str:
    if not os.path.isdir(output_dir):
        raise FileNotFoundError(f"[오류] 출력 경로가 존재하지 않습니다: {output_dir}")
    df = read_csv_smart(input_csv)
    out_df = aggregate(df)
    out_path = build_output_path(input_csv, output_dir)
    write_by_month(out_df, out_path)
    return out_path

def main():
    import argparse
    ap = argparse.ArgumentParser(description="(_source_file, Num_event) 기반 3행(t0,+5s,+10s) 집계")
    ap.add_argument("--input","-i", required=True, help="입력 CSV 경로")
    ap.add_argument("--output-dir","-o", default=DEFAULT_OUTPUT_DIR, help="출력 폴더(존재해야 함)")
    args = ap.parse_args()
    try:
        out = convert(args.input, args.output_dir)
        print(f"[완료] 저장: {out}")
    except Exception as e:
        print(f"[실패] {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
