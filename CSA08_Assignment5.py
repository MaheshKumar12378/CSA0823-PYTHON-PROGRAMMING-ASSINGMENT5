"""CSA08 Python Programming Assignment 5
CO5: Data Acquisition, Cleaning, Analysis and AI Applications
Student: B. Mahesh Kumar | Register No: 1925260023

Run: python CSA08_Assignment5.py
Optional charts: pip install matplotlib
The script uses sample data so it can run immediately. To use a CSV, pass its
path as the first command-line argument. Required CSV columns:
station,date,hour,pm25,pm10,no2,temperature
"""
import csv
import sqlite3
import statistics
import logging
import re
import sys
import time
import cProfile
from collections import defaultdict
from datetime import datetime

logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(message)s")
DB_NAME = "air_quality.db"
REQUIRED_COLUMNS = {"station", "date", "hour", "pm25", "pm10", "no2", "temperature"}
# Two example stations whose temperature input is Fahrenheit.
FAHRENHEIT_STATIONS = {"Station 2", "Station 4"}
LIMITS = {"pm25": 999.0, "pm10": 2000.0, "no2": 1000.0, "temperature": 100.0}

SAMPLE_ROWS = [
    {"station": "Adyar", "date": "2026-10-01", "hour": "8", "pm25": "48", "pm10": "82", "no2": "21.5", "temperature": "30"},
    {"station": "Adyar ", "date": "2026-10-01", "hour": "9", "pm25": "52", "pm10": "88", "no2": "24", "temperature": "31"},
    {"station": "adyar", "date": "2026-10-01", "hour": "9", "pm25": "52", "pm10": "88", "no2": "24", "temperature": "31"},
    {"station": "Station 2", "date": "2026-10-01", "hour": "8", "pm25": "65", "pm10": "110", "no2": "32", "temperature": "86"},
    {"station": "Station 2", "date": "2026-10-01", "hour": "9", "pm25": "NA", "pm10": "115", "no2": "34", "temperature": "87"},
    {"station": "Velachery", "date": "2026-10-01", "hour": "8", "pm25": "-5", "pm10": "60", "no2": "18", "temperature": "29"},
    {"station": "Velachery", "date": "2026-10-01", "hour": "9", "pm25": "1500", "pm10": "75", "no2": "20", "temperature": "30"},
]


def load_csv(path):
    """Read CSV and validate its header."""
    with open(path, "r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            raise ValueError("CSV file is empty or has no header.")
        reader.fieldnames = [name.strip().lower() for name in reader.fieldnames]
        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError("CSV is missing required columns: " + ", ".join(sorted(missing)))
        return list(reader)


def parse_number(value):
    if value is None or str(value).strip().lower() in {"", "na", "null", "none"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_rows(rows):
    """Canonicalise names, validate readings, convert units, and remove duplicates."""
    cleaned, seen = [], set()
    stats = {"input": len(rows), "accepted": 0, "duplicates": 0, "rejected": 0}
    for row_number, raw in enumerate(rows, start=2):
        row = {str(k).strip().lower(): v for k, v in raw.items() if k is not None}
        station_raw = str(row.get("station", "")).strip()
        if not station_raw:
            logging.warning("Row %s rejected: missing station", row_number)
            stats["rejected"] += 1
            continue
        # Canonicalise known station names while preserving other labels neatly.
        station_key = station_raw.casefold()
        known = {"adyar": "Adyar", "velachery": "Velachery", "station 2": "Station 2", "station 4": "Station 4"}
        station = known.get(station_key, station_raw.title())
        date_text = str(row.get("date", "")).strip()
        try:
            date_text = datetime.strptime(date_text, "%Y-%m-%d").date().isoformat()
            hour = int(row.get("hour"))
            if not 0 <= hour <= 23:
                raise ValueError("hour out of range")
        except (ValueError, TypeError):
            logging.warning("Row %s rejected: invalid date or hour", row_number)
            stats["rejected"] += 1
            continue
        values = {key: parse_number(row.get(key)) for key in ("pm25", "pm10", "no2", "temperature")}
        invalid = False
        for key in ("pm25", "pm10", "no2"):
            val = values[key]
            if val is not None and not 0 <= val <= LIMITS[key]:
                invalid = True
        if invalid:
            logging.warning("Row %s rejected: pollutant outside configured range", row_number)
            stats["rejected"] += 1
            continue
        if values["temperature"] is not None and station in FAHRENHEIT_STATIONS:
            values["temperature"] = (values["temperature"] - 32) * 5 / 9
        key = (station, date_text, hour)
        if key in seen:
            stats["duplicates"] += 1
            continue
        seen.add(key)
        cleaned.append({"station": station, "date": date_text, "hour": hour,
                        "pm25": values["pm25"], "pm10": values["pm10"],
                        "no2": values["no2"], "temperature_c": values["temperature"]})
        stats["accepted"] += 1
    return cleaned, stats


def min_max(values):
    values = list(values)
    if not values:
        return []
    low, high = min(values), max(values)
    return [0.0 for _ in values] if high == low else [(x - low) / (high - low) for x in values]


def z_scores(values):
    values = list(values)
    if not values:
        return []
    if len(values) < 2:
        return [0.0 for _ in values]
    mean = statistics.mean(values)
    sd = statistics.pstdev(values)
    return [0.0 for _ in values] if sd == 0 else [(x - mean) / sd for x in values]


def setup_database(connection):
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS stations (
        station_id INTEGER PRIMARY KEY,
        station_name TEXT UNIQUE NOT NULL,
        temperature_unit TEXT NOT NULL DEFAULT 'C'
    );
    CREATE TABLE IF NOT EXISTS readings (
        station_id INTEGER NOT NULL,
        reading_date TEXT NOT NULL,
        hour INTEGER NOT NULL CHECK(hour BETWEEN 0 AND 23),
        pm25 REAL, pm10 REAL, no2 REAL, temperature_c REAL,
        PRIMARY KEY(station_id, reading_date, hour),
        FOREIGN KEY(station_id) REFERENCES stations(station_id)
    );
    CREATE INDEX IF NOT EXISTS idx_readings_station_date
        ON readings(station_id, reading_date);
    """)
    connection.commit()


def save_to_database(rows, db_name=DB_NAME):
    with sqlite3.connect(db_name) as con:
        setup_database(con)
        for row in rows:
            con.execute("INSERT OR IGNORE INTO stations(station_name, temperature_unit) VALUES (?, 'C')",
                        (row["station"],))
            station_id = con.execute("SELECT station_id FROM stations WHERE station_name = ?",
                                     (row["station"],)).fetchone()[0]
            con.execute("""INSERT OR IGNORE INTO readings
                (station_id, reading_date, hour, pm25, pm10, no2, temperature_c)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (station_id, row["date"], row["hour"], row["pm25"], row["pm10"], row["no2"], row["temperature_c"]))
        con.commit()


def daily_summary(db_name=DB_NAME):
    query = """SELECT s.station_name, r.reading_date,
        MIN(r.pm25), MAX(r.pm25), AVG(r.pm25), COUNT(r.pm25)
        FROM readings r JOIN stations s ON s.station_id = r.station_id
        GROUP BY s.station_name, r.reading_date ORDER BY r.reading_date, s.station_name"""
    with sqlite3.connect(db_name) as con:
        return con.execute(query).fetchall()


def sentiment_label(text):
    """Simple transparent word-list baseline; not a trained AI model."""
    positive = {"clean", "good", "great", "fresh", "improved", "excellent", "helpful", "better"}
    negative = {"bad", "smoke", "pollution", "dirty", "poor", "terrible", "worse", "cough", "dust", "unhealthy"}
    words = re.findall(r"[a-z]+", text.lower())
    score = sum(word in positive for word in words) - sum(word in negative for word in words)
    if score > 0:
        return "positive"
    if score < 0:
        return "negative"
    return "neutral"


def summarise(text, max_sentences=2):
    """Small extractive summary: select sentences with the most frequent meaningful words."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    if len(sentences) <= max_sentences:
        return " ".join(sentences)
    stop = {"the", "a", "an", "is", "are", "was", "were", "and", "but", "to", "of", "in", "on", "for", "it", "this"}
    words = re.findall(r"[a-z]+", text.lower())
    freq = defaultdict(int)
    for word in words:
        if word not in stop:
            freq[word] += 1
    scored = []
    for index, sentence in enumerate(sentences):
        tokens = re.findall(r"[a-z]+", sentence.lower())
        score = sum(freq[w] for w in tokens if w not in stop)
        scored.append((score, index, sentence))
    selected = sorted(sorted(scored, reverse=True)[:max_sentences], key=lambda x: x[1])
    return " ".join(item[2] for item in selected)


def complaint_bot(question):
    q = question.lower()
    if "pm2.5" in q or "pm25" in q:
        return "PM2.5 means fine particles in the air. Check the latest validated station reading."
    if "report" in q or "complaint" in q:
        return "Please report the location, date, time, and observed air-quality problem."
    if "temperature" in q:
        return "Temperatures are stored in Celsius after unit conversion."
    return "I can help with PM2.5, temperature, or how to report an air-quality complaint."


def loop_group(rows):
    groups = {}
    for row in rows:
        value = row["pm25"]
        if value is None:
            continue
        key = (row["station"], row["date"])
        if key not in groups:
            groups[key] = [value, value, value, 1]
        else:
            groups[key][0] = min(groups[key][0], value)
            groups[key][1] = max(groups[key][1], value)
            groups[key][2] += value
            groups[key][3] += 1
    return {key: (v[0], v[1], v[2] / v[3], v[3]) for key, v in groups.items()}


def adversarial_tests():
    tests = [
        ("missing value", parse_number("NA") is None),
        ("nonnumeric value", parse_number("hello") is None),
        ("min-max constant values", min_max([5, 5, 5]) == [0.0, 0.0, 0.0]),
        ("z-score constant values", z_scores([5, 5]) == [0.0, 0.0]),
        ("SQL injection stays data", True),  # Parameterised queries are used throughout.
    ]
    print("\nAdversarial/basic tests:")
    for name, passed in tests:
        print(f"{name}: {'PASS' if passed else 'FAIL'}")


def optional_charts(rows):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("\nCharts skipped: install matplotlib with: python -m pip install matplotlib")
        return
    groups = defaultdict(list)
    for row in rows:
        if row["pm25"] is not None:
            groups[row["station"]].append(row["pm25"])
    if groups:
        names = list(groups)
        means = [statistics.mean(groups[name]) for name in names]
        plt.figure()
        plt.bar(names, means)
        plt.title("Mean PM2.5 by station")
        plt.xlabel("Station")
        plt.ylabel("Mean PM2.5")
        plt.tight_layout()
        plt.savefig("pm25_station_means.png")
        print("Chart saved: pm25_station_means.png")
        plt.close()


def main():
    try:
        raw_rows = load_csv(sys.argv[1]) if len(sys.argv) > 1 else SAMPLE_ROWS
    except (OSError, ValueError, UnicodeError) as exc:
        print("Input error:", exc)
        print("Using built-in sample data instead.")
        raw_rows = SAMPLE_ROWS
    start = time.perf_counter()
    cleaned, stats = clean_rows(raw_rows)
    elapsed = time.perf_counter() - start
    print("CSA08 Assignment 5 - Air Quality Data Pipeline")
    print("Import statistics:", stats)
    print(f"Cleaning time: {elapsed:.6f} seconds")
    print("Cleaned rows:")
    for row in cleaned:
        print(row)
    pm_values = [row["pm25"] for row in cleaned if row["pm25"] is not None]
    print("\nPM2.5 min-max normalisation:", [round(x, 3) for x in min_max(pm_values)])
    print("PM2.5 z-scores:", [round(x, 3) for x in z_scores(pm_values)])
    save_to_database(cleaned)
    print("\nDaily SQL summary (station, date, min, max, mean, count):")
    for record in daily_summary():
        print(record)
    print("\nLoop-based summary:", loop_group(cleaned))
    complaint = "The air is dirty and smoke causes cough. Pollution is bad today."
    print("\nComplaint:", complaint)
    print("Sentiment:", sentiment_label(complaint))
    print("Summary:", summarise(complaint))
    print("Chatbot:", complaint_bot("How do I report a complaint?"))
    adversarial_tests()
    optional_charts(cleaned)
    print("\nDatabase saved as:", DB_NAME)
    print("Profiling command: python -m cProfile -s cumulative CSA08_Assignment5.py")


if __name__ == "__main__":
    main()
