"""Load Synthea FHIR R4 bundles into the warehouse (optional, richer data).

1. Download Synthea's sample FHIR data (https://synthea.mitre.org/downloads)
   and unzip the *.json bundles into data/synthea/
2. python data/load_synthea.py data/synthea

Patients are split across the two demo tenants by a hash of their id.
"""
import json
import os
import sys
import zlib
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()


def _date(value):
    return value[:10] if value else None


def _coding(resource, field="code"):
    coding = (resource.get(field) or {}).get("coding") or [{}]
    return coding[0].get("code"), coding[0].get("display")


def parse_bundle(path: Path):
    bundle = json.loads(path.read_text(encoding="utf-8"))
    out = {"patients": [], "conditions": [], "observations": [], "encounters": []}
    tenant = None
    for entry in bundle.get("entry", []):
        r = entry.get("resource", {})
        kind = r.get("resourceType")
        if kind == "Patient":
            tenant = 1 + zlib.crc32(r["id"].encode()) % 2
            name = (r.get("name") or [{}])[0]
            addr = (r.get("address") or [{}])[0]
            phone = next((t.get("value") for t in r.get("telecom", []) if t.get("system") == "phone"), None)
            out["patients"].append((r["id"], tenant, " ".join(name.get("given", [])), name.get("family"),
                                    phone, r.get("gender"), _date(r.get("birthDate")),
                                    _date(r.get("deceasedDateTime")), addr.get("city"), addr.get("state")))
        elif kind == "Condition":
            code, desc = _coding(r)
            out["conditions"].append((tenant, r["subject"]["reference"].split(":")[-1].split("/")[-1], code, desc,
                                      _date(r.get("onsetDateTime")), _date(r.get("abatementDateTime"))))
        elif kind == "Observation" and "valueQuantity" in r:
            code, desc = _coding(r)
            q = r["valueQuantity"]
            out["observations"].append((tenant, r["subject"]["reference"].split(":")[-1].split("/")[-1], code, desc,
                                        q.get("value"), q.get("unit"), _date(r.get("effectiveDateTime"))))
        elif kind == "Encounter":
            period = r.get("period", {})
            types = r.get("type") or [{}]
            out["encounters"].append((r["id"], tenant, r["subject"]["reference"].split(":")[-1].split("/")[-1],
                                      (r.get("class") or {}).get("code"), types[0].get("text"),
                                      _date(period.get("start")), _date(period.get("end"))))
    return out


def main(folder: str):
    files = sorted(Path(folder).glob("*.json"))
    with psycopg.connect(os.environ["ADMIN_DB_URL"]) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO tenants (id, name) VALUES (1, 'Sunrise Health'), (2, 'Lakeside Clinic') "
                    "ON CONFLICT DO NOTHING")
        for i, f in enumerate(files, 1):
            d = parse_bundle(f)
            if not d["patients"]:
                continue  # hospital/practitioner bundles
            cur.executemany("INSERT INTO patients VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                            d["patients"])
            cur.executemany("INSERT INTO conditions (tenant_id, patient_id, code, description, onset_date,"
                            " abatement_date) VALUES (%s,%s,%s,%s,%s,%s)", d["conditions"])
            cur.executemany("INSERT INTO observations (tenant_id, patient_id, code, description, value_numeric,"
                            " unit, effective_date) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            [o for o in d["observations"] if o[6]])
            cur.executemany("INSERT INTO encounters VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                            [e for e in d["encounters"] if e[5]])
            if i % 50 == 0:
                conn.commit()
                print(f"{i}/{len(files)} bundles")
        conn.commit()
    print(f"Done: {len(files)} bundles")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/synthea")
