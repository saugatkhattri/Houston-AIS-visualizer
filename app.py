from flask import Flask, render_template, jsonify
import pandas as pd
import os
import math

app = Flask(__name__)

MAPBOX_TOKEN = "YOUR_MAP_BOX_TOKEN"
CSV_FILE = "ais_houston.csv"

# Focus: Houston Ship Channel + Galveston Bay
BOUNDS = {
    "min_lat": 29.15,
    "max_lat": 29.90,
    "min_lon": -95.45,
    "max_lon": -94.65
}

MAX_SHIPS = 60
MIN_POINTS_PER_SHIP = 8
MAX_POINTS_PER_SHIP = 150
MIN_AVG_SOG = 0.5


VESSEL_TYPE_NAMES = {
    "0": "Unknown", "20": "Wing in Ground", "21": "Wing in Ground Hazardous A",
    "22": "Wing in Ground Hazardous B", "23": "Wing in Ground Hazardous C",
    "24": "Wing in Ground Hazardous D", "29": "Wing in Ground (other)",
    "30": "Fishing", "31": "Towing", "32": "Towing Large",
    "33": "Dredging", "34": "Diving Ops", "35": "Military Ops",
    "36": "Sailing", "37": "Pleasure Craft", "40": "High Speed Craft",
    "50": "Pilot Vessel", "51": "Search and Rescue", "52": "Tug",
    "53": "Port Tender", "54": "Anti-Pollution", "55": "Law Enforcement",
    "56": "Spare - Local 1", "57": "Spare - Local 2", "58": "Medical Transport",
    "59": "Noncombatant", "60": "Passenger", "61": "Passenger Hazardous A",
    "62": "Passenger Hazardous B", "63": "Passenger Hazardous C",
    "64": "Passenger Hazardous D", "69": "Passenger (other)",
    "70": "Cargo", "71": "Cargo Hazardous A", "72": "Cargo Hazardous B",
    "73": "Cargo Hazardous C", "74": "Cargo Hazardous D", "79": "Cargo (other)",
    "80": "Tanker", "81": "Tanker Hazardous A", "82": "Tanker Hazardous B",
    "83": "Tanker Hazardous C", "84": "Tanker Hazardous D", "89": "Tanker (other)",
    "90": "Other", "91": "Other Hazardous A", "92": "Other Hazardous B",
    "93": "Other Hazardous C", "94": "Other Hazardous D", "99": "Other (no info)"
}


def get_vessel_type_name(type_val):
    if pd.isna(type_val):
        return "Unknown"
    key = str(int(float(type_val))) if type_val != "" else "0"
    return VESSEL_TYPE_NAMES.get(key, f"Type {key}")


def bearing(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(math.radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = (math.cos(lat1) * math.sin(lat2)
         - math.sin(lat1) * math.cos(lat2) * math.cos(dlon))
    return (math.degrees(math.atan2(x, y)) + 360) % 360


_cached_data = None


def load_and_process():
    global _cached_data
    if _cached_data is not None:
        return _cached_data

    if not os.path.exists(CSV_FILE):
        return None

    df = pd.read_csv(CSV_FILE, low_memory=False)

    df = df.rename(columns={
        "LAT": "lat", "LON": "lon",
        "BaseDateTime": "time", "MMSI": "mmsi",
        "SOG": "speed", "COG": "course",
        "VesselName": "name", "VesselType": "type"
    })

    needed = ["mmsi", "time", "lat", "lon", "speed", "course", "name", "type"]
    df = df[[c for c in needed if c in df.columns]]
    df = df.dropna(subset=["mmsi", "lat", "lon", "time"])

    # Spatial filter
    df = df[
        (df["lat"] >= BOUNDS["min_lat"]) & (df["lat"] <= BOUNDS["max_lat"]) &
        (df["lon"] >= BOUNDS["min_lon"]) & (df["lon"] <= BOUNDS["max_lon"])
    ]

    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"])
    df["speed"] = pd.to_numeric(df["speed"], errors="coerce").fillna(0)
    df["course"] = pd.to_numeric(df["course"], errors="coerce").fillna(0)
    df = df.sort_values(["mmsi", "time"])

    _cached_data = df
    return df


@app.route("/")
def home():
    return render_template("index.html", mapbox_token=MAPBOX_TOKEN)


@app.route("/api/routes")
def routes():
    df = load_and_process()
    if df is None:
        return jsonify({"error": f"{CSV_FILE} not found"}), 404

    ships = []
    for mmsi, group in df.groupby("mmsi"):
        group = group.drop_duplicates(subset=["lat", "lon", "time"])
        if len(group) < MIN_POINTS_PER_SHIP:
            continue
        avg_speed = group["speed"].mean()
        if avg_speed < MIN_AVG_SOG:
            continue

        if len(group) > MAX_POINTS_PER_SHIP:
            step = max(1, len(group) // MAX_POINTS_PER_SHIP)
            group = group.iloc[::step]

        points = group.to_dict("records")
        coords = []
        for i, row in enumerate(points):
            lon = float(row["lon"])
            lat = float(row["lat"])
            if i < len(points) - 1:
                hdg = bearing(lon, lat,
                              float(points[i + 1]["lon"]),
                              float(points[i + 1]["lat"]))
            else:
                hdg = float(row["course"]) if row.get("course") else 0
            coords.append({
                "lon": lon,
                "lat": lat,
                "time": row["time"].strftime("%Y-%m-%dT%H:%M:%S"),
                "speed": round(float(row["speed"]), 2),
                "heading": round(hdg, 1)
            })

        first = points[0]
        type_code = str(first.get("type", "0"))
        ships.append({
            "mmsi": str(mmsi),
            "name": str(first.get("name", "Unknown Vessel")).strip() or "Unknown Vessel",
            "type_code": type_code,
            "type_name": get_vessel_type_name(first.get("type")),
            "avg_speed": round(float(avg_speed), 2),
            "route": coords
        })

    # Sort by route length (most data = most interesting)
    ships = sorted(ships, key=lambda x: len(x["route"]), reverse=True)[:MAX_SHIPS]

    all_times = [p["time"] for s in ships for p in s["route"]]

    return jsonify({
        "project": "Houston Maritime Intelligence Dashboard",
        "center": [-95.05, 29.72],
        "camera": {"zoom": 11.5, "pitch": 65, "bearing": -35},
        "total_ships": len(ships),
        "start_time": min(all_times) if all_times else None,
        "end_time": max(all_times) if all_times else None,
        "ships": ships
    })


@app.route("/api/summary")
def summary():
    df = load_and_process()
    if df is None:
        return jsonify({"error": f"{CSV_FILE} not found"}), 404

    total_records = len(df)
    unique_vessels = df["mmsi"].nunique()
    avg_speed = round(float(df["speed"].mean()), 2)

    type_counts_raw = df.drop_duplicates("mmsi")["type"].value_counts()
    type_counts = {}
    for k, v in type_counts_raw.items():
        name = get_vessel_type_name(k)
        type_counts[name] = int(v)

    top_type = max(type_counts, key=type_counts.get) if type_counts else "Unknown"

    top_vessels = (
        df.groupby("mmsi")
        .agg(
            name=("name", "first"),
            records=("mmsi", "count"),
            avg_speed=("speed", "mean")
        )
        .sort_values("records", ascending=False)
        .head(10)
        .reset_index()
    )
    top_vessels_list = [
        {
            "mmsi": str(row["mmsi"]),
            "name": str(row["name"]).strip() or "Unknown",
            "records": int(row["records"]),
            "avg_speed": round(float(row["avg_speed"]), 2)
        }
        for _, row in top_vessels.iterrows()
    ]

    # Busiest zone: divide bay into quadrants
    df["zone"] = df.apply(lambda r: (
        "Upper Ship Channel" if r["lat"] > 29.72 and r["lon"] < -95.05 else
        "Galveston Bay" if r["lat"] <= 29.55 else
        "Lower Ship Channel" if r["lon"] < -95.05 else
        "Bayport / La Porte"
    ), axis=1)
    busiest_zone = df["zone"].value_counts().idxmax()

    return jsonify({
        "total_records": total_records,
        "unique_vessels": unique_vessels,
        "avg_speed_knots": avg_speed,
        "vessel_type_counts": type_counts,
        "top_vessel_type": top_type,
        "busiest_zone": busiest_zone,
        "top_vessels": top_vessels_list,
        "time_range": {
            "start": str(df["time"].min()),
            "end": str(df["time"].max())
        }
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
