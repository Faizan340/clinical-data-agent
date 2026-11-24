import pytest

from app.guardrails import GuardrailViolation, check_sql


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM patients",
    "SELECT gender, COUNT(DISTINCT id) FROM patients GROUP BY gender",
    "WITH d AS (SELECT patient_id FROM conditions WHERE code = '44054006') SELECT COUNT(*) FROM d",
])
def test_allows_read_only_aggregates(sql):
    assert check_sql(sql)


def test_organisation_name_is_not_phi():
    assert check_sql("SELECT t.name, COUNT(*) FROM tenants t GROUP BY t.name")


def test_adds_limit_when_missing():
    assert "LIMIT 50" in check_sql("SELECT city FROM patients", max_rows=50)


def test_keeps_existing_limit():
    assert "LIMIT 5" in check_sql("SELECT city FROM patients LIMIT 5")


@pytest.mark.parametrize("sql,kind", [
    ("DELETE FROM patients", "write"),
    ("UPDATE patients SET city = 'x'", "write"),
    ("DROP TABLE patients", "write"),
    ("INSERT INTO tenants VALUES (3, 'x')", "write"),
    ("SELECT 1; DROP TABLE patients", "unsafe"),
    ("SELECT first_name, last_name FROM patients", "phi"),
    ("SELECT p.phone FROM patients p", "phi"),
    ("SELECT * FROM patients", "phi"),
    ("SELECT pg_sleep(10)", "unsafe"),
    ("SELECT current_setting('app.tenant_id')", "unsafe"),
    ("SELEC COUNT(*) FROM", "invalid"),
])
def test_blocks_unsafe_sql(sql, kind):
    with pytest.raises(GuardrailViolation) as err:
        check_sql(sql)
    assert err.value.kind == kind
