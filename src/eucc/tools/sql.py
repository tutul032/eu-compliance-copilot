"""SQL tool: answer data questions from the energy gold tables of Project 1 (read-only).

    load_catalog()                          -> tables, columns, examples (energy_catalog.yaml)
    generate_sql(question, catalog, today)  -> one SELECT from gpt-oss, as JSON {"sql": ...}
    validate_sql(sql, allowed_tables)       -> the checked query, with LIMIT 200 if it had none
    run_sql(sql)                            -> {"columns", "types", "rows"} from a SQL warehouse
    query(question, catalog, today)         -> generate, validate, run; one retry with the error

Safety is layered: the model sees only the catalog, the validator (sqlglot) lets through exactly
one SELECT over the whitelisted, fully qualified workspace.energy_prod tables with no DDL/DML and
no side-effecting functions, and the warehouse runs it as the caller, who only needs SELECT on
energy_prod. Project 1 owns those tables; nothing here writes to them.

The warehouse is SQL_WAREHOUSE_ID unless EUCC_SQL_WAREHOUSE_ID is set (the bundle variable
sql_warehouse_id reaches the eval_agent job as --warehouse-id, which sets it).
"""

import datetime as dt
import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import mlflow
import sqlglot
import yaml
from sqlglot import exp

from eucc import rag

CATALOG_PATH = Path(__file__).resolve().with_name("energy_catalog.yaml")
SQL_WAREHOUSE_ID = "4228ff3a910b0b5e"
WAREHOUSE_ENV = "EUCC_SQL_WAREHOUSE_ID"
ROW_LIMIT = 200
WAIT_TIMEOUT = "30s"  # The API's maximum wait for a synchronous result.
POLL_SECONDS = 2.0
MAX_WAIT_SECONDS = 120.0  # Then the statement is cancelled.
MAX_ATTEMPTS = 2  # First try plus one retry with the error message.
DIALECT = "databricks"

# Statement types that change data, schema, permissions or the session; anywhere in the tree.
FORBIDDEN_NODES = frozenset(
    {
        "Alter",
        "AlterColumn",
        "Analyze",
        "Cache",
        "Command",
        "Copy",
        "Create",
        "Delete",
        "Describe",
        "Drop",
        "Grant",
        "Insert",
        "LoadData",
        "Merge",
        "Pragma",
        "Refresh",
        "Revoke",
        "Set",
        "TruncateTable",
        "Uncache",
        "Update",
        "Use",
    }
)
# Functions that read outside the catalog, call out, or cost model tokens.
FORBIDDEN_FUNCTIONS = frozenset(
    {
        "http_request",
        "java_method",
        "read_files",
        "read_kafka",
        "read_kinesis",
        "read_pubsub",
        "read_pulsar",
        "read_statestore",
        "reflect",
        "secret",
        "try_reflect",
        "try_secret",
        "vector_search",
    }
)
_NUMERIC_TYPES = {"BYTE", "SHORT", "INT", "LONG", "FLOAT", "DOUBLE", "DECIMAL"}
_INTEGER_TYPES = {"BYTE", "SHORT", "INT", "LONG"}
_RUNNING_STATES = {"PENDING", "RUNNING"}

SQL_SYSTEM_PROMPT = """\
You write one Databricks SQL query that answers a question from the energy data catalog in the \
user message. Return exactly one JSON object and nothing else: {"sql": "<the query>"}

Rules:
1. Exactly one SELECT statement (a WITH clause is fine). No DDL, DML, SET, USE or comments.
2. Use only the tables of the catalog, always fully qualified as workspace.energy_prod.<table>, \
and only their columns.
3. Relative dates refer to the date given in the user message (Europe/Berlin): "yesterday" is \
that date minus one day, "the last 7 days" are the 7 days up to and including yesterday. Write \
dates as DATE'YYYY-MM-DD' literals.
4. Return only the columns the answer needs, with readable aliases, ordered by date where there \
is one; round averages to 2 decimals. At most 200 rows.
5. Prefer the daily tables for daily figures; use the hourly tables only for hours."""


class SqlError(Exception):
    """A query that could not be generated, validated or run; the message goes to the retry."""


class SqlValidationError(SqlError):
    pass


class SqlExecutionError(SqlError):
    pass


class SqlToolError(Exception):
    """The tool gave up after its retry; sql is the last query tried (None: none was generated)."""

    def __init__(self, message: str, sql: str | None = None, attempts: int = MAX_ATTEMPTS):
        super().__init__(message)
        self.sql = sql
        self.attempts = attempts


# ---------------------------------------------------------------- catalog


def load_catalog(path: Path | str = CATALOG_PATH) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def qualified(catalog: dict[str, Any], table: str) -> str:
    return f"{catalog['catalog']}.{catalog['schema']}.{table}"


def allowed_tables(catalog: dict[str, Any]) -> frozenset[str]:
    """Fully qualified, lower-case names of the catalog's tables."""
    return frozenset(qualified(catalog, t["name"]).lower() for t in catalog["tables"])


def format_catalog(catalog: dict[str, Any]) -> str:
    lines = [f"- {note}" for note in catalog.get("notes", [])]
    for table in catalog["tables"]:
        lines += ["", f"Table {qualified(catalog, table['name'])}: {table['description']}"]
        lines += [f"  {c['name']} {c['type']}: {c['meaning']}" for c in table["columns"]]
        lines += [f'  Example question: "{q}"' for q in table.get("example_questions", [])]
    return "\n".join(lines).strip()


# ---------------------------------------------------------------- generation


def build_sql_messages(
    question: str,
    catalog: dict[str, Any],
    today: dt.date,
    previous_sql: str | None = None,
    error: str | None = None,
) -> list[dict[str, str]]:
    yesterday = today - dt.timedelta(days=1)
    user = (
        f"Energy data catalog:\n{format_catalog(catalog)}\n\n"
        f"Today's date (Europe/Berlin): {today:%Y-%m-%d} ({today:%A}); "
        f"yesterday: {yesterday:%Y-%m-%d}.\n\nQuestion:\n{question}"
    )
    messages = [
        {"role": "system", "content": SQL_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    if error is not None:
        if previous_sql:
            messages.append({"role": "assistant", "content": f'{{"sql": "{previous_sql}"}}'})
        messages.append(
            {
                "role": "user",
                "content": f"That query failed: {error}\nFix it and return only the JSON object.",
            }
        )
    return messages


def parse_sql_reply(text: str) -> str:
    value = rag.parse_first_json(text)
    if value is None or not isinstance(value.get("sql"), str) or not value["sql"].strip():
        raise SqlError('the reply was not a JSON object {"sql": "<query>"}')
    return value["sql"].strip()


ChatFn = Callable[[list[dict[str, str]]], tuple[str, dict[str, int]]]


@mlflow.trace(span_type="LLM")
def generate_sql(
    question: str,
    catalog: dict[str, Any],
    today: dt.date | None = None,
    previous_sql: str | None = None,
    error: str | None = None,
    chat: ChatFn = rag.chat,
) -> str:
    """One SELECT for the question; previous_sql and error ask the model to fix a failed query."""
    messages = build_sql_messages(question, catalog, today or local_today(), previous_sql, error)
    text, _ = chat(messages)
    return parse_sql_reply(text)


def local_today() -> dt.date:
    """Today in Europe/Berlin, the time zone of the catalog's days (UTC without tz data)."""
    try:
        from zoneinfo import ZoneInfo

        return dt.datetime.now(ZoneInfo("Europe/Berlin")).date()
    except Exception:  # No IANA database (Windows without tzdata): UTC is at most 2 h off.
        return dt.datetime.now(dt.UTC).date()


# ---------------------------------------------------------------- validation


def _function_names(node: exp.Func) -> set[str]:
    names = {node.sql_name().lower()}
    if isinstance(node, exp.Anonymous):
        names.add(str(node.name).lower())
    return names


def validate_sql(sql: str, allowed: frozenset[str] | Sequence[str]) -> str:
    """The query in Databricks SQL, with LIMIT 200 if it had none (a larger limit is capped).

    Raises SqlValidationError unless the text is exactly one SELECT (a WITH clause is allowed)
    that reads only `allowed` tables, fully qualified, and has no DDL/DML or forbidden function.
    """
    allowed = frozenset(name.lower() for name in allowed)
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except sqlglot.errors.SqlglotError as error:
        raise SqlValidationError(f"the query is not valid SQL: {error}") from error
    if len(statements) != 1:
        raise SqlValidationError(f"expected exactly one statement, got {len(statements)}")
    tree = statements[0]
    for node in tree.walk():
        if type(node).__name__ in FORBIDDEN_NODES:
            raise SqlValidationError(f"{type(node).__name__.upper()} is not allowed; only SELECT")
    if not isinstance(tree, exp.Select):
        raise SqlValidationError(f"only a SELECT is allowed, got {type(tree).__name__.upper()}")
    for function in tree.find_all(exp.Func):
        forbidden = _function_names(function) & FORBIDDEN_FUNCTIONS
        if forbidden or any(name.startswith("ai_") for name in _function_names(function)):
            name = sorted(forbidden or _function_names(function))[0]
            raise SqlValidationError(f"function {name} is not allowed")
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if not table.db and not table.catalog and name in cte_names:
            continue
        full = ".".join(part for part in (table.catalog, table.db, table.name) if part).lower()
        if full not in allowed:
            raise SqlValidationError(
                f"table {full or table.sql(dialect=DIALECT)} is not allowed; use only "
                + ", ".join(sorted(allowed))
            )
    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(ROW_LIMIT)
    else:
        value = limit.expression if isinstance(limit, exp.Limit) else None
        if not (isinstance(value, exp.Literal) and value.is_int):
            raise SqlValidationError("LIMIT must be a number")
        if int(value.this) > ROW_LIMIT:
            tree = tree.limit(ROW_LIMIT)
    return tree.sql(dialect=DIALECT)


# ---------------------------------------------------------------- execution


def warehouse_id() -> str:
    return os.environ.get(WAREHOUSE_ENV) or SQL_WAREHOUSE_ID


def _convert(value: Any, type_name: str) -> Any:
    """JSON_ARRAY results are strings; numbers and booleans become Python values."""
    if value is None:
        return None
    if type_name in _INTEGER_TYPES:
        return int(value)
    if type_name in _NUMERIC_TYPES:
        return float(value)
    if type_name == "BOOLEAN":
        return str(value).lower() == "true"
    return value


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def parse_statement(response: Any, extra_rows: Sequence[Sequence[Any]] = ()) -> dict[str, Any]:
    columns = response.manifest.schema.columns if response.manifest else []
    names = [c.name for c in columns]
    types = [_enum_value(c.type_name).upper() for c in columns]
    raw = list((response.result.data_array if response.result else None) or []) + list(extra_rows)
    rows = [[_convert(v, t) for v, t in zip(row, types, strict=False)] for row in raw]
    return {"columns": names, "types": types, "rows": rows}


@mlflow.trace(span_type="TOOL")
def run_sql(sql: str, *, client: Any = None, warehouse: str | None = None) -> dict[str, Any]:
    """Run one statement: wait up to 30 s, then poll until it finishes (cancel after 120 s)."""
    from databricks.sdk.errors import DatabricksError
    from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout

    client = client or rag._workspace()
    api = client.statement_execution
    try:
        response = api.execute_statement(
            statement=sql,
            warehouse_id=warehouse or warehouse_id(),
            wait_timeout=WAIT_TIMEOUT,
            on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE,
        )
        deadline = time.monotonic() + MAX_WAIT_SECONDS
        while _enum_value(response.status.state) in _RUNNING_STATES:
            if time.monotonic() > deadline:
                api.cancel_execution(response.statement_id)
                raise SqlExecutionError(f"the query did not finish in {MAX_WAIT_SECONDS:.0f} s")
            time.sleep(POLL_SECONDS)
            response = api.get_statement(response.statement_id)
        state = _enum_value(response.status.state)
        if state != "SUCCEEDED":
            error = response.status.error
            message = getattr(error, "message", None) or "no error message"
            raise SqlExecutionError(f"the query {state.lower()}: {message}")
        extra: list[Sequence[Any]] = []
        chunk = response.result.next_chunk_index if response.result else None
        while chunk is not None:  # LIMIT 200 fits one chunk; more only for a reference query.
            part = api.get_statement_result_chunk_n(response.statement_id, chunk)
            extra.extend(part.data_array or [])
            chunk = part.next_chunk_index
    except DatabricksError as error:
        raise SqlExecutionError(f"the warehouse returned an error: {error}") from error
    return parse_statement(response, extra)


# ---------------------------------------------------------------- the tool

RunFn = Callable[[str], dict[str, Any]]
GenerateFn = Callable[..., str]


@mlflow.trace(span_type="TOOL")
def query(
    question: str,
    catalog: dict[str, Any] | None = None,
    today: dt.date | None = None,
    *,
    generate: GenerateFn = generate_sql,
    run: RunFn = run_sql,
) -> dict[str, Any]:
    """{"sql", "columns", "types", "rows", "attempts"}; SqlToolError after the one retry.

    A query that fails validation or execution goes back to the model with the error message
    once; the second failure ends the tool with a clear message.
    """
    catalog = catalog or load_catalog()
    allowed = allowed_tables(catalog)
    today = today or local_today()
    sql: str | None = None
    error: str | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            sql = generate(question, catalog, today=today, previous_sql=sql, error=error)
            sql = validate_sql(sql, allowed)
            return {"sql": sql, **run(sql), "attempts": attempt}
        except SqlError as failure:
            error = str(failure)
    raise SqlToolError(
        f"The energy data query failed after {MAX_ATTEMPTS} attempts: {error}", sql=sql
    )
