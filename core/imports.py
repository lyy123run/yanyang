"""GPX / TCX 轨迹文件解析：提取距离、时长、平均心率、步频，写入 Record + TrackPoint"""
import math
import xml.etree.ElementTree as ET
from datetime import datetime

GPX_NS = {"g": "http://www.topografix.com/GPX/1/1"}
TCX_NS = {"t": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2",
          "t2": "http://www.garmin.com/xmlschemas/ActivityExtension/v2"}


def _haversine(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _parse_time(txt):
    if not txt:
        return None
    txt = txt.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(txt)
    except ValueError:
        return None


def parse_gpx(content):
    root = ET.fromstring(content)
    pts = []
    for trkpt in root.iter("{http://www.topografix.com/GPX/1/1}trkpt"):
        lat = float(trkpt.get("lat"))
        lon = float(trkpt.get("lon"))
        ele = trkpt.find("g:ele", GPX_NS)
        tm = trkpt.find("g:time", GPX_NS)
        hr = None
        for node in trkpt.iter():
            if node.tag.endswith("}hr") and node.text:
                try:
                    hr = int(float(node.text))
                except ValueError:
                    hr = None
        pts.append({
            "lat": lat, "lon": lon,
            "ele": float(ele.text) if ele is not None and ele.text else 0.0,
            "ts": tm.text if tm is not None else "",
            "hr": hr,
        })
    if not pts:
        raise ValueError("GPX 中未找到轨迹点（trkpt）")

    dist = sum(_haversine(pts[i - 1]["lat"], pts[i - 1]["lon"], pts[i]["lat"], pts[i]["lon"])
               for i in range(1, len(pts)))
    times = [_parse_time(p["ts"]) for p in pts]
    valid = [t for t in times if t]
    duration = (valid[-1] - valid[0]).total_seconds() if len(valid) >= 2 else 0
    hrs = [p["hr"] for p in pts if p["hr"]]
    return {
        "distance_km": round(dist / 1000.0, 2),
        "duration_min": round(duration / 60.0, 1),
        "avg_hr": int(round(sum(hrs) / len(hrs))) if hrs else None,
        "cadence": None,
        "points": pts,
        "source": "gpx",
        "started_at": valid[0] if valid else None,
    }


def parse_tcx(content):
    root = ET.fromstring(content)
    laps = list(root.iter("{http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2}Lap"))
    if not laps:
        raise ValueError("TCX 中未找到 Lap 数据")

    dist_m = 0.0
    duration_s = 0.0
    hr_vals, cadence_vals = [], []
    for lap in laps:
        d = lap.find("t:DistanceMeters", TCX_NS)
        t = lap.find("t:TotalTimeSeconds", TCX_NS)
        hr = lap.find("t:AverageHeartRateBpm/t:Value", TCX_NS)
        cad = lap.find("t:Cadence", TCX_NS)
        if d is not None and d.text:
            dist_m += float(d.text)
        if t is not None and t.text:
            duration_s += float(t.text)
        if hr is not None and hr.text:
            hr_vals.append(float(hr.text))
        if cad is not None and cad.text:
            cadence_vals.append(float(cad.text))

    pts = []
    for tp in root.iter("{http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2}Trackpoint"):
        pos = tp.find("t:Position", TCX_NS)
        tm = tp.find("t:Time", TCX_NS)
        hr = tp.find("t:HeartRateBpm/t:Value", TCX_NS)
        ele = tp.find("t:AltitudeMeters", TCX_NS)
        if pos is None:
            continue
        lat = pos.find("t:LatitudeDegrees", TCX_NS)
        lon = pos.find("t:LongitudeDegrees", TCX_NS)
        pts.append({
            "lat": float(lat.text) if lat is not None and lat.text else 0,
            "lon": float(lon.text) if lon is not None and lon.text else 0,
            "ele": float(ele.text) if ele is not None and ele.text else 0,
            "ts": tm.text if tm is not None else "",
            "hr": int(float(hr.text)) if hr is not None and hr.text else None,
        })

    # 部分 TCX 缺失 Lap 汇总时，用轨迹点回算
    if dist_m <= 0 and len(pts) >= 2:
        dist_m = sum(_haversine(pts[i - 1]["lat"], pts[i - 1]["lon"], pts[i]["lat"], pts[i]["lon"])
                     for i in range(1, len(pts)))
    if duration_s <= 0:
        times = [_parse_time(p["ts"]) for p in pts]
        valid = [t for t in times if t]
        if len(valid) >= 2:
            duration_s = (valid[-1] - valid[0]).total_seconds()
    hrs = [p["hr"] for p in pts if p["hr"]]

    return {
        "distance_km": round(dist_m / 1000.0, 2),
        "duration_min": round(duration_s / 60.0, 1),
        "avg_hr": int(round(sum(hr_vals) / len(hr_vals))) if hr_vals else (
            int(round(sum(hrs) / len(hrs)) if hrs else None)),
        "cadence": int(round(sum(cadence_vals) / len(cadence_vals))) if cadence_vals else None,
        "points": pts,
        "source": "tcx",
        "started_at": None,
    }


def parse_file(filename, content):
    name = (filename or "").lower()
    if name.endswith(".gpx"):
        return parse_gpx(content)
    if name.endswith(".tcx"):
        return parse_tcx(content)
    # 未知扩展名：按内容判断
    head = content[:400].decode("utf-8", errors="ignore")
    if "TrainingCenterDatabase" in content[:2000].decode("utf-8", errors="ignore"):
        return parse_tcx(content)
    if "gpx" in head.lower():
        return parse_gpx(content)
    raise ValueError("仅支持 .gpx 或 .tcx 文件")
