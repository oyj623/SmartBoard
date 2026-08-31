#!/usr/bin/env python3
"""
Build the example database.

    python example/seed.py

Twelve real Malaysian cities, eight products, 180 days of trading. Everything is
synthetic but deterministic — the seeded RNG means every clone of this repo gets
byte-identical numbers, so the tests can assert on them and the README can quote
them.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "cafe.db"

# Real coordinates. The map panel is only interesting if the dots are in the
# right places.
CITIES = [
    # name,             lat,     lng,     size weight
    ("Kuala Lumpur",    3.1390, 101.6869, 3.4),
    ("Petaling Jaya",   3.1073, 101.6067, 2.1),
    ("Shah Alam",       3.0733, 101.5185, 1.6),
    ("Johor Bahru",     1.4927, 103.7414, 2.0),
    ("George Town",     5.4141, 100.3288, 1.8),
    ("Ipoh",            4.5975, 101.0901, 1.2),
    ("Melaka",          2.1896, 102.2501, 1.1),
    ("Seremban",        2.7297, 101.9381, 0.9),
    ("Kuantan",         3.8077, 103.3260, 0.8),
    ("Kota Bharu",      6.1254, 102.2381, 0.7),
    ("Kuching",         1.5535, 110.3593, 1.3),
    ("Kota Kinabalu",   5.9804, 116.0735, 1.1),
]

# product, category, base price, base daily units per unit-of-city-weight
PRODUCTS = [
    ("kopi_o",     "drink", 4.50, 42),
    ("teh_tarik",  "drink", 4.80, 38),
    ("latte",      "drink", 11.50, 26),
    ("espresso",   "drink", 8.50, 14),
    ("matcha",     "drink", 13.00, 11),
    ("kaya_toast", "food",  6.50, 22),
    ("croissant",  "food",  9.00, 13),
    ("nasi_lemak", "food",  12.00, 18),
]

DAYS = 180
END = date(2026, 8, 31)

SCHEMA = """
DROP TABLE IF EXISTS sales;
DROP TABLE IF EXISTS cities;

CREATE TABLE cities (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    lat  REAL NOT NULL,
    lng  REAL NOT NULL
);

CREATE TABLE sales (
    id          INTEGER PRIMARY KEY,
    city_id     INTEGER NOT NULL REFERENCES cities(id),
    date        TEXT NOT NULL,
    product     TEXT NOT NULL,
    category    TEXT NOT NULL,
    qty         INTEGER NOT NULL,
    revenue_myr REAL NOT NULL
);

CREATE INDEX ix_sales_date ON sales(date);
CREATE INDEX ix_sales_city ON sales(city_id);
"""


def build() -> None:
    rng = random.Random(7)
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)

    conn.executemany(
        "INSERT INTO cities (id, name, lat, lng) VALUES (?, ?, ?, ?)",
        [(i + 1, name, lat, lng) for i, (name, lat, lng, _) in enumerate(CITIES)],
    )

    start = END - timedelta(days=DAYS - 1)
    rows = []

    for city_id, (_, _, _, weight) in enumerate(CITIES, start=1):
        for offset in range(DAYS):
            day = start + timedelta(days=offset)
            iso = day.isoformat()

            # Weekends are busier for food, quieter for the morning coffee run.
            weekend = day.weekday() >= 5
            # A gentle upward trend over the six months, so a line chart says
            # something rather than wobbling around a flat mean.
            trend = 1.0 + 0.18 * (offset / DAYS)

            for product, category, price, base in PRODUCTS:
                shape = 1.25 if (weekend and category == "food") else (0.82 if weekend else 1.0)
                expected = base * weight * shape * trend
                qty = max(0, int(rng.gauss(expected, expected * 0.18)))
                if qty == 0:
                    continue
                # Occasional promotions, so unit price is not a constant.
                discount = 0.85 if rng.random() < 0.06 else 1.0
                revenue = round(qty * price * discount * rng.uniform(0.99, 1.01), 2)
                rows.append((city_id, iso, product, category, qty, revenue))

    conn.executemany(
        "INSERT INTO sales (city_id, date, product, category, qty, revenue_myr) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()

    total = conn.execute("SELECT COUNT(*), SUM(revenue_myr) FROM sales").fetchone()
    conn.close()

    print(f"Wrote {DB}")
    print(f"  {len(CITIES)} cities · {len(PRODUCTS)} products · {DAYS} days")
    print(f"  {total[0]:,} sales rows · RM {total[1]:,.0f} total revenue")


if __name__ == "__main__":
    build()
