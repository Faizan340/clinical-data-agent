-- Clinical warehouse schema (synthetic data only).
-- Every table carries tenant_id so row-level security can isolate tenants.

CREATE TABLE tenants (
    id   INT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE patients (
    id            TEXT PRIMARY KEY,
    tenant_id     INT  NOT NULL REFERENCES tenants(id),
    first_name    TEXT,          -- PHI: hidden from the agent
    last_name     TEXT,          -- PHI: hidden from the agent
    phone         TEXT,          -- PHI: hidden from the agent
    gender        TEXT,          -- 'male' / 'female'
    birth_date    DATE NOT NULL,
    deceased_date DATE,
    city          TEXT,
    state         TEXT
);

CREATE TABLE conditions (
    id             BIGSERIAL PRIMARY KEY,
    tenant_id      INT  NOT NULL REFERENCES tenants(id),
    patient_id     TEXT NOT NULL REFERENCES patients(id),
    code           TEXT NOT NULL,  -- SNOMED CT
    description    TEXT,
    onset_date     DATE,
    abatement_date DATE            -- NULL = still active
);

CREATE TABLE observations (
    id             BIGSERIAL PRIMARY KEY,
    tenant_id      INT  NOT NULL REFERENCES tenants(id),
    patient_id     TEXT NOT NULL REFERENCES patients(id),
    code           TEXT NOT NULL,  -- LOINC
    description    TEXT,
    value_numeric  NUMERIC,
    unit           TEXT,
    effective_date DATE NOT NULL
);

CREATE TABLE encounters (
    id              TEXT PRIMARY KEY,
    tenant_id       INT  NOT NULL REFERENCES tenants(id),
    patient_id      TEXT NOT NULL REFERENCES patients(id),
    encounter_class TEXT,          -- wellness / ambulatory / emergency / inpatient
    description     TEXT,
    start_date      DATE NOT NULL,
    end_date        DATE
);

CREATE INDEX idx_cond_patient_code  ON conditions   (patient_id, code);
CREATE INDEX idx_obs_patient_code   ON observations (patient_id, code, effective_date DESC);
CREATE INDEX idx_enc_patient_start  ON encounters   (patient_id, start_date);
