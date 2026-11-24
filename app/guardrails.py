"""SQL guardrail: every query the agent writes passes through here before it runs.

The database also enforces read-only access, column grants and row-level security,
so this layer is the first line of defense, not the only one.
"""
import sqlglot
from sqlglot import exp

# Patient identifiers. (A bare "name" is not listed: tenants.name is an organisation name, not PHI.
# Patient names live in first_name/last_name, which the DB role cannot read anyway.)
PHI_COLUMNS = {"first_name", "last_name", "phone", "address", "ssn", "email"}
BLOCKED_FUNCTIONS = {"pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "dblink",
                     "set_config", "current_setting", "lo_import", "lo_export", "pg_terminate_backend"}
WRITE_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command,
               exp.Merge, exp.TruncateTable, exp.Grant)


class GuardrailViolation(ValueError):
    def __init__(self, message: str, kind: str):
        super().__init__(message)
        self.kind = kind  # "write" | "phi" | "unsafe" | "invalid"


def check_sql(sql: str, max_rows: int = 200) -> str:
    """Validate agent SQL. Returns the (possibly rewritten) safe SQL or raises GuardrailViolation."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise GuardrailViolation(f"SQL does not parse: {e}", "invalid") from e
    if len(statements) != 1:
        raise GuardrailViolation("Exactly one SQL statement is allowed.", "unsafe")
    stmt = statements[0]

    if any(stmt.find(node) for node in WRITE_NODES) or not isinstance(stmt, exp.Query):
        raise GuardrailViolation("Only read-only SELECT queries are allowed.", "write")

    for col in stmt.find_all(exp.Column):
        if col.name.lower() in PHI_COLUMNS:
            raise GuardrailViolation(f"Column '{col.name}' is protected health information.", "phi")

    for star in stmt.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            raise GuardrailViolation("SELECT * is not allowed; name the columns you need.", "phi")

    for fn in stmt.find_all(exp.Func):
        name = (fn.name if isinstance(fn, exp.Anonymous) else fn.sql_name()).lower()
        if name in BLOCKED_FUNCTIONS:
            raise GuardrailViolation(f"Function '{name}' is not allowed.", "unsafe")

    if isinstance(stmt, exp.Select) and not stmt.args.get("limit"):
        stmt = stmt.limit(max_rows)
    return stmt.sql(dialect="postgres")
