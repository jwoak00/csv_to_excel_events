#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSV to Excel converter with speed camera enrichment.
"""

import os
import sys
import argparse
import base64
import binascii
import math
import numbers
import sqlite3
import struct
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

import pandas as pd

SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
DEFAULT_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "BTO_output")
DEFAULT_DB_PATH = os.path.join(SCRIPT_DIR, "SQLite", "20250602.sqlite")
DEFAULT_CSV_PATH = os.path.join(SCRIPT_DIR, "input_table.csv")

ALLOW_EVENTCODES = {81, 82, 83, 84, 85}
CAMERA_SEARCH_RADIUS_M = 1000.0 # 1 km
HEADING_TOLERANCE_DEG = 20.0   # 20 degrees
ALLOWED_CAMERA_CODES = {
    "1-130", "1-0", "1-12", "1-13", "1-2", "1-9", "1-139",
    "7-130", "7-0", "7-9", "7-139", "48-0"
}
DEFAULT_CAM_TABLE = "250602"
DEFAULT_EXTENSION_CANDIDATES = (
    "mod_spatialite",
    "mod_spatialite.dll",
    "mod_spatialite.so",
    "mod_spatialite.dylib",
    "libspatialite",
    "libspatialite.dll",
)


def _strip_excel_wrapper(text: str) -> str:
    if text.startswith('="') and text.endswith('"'):
        return text[2:-1]
    if text.startswith('="') and text.endswith('""'):
        return text[2:-2]
    if text.startswith('=') and len(text) > 1 and text[1] in {'"', "'"}:
        quote = text[1]
        if text.endswith(quote):
            return text[2:-1]
    return text


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, bool):
        return None
    if isinstance(value, numbers.Real):
        result = float(value)
    else:
        try:
            text = str(value).strip()
        except Exception:
            return None
        if not text or text.lower() == "nan":
            return None
        text = _strip_excel_wrapper(text)
        if not text:
            return None
        try:
            result = float(text)
        except ValueError:
            return None
    if math.isnan(result) or math.isinf(result):
        return None
    if abs(result) > 1000.0 and abs(result) < 1e9:
        result /= 1_000_000.0
    return result


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    rad = math.radians
    dlon = rad(lon2 - lon1)
    dlat = rad(lat2 - lat1)
    a = math.sin(dlat / 2.0) ** 2 + math.cos(rad(lat1)) * math.cos(rad(lat2)) * math.sin(dlon / 2.0) ** 2
    return 2.0 * 6371000.0 * math.asin(math.sqrt(a))


def _bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    theta = math.atan2(y, x)
    bearing = (math.degrees(theta) + 360.0) % 360.0
    return bearing


def _angle_diff_deg(a: float, b: float) -> float:
    diff = abs((a - b) % 360.0)
    if diff > 180.0:
        diff = 360.0 - diff
    return diff


def _degree_buffer(lat: float) -> Tuple[float, float]:
    lat_buffer = CAMERA_SEARCH_RADIUS_M / 111320.0
    cos_lat = math.cos(math.radians(lat))
    if abs(cos_lat) < 1e-12:
        lon_buffer = 180.0
    else:
        lon_buffer = CAMERA_SEARCH_RADIUS_M / (111320.0 * abs(cos_lat))
    return lat_buffer, lon_buffer


def decode_spatialite_point(blob_value: Any) -> Optional[Tuple[float, float]]:
    if blob_value is None:
        return None
    try:
        blob_text = str(blob_value).strip()
    except Exception:
        return None
    if not blob_text or blob_text.lower() == "nan":
        return None
    try:
        raw = base64.b64decode(blob_text)
    except (binascii.Error, ValueError):
        return None
    if len(raw) < 38:
        return None
    try:
        minx, maxx, miny, maxy = struct.unpack("<dddd", raw[6:38])
    except struct.error:
        return None
    if any(math.isnan(v) or math.isinf(v) for v in (minx, maxx, miny, maxy)):
        return None
    lon = (minx + maxx) / 2.0
    lat = (miny + maxy) / 2.0
    return lon, lat


class CameraIndex:
    def __init__(self, records: List[Dict[str, Any]]):
        self._records = records

    def lookup(self, lon: float, lat: float, heading: Optional[float], *, require_heading: bool = True) -> Optional[Dict[str, Any]]:
        if not self._records:
            return None
        lat_buf, lon_buf = _degree_buffer(lat)
        best: Optional[Dict[str, Any]] = None
        best_dist = CAMERA_SEARCH_RADIUS_M + 1.0
        for record in self._records:
            cam_lat = record["latitude"]
            cam_lon = record["longitude"]
            if abs(cam_lat - lat) > lat_buf or abs(cam_lon - lon) > lon_buf:
                continue
            distance = haversine_m(lon, lat, cam_lon, cam_lat)
            if distance > CAMERA_SEARCH_RADIUS_M:
                continue

            if require_heading:
                if heading is None:
                    continue
                cam_heading = record.get("heading")
                if cam_heading is None or _angle_diff_deg(cam_heading, heading) > HEADING_TOLERANCE_DEG:
                    continue
                azimuth = _bearing_deg(lon, lat, cam_lon, cam_lat)
                if _angle_diff_deg(azimuth, heading) > HEADING_TOLERANCE_DEG:
                    continue

            if distance < best_dist:
                best = record
                best_dist = distance
        if best is None:
            return None
        return {
            "row_idx": best.get("row_idx"),
            "cam_id": best.get("cam_id"),
            "speed": best.get("speed"),
            "distance": best_dist,
            "heading": best.get("heading"),
            "code": best.get("code"),
        }


def _collect_extension_candidates(explicit_path: Optional[str]) -> List[str]:
    candidates: List[str] = []
    if explicit_path:
        candidates.append(explicit_path)

    env_value = os.environ.get("SPATIALITE_LIBRARY_PATH")
    if env_value:
        for node in env_value.split(os.pathsep):
            node = node.strip()
            if not node:
                continue
            candidates.append(node)
            if os.path.isdir(node):
                for default_name in DEFAULT_EXTENSION_CANDIDATES:
                    candidates.append(os.path.join(node, default_name))

    candidates.extend(DEFAULT_EXTENSION_CANDIDATES)

    seen = set()
    ordered: List[str] = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def connect_spatialite(db_path: str, explicit_extension: Optional[str]) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, check_same_thread=False)
    connection.enable_load_extension(True)
    load_errors = []
    for candidate in _collect_extension_candidates(explicit_extension):
        try:
            connection.load_extension(candidate)
            break
        except sqlite3.OperationalError as exc:
            load_errors.append(f"{candidate}: {exc}")
    else:
        connection.close()
        message = [
            "Failed to load SpatiaLite extension.",
            "Provide --spatialite or set SPATIALITE_LIBRARY_PATH.",
            "Tried:",
        ]
        message.extend(f"  {line}" for line in load_errors)
        raise RuntimeError("\n".join(message))
    connection.enable_load_extension(False)
    return connection


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f'PRAGMA table_info("{table}")')
    return any(row[1] == column for row in cur.fetchall())


def load_camera_records_from_sqlite(db_path: str, table: str, spatialite_extension: Optional[str]) -> List[Dict[str, Any]]:
    conn = connect_spatialite(db_path, spatialite_extension)
    try:
        has_type = _table_has_column(conn, table, "type")
        placeholders = ", ".join(f'"{code}"' for code in sorted(ALLOWED_CAMERA_CODES))
        select_cols = 'idx, cam_id, speed, heading, code, ST_X(GEOMETRY), ST_Y(GEOMETRY)'
        if has_type:
            select_cols += ', type'
        query = (
            f'SELECT {select_cols} '
            f'FROM "{table}" '
            'WHERE cam_id IS NOT NULL AND TRIM(cam_id) <> "" '
        )
        if has_type:
            query += 'AND type = "EP" '
        query += f'AND code IN ({placeholders})'

        records: List[Dict[str, Any]] = []
        for row in conn.execute(query):
            if has_type:
                idx_value, cam_id, speed, heading, code, lon, lat, cam_type = row
                cam_type_text = str(cam_type).upper() if cam_type else ""
            else:
                idx_value, cam_id, speed, heading, code, lon, lat = row
                cam_type_text = "EP"

            if cam_type_text != "EP":
                continue

            lon_val = _safe_float(lon)
            lat_val = _safe_float(lat)
            if lon_val is None or lat_val is None:
                continue

            speed_val = _safe_float(speed)
            heading_val = _safe_float(heading)
            if heading_val is None:
                continue

            code_text = str(code).strip().upper()
            if code_text not in ALLOWED_CAMERA_CODES:
                continue

            row_idx_val: Optional[int] = None
            if isinstance(idx_value, numbers.Integral):
                row_idx_val = int(idx_value)
            else:
                try:
                    row_idx_val = int(str(idx_value).strip())
                except (TypeError, ValueError):
                    row_idx_val = None

            record: Dict[str, Any] = {
                "row_idx": row_idx_val,
                "cam_id": str(cam_id),
                "speed": speed_val,
                "longitude": lon_val,
                "latitude": lat_val,
                "type": cam_type_text,
                "heading": heading_val,
                "code": code_text,
            }
            records.append(record)

        if not records:
            raise RuntimeError(f"No camera rows available in table '{table}'.")
        return records
    finally:
        conn.close()


def read_camera_csv(csv_path: str) -> pd.DataFrame:
    last_err = None
    for enc in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return pd.read_csv(csv_path, encoding=enc)
        except Exception as exc:
            last_err = exc
    raise RuntimeError(f"Failed to read camera CSV: {last_err}")


def load_camera_records_from_csv(csv_path: str) -> List[Dict[str, Any]]:
    df = read_camera_csv(csv_path)
    lon_candidates = [c for c in df.columns if c.lower() in {"longitude", "lon", "gps_x", "x"}]
    lat_candidates = [c for c in df.columns if c.lower() in {"latitude", "lat", "gps_y", "y"}]
    cam_id_columns = [c for c in df.columns if c.lower() == "cam_id"]
    speed_columns = [c for c in df.columns if c.lower() in {"speed", "limit_speed"}]
    type_columns = [c for c in df.columns if c.lower() == "type"]
    heading_columns: List[str] = []
    for name in ("cam_heading", "heading"):
        heading_columns.extend([c for c in df.columns if c.lower() == name])
    code_columns = [c for c in df.columns if c.lower() == "code"]
    row_idx_columns = [c for c in df.columns if c.lower() in {"row_idx", "idx", "ogc_fid"}]

    records_map: Dict[str, Dict[str, Any]] = {}
    for _, row in df.iterrows():
        cam_id_value = None
        for col in cam_id_columns:
            value = row[col]
            if pd.notna(value):
                cam_id_value = value
                break
        if cam_id_value is None:
            continue

        cam_id = str(cam_id_value).strip()
        if not cam_id or cam_id.lower() == "nan":
            continue

        row_idx_val: Optional[int] = None
        for col in row_idx_columns:
            value = row.get(col, None)
            if pd.notna(value):
                if isinstance(value, numbers.Integral):
                    row_idx_val = int(value)
                    break
                text_value = str(value).strip()
                if not text_value:
                    continue
                try:
                    row_idx_val = int(float(text_value))
                    break
                except ValueError:
                    try:
                        row_idx_val = int(text_value)
                        break
                    except ValueError:
                        row_idx_val = None

        lon_lat: Optional[Tuple[float, float]] = None
        if "GEOMETRY" in df.columns and pd.notna(row["GEOMETRY"]):
            lon_lat = decode_spatialite_point(row["GEOMETRY"])

        if lon_lat is None and lon_candidates and lat_candidates:
            lon_val = _safe_float(row[lon_candidates[0]])
            lat_val = _safe_float(row[lat_candidates[0]])
            if lon_val is not None and lat_val is not None:
                lon_lat = (lon_val, lat_val)

        if lon_lat is None:
            continue

        speed_val = None
        for col in speed_columns:
            val = row[col]
            if pd.notna(val):
                candidate_speed = _safe_float(val)
                if candidate_speed is not None:
                    speed_val = candidate_speed
                    break

        cam_type = ""
        for col in type_columns:
            val = row[col]
            if pd.notna(val):
                cam_type = str(val).upper()
                break
        if cam_type != "EP":
            continue

        heading_val = None
        for col in heading_columns:
            val = row.get(col)
            if pd.notna(val):
                heading_val = _safe_float(val)
                if heading_val is not None:
                    break
        if heading_val is None:
            continue

        code_text = ""
        for col in code_columns:
            val = row[col]
            if pd.notna(val):
                code_text = str(val).strip().upper()
                break
        if code_text not in ALLOWED_CAMERA_CODES:
            continue

        record = {
            "row_idx": row_idx_val,
            "cam_id": cam_id,
            "speed": speed_val,
            "longitude": float(lon_lat[0]),
            "latitude": float(lon_lat[1]),
            "type": cam_type,
            "heading": heading_val,
            "code": code_text,
        }
        priority = 1
        existing = records_map.get(cam_id)
        if existing is None or priority > existing["priority"]:
            records_map[cam_id] = {"priority": priority, "record": record}

    if not records_map:
        raise RuntimeError("No camera records found in CSV.")
    return [item["record"] for item in records_map.values()]



def _deduplicate_camera_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    dedup: Dict[str, Dict[str, Any]] = {}
    for record in records:
        cam_id = record["cam_id"]
        priority = 1 if str(record.get("type", "")).upper() == "EP" else 0
        existing = dedup.get(cam_id)
        if existing is None or priority > existing["priority"]:
            dedup[cam_id] = {"priority": priority, "record": record}
    return [entry["record"] for entry in dedup.values()]


def resolve_camera_source(
    cam_db: Optional[str],
    cam_csv: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    if cam_db and cam_csv:
        raise ValueError("Use only one of --cam-db or --cam-csv.")
    message: Optional[str] = None
    if not cam_db and not cam_csv:
        if os.path.exists(DEFAULT_DB_PATH):
            cam_db = DEFAULT_DB_PATH
            message = f"Using default camera DB: {DEFAULT_DB_PATH}"
        elif os.path.exists(DEFAULT_CSV_PATH):
            cam_csv = DEFAULT_CSV_PATH
            message = f"Using default camera CSV: {DEFAULT_CSV_PATH}"
    return cam_db, cam_csv, message


def build_camera_index(
    cam_db: Optional[str],
    cam_csv: Optional[str],
    cam_table: str,
    spatialite_extension: Optional[str],
) -> Tuple[Optional[CameraIndex], Optional[str]]:
    cam_db, cam_csv, auto_message = resolve_camera_source(cam_db, cam_csv)

    if cam_db:
        if not os.path.exists(cam_db):
            raise FileNotFoundError(f"Camera DB not found: {cam_db}")
        records = load_camera_records_from_sqlite(cam_db, cam_table, spatialite_extension)
    elif cam_csv:
        if not os.path.exists(cam_csv):
            raise FileNotFoundError(f"Camera CSV not found: {cam_csv}")
        records = load_camera_records_from_csv(cam_csv)
    else:
        return None, auto_message

    normalized = _deduplicate_camera_records(records)
    if not normalized:
        raise RuntimeError("Camera data could not be prepared.")
    return CameraIndex(normalized), auto_message


def classify_speed(
    limit_speed: Optional[float],
    t0: Optional[float],
    p5: Optional[float],
    p10: Optional[float],
) -> Optional[int]:
    limit = _safe_float(limit_speed)
    t0_val = _safe_float(t0)
    if limit is None or t0_val is None:
        return None
    t0_over = t0_val - limit
    if t0_over < 20:
        return 0

    p5_val = _safe_float(p5)
    p10_val = _safe_float(p10)
    if p5_val is None or p10_val is None:
        return None

    over5 = (p5_val - limit) >= 20
    over10 = (p10_val - limit) >= 20

    if over5 and over10:
        return 2
    if over5 and not over10:
        return 1
    if not over5 and not over10:
        return 3
    return None


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
        raise RuntimeError(f"CSV을 읽지 못했습니다. 마지막 오류: {last_err}")

    if "Num_event" not in df.columns and "Num_Event" in df.columns:
        df = df.rename(columns={"Num_Event": "Num_event"})

    required = ["Num_event", "DateTime", "eventcode", "Speed", "GPS_X", "GPS_Y", "GPS_Degree", "_source_file"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"필수 컬럼이 없습니다: {missing}")

    df["eventcode_int"] = pd.to_numeric(df["eventcode"], errors="coerce").astype("Int64")

    df["_digits"] = df["DateTime"].astype(str).str.replace(r"\D", "", regex=True)

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


def aggregate(df: pd.DataFrame, camera_index: Optional[CameraIndex]) -> pd.DataFrame:
    df_f = df[df["eventcode_int"].isin(list(ALLOW_EVENTCODES))].copy()

    recs = []
    for (src, num_event), g in df_f.groupby(["_source_file", "Num_event"], sort=False):
        g_sorted = g.sort_values("_digits", kind="stable")
        g3 = g_sorted.head(3)

        speeds = g3["Speed_num"].tolist()
        t0 = speeds[0] if len(speeds) >= 1 else None
        p5 = speeds[1] if len(speeds) >= 2 else None
        p10 = speeds[2] if len(speeds) >= 3 else None

        row0 = g3.iloc[0] if len(g3) >= 1 else None
        DateTime = row0["DateTime"] if row0 is not None else ""
        eventcode = row0["eventcode"] if row0 is not None else ""
        GPS_X = row0["GPS_X"] if row0 is not None else ""
        GPS_Y = row0["GPS_Y"] if row0 is not None else ""
        GPS_Degree = row0["GPS_Degree"] if row0 is not None else ""
        month_val = month_from_digits(row0["_digits"]) if row0 is not None else None

        camera_id_val = ""
        row_idx_val: Optional[int] = None
        limit_speed_val: Optional[float] = None
        if camera_index is not None and not g3.empty:
            lookup_order: List[pd.Series] = []
            if len(g3) >= 2:
                lookup_order.append(g3.iloc[1])
            if len(g3) >= 1:
                lookup_order.append(g3.iloc[0])
            if len(g3) >= 3:
                lookup_order.append(g3.iloc[2])

            for lookup_row in lookup_order:
                lon_val = _safe_float(lookup_row.get("GPS_X"))
                lat_val = _safe_float(lookup_row.get("GPS_Y"))
                heading_val = _safe_float(lookup_row.get("GPS_Degree"))
                if lon_val is None or lat_val is None:
                    continue
                match = None
                if heading_val is not None:
                    match = camera_index.lookup(lon_val, lat_val, heading_val, require_heading=True)
                if match is None:
                    match = camera_index.lookup(lon_val, lat_val, heading_val, require_heading=False)
                if match:
                    camera_id_val = match.get("cam_id", "") or ""
                    limit_speed_val = match.get("speed")
                    raw_idx = match.get("row_idx")
                    if isinstance(raw_idx, numbers.Integral):
                        row_idx_val = int(raw_idx)
                    else:
                        try:
                            row_idx_val = int(str(raw_idx).strip())
                        except (TypeError, ValueError):
                            row_idx_val = None
                    break

        classification = classify_speed(limit_speed_val, t0, p5, p10)

        recs.append({
            "_source_file": src,
            "Num_event": num_event,
            "DateTime": DateTime,
            "eventcode": eventcode,
            "GPS_X": GPS_X,
            "GPS_Y": GPS_Y,
            "GPS_Degree": GPS_Degree,
            "camera_id": camera_id_val,
            "row_idx": row_idx_val if row_idx_val is not None else pd.NA,
            "과속속도": limit_speed_val if limit_speed_val is not None else pd.NA,
            "t0": t0,
            "t+5s": p5,
            "t+10s": p10,
            "t0_과속속도_분류": classification,
            "_month": month_val,
        })

    out = pd.DataFrame.from_records(recs)

    cols = [
        "Num_event", "DateTime", "eventcode",
        "GPS_X", "GPS_Y", "GPS_Degree", "camera_id", "row_idx",
        "과속속도", "t0", "t+5s", "t+10s", "t0_과속속도_분류", "_month", "_source_file",
    ]
    for c in cols:
        if c not in out.columns:
            out[c] = pd.NA
    out = out[cols]

    return out


def write_by_month(out_df: pd.DataFrame, xlsx_path: str):
    out_df_sorted = out_df.sort_values(by=["_source_file", "Num_event"]).copy()
    months_present = sorted(
        {int(m) for m in out_df_sorted["_month"] if isinstance(m, numbers.Integral)}
    )
    others_mask = ~out_df_sorted["_month"].apply(lambda x: isinstance(x, numbers.Integral))
    others_df = out_df_sorted[others_mask]

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        for mm in months_present:
            sheet = f"{mm:02d}월"
            tmp = out_df_sorted[out_df_sorted["_month"] == mm].drop(columns=["_month"])
            tmp.to_excel(writer, sheet_name=sheet, index=False)
        if len(others_df) > 0:
            others_df.drop(columns=["_month"]).to_excel(writer, sheet_name="기타", index=False)


def build_output_path(input_csv: str, output_dir: str) -> str:
    base = os.path.basename(input_csv)
    stem = os.path.splitext(base)[0]
    return os.path.join(output_dir, f"{stem}_output.xlsx")


def convert(input_csv: str, output_dir: str, camera_index: Optional[CameraIndex]) -> str:
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    df = read_csv_smart(input_csv)
    out_df = aggregate(df, camera_index)
    out_path = build_output_path(input_csv, output_dir)
    write_by_month(out_df, out_path)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="(_source_file, Num_event) 기반 3개(t0,+5s,+10s) 집계")
    ap.add_argument("--input", "-i", help="입력 CSV 파일 경로")
    ap.add_argument("--input-dir", help="CSV 파일이 포함된 디렉터리 경로")
    ap.add_argument("--output-dir", "-o", default=DEFAULT_OUTPUT_DIR, help="출력 폴더")
    ap.add_argument("--cam-db", help="카메라 정보 SQLite 파일 경로")
    ap.add_argument("--cam-csv", help="카메라 정보 CSV 경로 (cam_id, speed, 좌표 포함)")
    ap.add_argument("--cam-table", default=DEFAULT_CAM_TABLE, help="SQLite에서 사용할 테이블명")
    ap.add_argument("--spatialite", help="SpatiaLite 확장 모듈 경로 (DLL/SO)")
    args = ap.parse_args()
    try:
        if args.input and args.input_dir:
            raise ValueError('하나의 입력 방식만 선택하세요 (--input 또는 --input-dir).')
        if not args.input and not args.input_dir:
            raise ValueError('CSV 파일 또는 디렉터리 중 하나를 지정해야 합니다.')

        input_paths: List[Path]
        output_dir_root: Path
        if args.input_dir:
            dir_path = Path(args.input_dir)
            if not dir_path.is_dir():
                raise FileNotFoundError(f'입력 디렉터리가 존재하지 않습니다: {dir_path}')
            input_paths = sorted(p for p in dir_path.iterdir() if p.suffix.lower() == '.csv')
            if not input_paths:
                raise FileNotFoundError(f'CSV 파일을 찾을 수 없습니다: {dir_path}')
            output_dir_root = Path(DEFAULT_OUTPUT_DIR) / (dir_path.name + '_output')
        else:
            file_path = Path(args.input)
            if not file_path.is_file():
                raise FileNotFoundError(f'입력 CSV 파일을 찾을 수 없습니다: {file_path}')
            input_paths = [file_path]
            output_dir_root = Path(args.output_dir)

        camera_index, auto_message = build_camera_index(args.cam_db, args.cam_csv, args.cam_table, args.spatialite)
        if auto_message:
            print(auto_message)

        if args.input_dir:
            output_dir_root.mkdir(parents=True, exist_ok=True)
            for csv_path in input_paths:
                out = convert(str(csv_path), str(output_dir_root), camera_index)
                print(f'[완료] {csv_path.name} -> {out}')
        else:
            out = convert(str(input_paths[0]), str(output_dir_root), camera_index)
            print(f'[완료] 저장: {out}')
    except Exception as e:
        print(f'[실패] {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()



