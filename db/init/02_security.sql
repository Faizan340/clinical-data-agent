-- Defense in depth for the agent's database user:
--   1. read-only role (SELECT only)
--   2. column-level grants: PHI columns on patients are not readable at all
--   3. row-level security: a session only sees rows of its own tenant

CREATE ROLE analyst_ro LOGIN PASSWORD 'analyst_ro';
GRANT CONNECT ON DATABASE clinical TO analyst_ro;
GRANT USAGE ON SCHEMA public TO analyst_ro;

GRANT SELECT ON tenants, conditions, observations, encounters TO analyst_ro;
GRANT SELECT (id, tenant_id, gender, birth_date, deceased_date, city, state) ON patients TO analyst_ro;

ALTER ROLE analyst_ro SET statement_timeout = '5s';
ALTER ROLE analyst_ro SET default_transaction_read_only = on;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['patients', 'conditions', 'observations', 'encounters'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        -- current_setting(..., true) returns NULL when unset, so an unset tenant sees nothing.
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I FOR SELECT TO analyst_ro '
            'USING (tenant_id = current_setting(''app.tenant_id'', true)::int)', t);
    END LOOP;
END $$;
