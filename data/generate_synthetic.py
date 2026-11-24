"""Generate a small, deterministic synthetic clinical dataset (no real patients).

Good for local dev, tests and CI. For a richer dataset, load Synthea output with
data/load_synthea.py instead (see README).

Usage:  python data/generate_synthetic.py --patients 1200
"""
import argparse
import os
import random
import uuid
from datetime import date, timedelta

import psycopg
from dotenv import load_dotenv

load_dotenv()

TENANTS = [(1, "Sunrise Health"), (2, "Lakeside Clinic")]
CITIES = [("Boston", "MA"), ("Springfield", "MA"), ("Worcester", "MA"), ("Hartford", "CT"),
          ("Providence", "RI"), ("Albany", "NY"), ("Buffalo", "NY"), ("Newark", "NJ")]
FIRST = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Aisha", "Wei", "Ravi", "Sofia"]
LAST = ["Smith", "Johnson", "Brown", "Garcia", "Khan", "Chen", "Patel", "Lopez", "Nguyen", "Davis"]

# SNOMED CT codes: (code, description, prevalence)
CONDITIONS = [
    ("44054006", "Diabetes mellitus type 2", 0.22),
    ("15777000", "Prediabetes", 0.15),
    ("38341003", "Hypertension", 0.35),
    ("433144002", "Chronic kidney disease stage 3", 0.08),
    ("195967001", "Asthma", 0.10),
]
HBA1C = ("4548-4", "Hemoglobin A1c/Hemoglobin.total in Blood", "%")
BMI = ("39156-5", "Body mass index", "kg/m2")
SBP = ("8480-6", "Systolic blood pressure", "mm[Hg]")
ENC_CLASSES = [("wellness", "General examination"), ("ambulatory", "Follow-up visit"),
               ("emergency", "Emergency room visit"), ("inpatient", "Hospital admission")]


def rand_date(rng, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, max((end - start).days, 0)))


def generate(n_patients: int, seed: int = 42):
    rng = random.Random(seed)
    today = date.today()
    patients, conditions, observations, encounters = [], [], [], []

    for _ in range(n_patients):
        pid = str(uuid.UUID(int=rng.getrandbits(128)))
        tenant = 1 if rng.random() < 0.6 else 2
        birth = rand_date(rng, date(1935, 1, 1), date(2010, 12, 31))
        deceased = rand_date(rng, today - timedelta(days=900), today) if rng.random() < 0.04 else None
        city, state = rng.choice(CITIES)
        patients.append((pid, tenant, rng.choice(FIRST), rng.choice(LAST),
                         f"555-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}",
                         rng.choice(["male", "female"]), birth, deceased, city, state))
        age = (today - birth).days // 365
        age_boost = 1.0 + max(age - 40, 0) / 40  # older patients carry more chronic disease

        has_diabetes = False
        for code, desc, prevalence in CONDITIONS:
            if rng.random() < prevalence * age_boost:
                onset = rand_date(rng, birth + timedelta(days=365 * 18), today - timedelta(days=30))
                abated = None
                if code in ("15777000", "195967001") and rng.random() < 0.3:
                    abated = rand_date(rng, onset, today)
                conditions.append((tenant, pid, code, desc, onset, abated))
                has_diabetes |= code == "44054006" and abated is None

        # HbA1c: diabetics get 0-6 tests over the last 2 years (some none recently -> care gaps)
        if has_diabetes:
            level = rng.uniform(6.0, 11.0)
            for _ in range(rng.choice([0, 1, 2, 3, 4, 6])):
                level = min(max(level + rng.uniform(-0.8, 0.8), 5.5), 13.0)
                observations.append((tenant, pid, *HBA1C[:2], round(level, 1), HBA1C[2],
                                     rand_date(rng, today - timedelta(days=730), today)))
        elif rng.random() < 0.2:
            observations.append((tenant, pid, *HBA1C[:2], round(rng.uniform(4.6, 6.4), 1), HBA1C[2],
                                 rand_date(rng, today - timedelta(days=730), today)))

        for _ in range(rng.randint(0, 3)):
            d = rand_date(rng, today - timedelta(days=1095), today)
            observations.append((tenant, pid, *BMI[:2], round(rng.uniform(18, 42), 1), BMI[2], d))
            observations.append((tenant, pid, *SBP[:2], rng.randint(100, 175), SBP[2], d))

        for _ in range(rng.randint(0, 6)):
            cls, desc = rng.choices(ENC_CLASSES, weights=[4, 6, 1.5, 0.7])[0]
            start = rand_date(rng, today - timedelta(days=1095), today)
            end = start + timedelta(days=rng.randint(1, 7)) if cls == "inpatient" else start
            encounters.append((str(uuid.UUID(int=rng.getrandbits(128))), tenant, pid, cls, desc, start, end))

    return patients, conditions, observations, encounters


def load(conn, patients, conditions, observations, encounters):
    with conn.cursor() as cur:
        cur.execute("TRUNCATE encounters, observations, conditions, patients, tenants RESTART IDENTITY CASCADE")
        cur.executemany("INSERT INTO tenants (id, name) VALUES (%s, %s)", TENANTS)
        cur.executemany("INSERT INTO patients (id, tenant_id, first_name, last_name, phone, gender, birth_date,"
                        " deceased_date, city, state) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", patients)
        cur.executemany("INSERT INTO conditions (tenant_id, patient_id, code, description, onset_date,"
                        " abatement_date) VALUES (%s,%s,%s,%s,%s,%s)", conditions)
        cur.executemany("INSERT INTO observations (tenant_id, patient_id, code, description, value_numeric,"
                        " unit, effective_date) VALUES (%s,%s,%s,%s,%s,%s,%s)", observations)
        cur.executemany("INSERT INTO encounters (id, tenant_id, patient_id, encounter_class, description,"
                        " start_date, end_date) VALUES (%s,%s,%s,%s,%s,%s,%s)", encounters)
    conn.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--patients", type=int, default=1200)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    data = generate(args.patients, args.seed)
    with psycopg.connect(os.environ["ADMIN_DB_URL"]) as conn:
        load(conn, *data)
    print("Loaded: " + ", ".join(f"{n}={len(rows)}" for n, rows in
                                 zip(["patients", "conditions", "observations", "encounters"], data)))
