"""The SQL tool without the network: validator, catalog, prompt, retry, statement polling."""

import datetime as dt
from types import SimpleNamespace

import pytest

from eucc.tools import sql
from eucc.tools.sql import SqlExecutionError, SqlToolError, SqlValidationError, validate_sql

CATALOG = sql.load_catalog()
ALLOWED = sql.allowed_tables(CATALOG)
DAILY = "workspace.energy_prod.gold_energy_daily"


def test_catalog_has_the_seven_gold_tables_with_columns_and_examples():
    names = {t["name"] for t in CATALOG["tables"]}
    assert names == {
        "gold_energy_hourly",
        "gold_energy_daily",
        "gold_forecast_accuracy",
        "gold_forecast_hourly",
        "gold_dq_daily_summary",
        "gold_dq_anomalies",
        "gold_dq_daily_summary_text",
    }
    for table in CATALOG["tables"]:
        assert table["columns"] and all(
            {"name", "type", "meaning"} <= set(c) for c in table["columns"]
        )
        assert 2 <= len(table["example_questions"]) <= 3
    assert f"{DAILY}" in ALLOWED


# ---------------------------------------------------------------- validator


def test_select_on_a_catalog_table_is_allowed_and_keeps_its_limit():
    out = validate_sql(f"SELECT date_local, price_avg_eur_mwh FROM {DAILY} LIMIT 5", ALLOWED)
    assert out == f"SELECT date_local, price_avg_eur_mwh FROM {DAILY} LIMIT 5"


def test_missing_limit_gets_limit_200():
    out = validate_sql(f"SELECT AVG(price_avg_eur_mwh) FROM {DAILY}", ALLOWED)
    assert out.endswith("LIMIT 200")


def test_larger_limit_is_capped():
    assert validate_sql(f"SELECT * FROM {DAILY} LIMIT 5000", ALLOWED).endswith("LIMIT 200")


def test_trailing_semicolon_is_one_statement():
    assert validate_sql(f"SELECT 1 AS x FROM {DAILY};", ALLOWED).endswith("LIMIT 200")


def test_cte_over_catalog_tables_is_allowed():
    query = (
        "WITH recent AS (SELECT issue_date, mape FROM workspace.energy_prod.gold_forecast_accuracy "
        "WHERE target = 'total_load_mwh') SELECT AVG(mape) AS avg_mape FROM recent"
    )
    out = validate_sql(query, ALLOWED)
    assert out.startswith("WITH recent AS") and out.endswith("LIMIT 200")


@pytest.mark.parametrize(
    "query",
    [
        f"DROP TABLE {DAILY}",
        f"DELETE FROM {DAILY} WHERE 1 = 1",
        f"INSERT INTO {DAILY} SELECT * FROM {DAILY}",
        f"UPDATE {DAILY} SET price_avg_eur_mwh = 0",
        f"CREATE TABLE workspace.energy_prod.x AS SELECT * FROM {DAILY}",
    ],
)
def test_ddl_and_dml_are_rejected(query):
    with pytest.raises(SqlValidationError, match="not allowed|only a SELECT"):
        validate_sql(query, ALLOWED)


def test_two_statements_are_rejected():
    with pytest.raises(SqlValidationError, match="exactly one statement, got 2"):
        validate_sql(f"SELECT * FROM {DAILY}; DROP TABLE {DAILY}", ALLOWED)


@pytest.mark.parametrize(
    "table",
    [
        "workspace.compliance_dev.chunks",  # Another schema.
        "workspace.energy_prod.silver_smard",  # Not in the catalog.
        "gold_energy_daily",  # Not fully qualified.
        "system.access.audit",
    ],
)
def test_tables_outside_the_whitelist_are_rejected(table):
    with pytest.raises(SqlValidationError, match="is not allowed"):
        validate_sql(f"SELECT * FROM {table}", ALLOWED)


def test_a_join_to_a_non_whitelisted_table_is_rejected():
    query = f"SELECT d.* FROM {DAILY} d JOIN workspace.default.secrets s ON d.date_local = s.d"
    with pytest.raises(SqlValidationError, match="workspace.default.secrets"):
        validate_sql(query, ALLOWED)


@pytest.mark.parametrize(
    "call", ["ai_query('databricks-gpt-oss-120b', 'hi')", "http_request('GET', 'x')"]
)
def test_side_effecting_functions_are_rejected(call):
    with pytest.raises(SqlValidationError, match="function"):
        validate_sql(f"SELECT {call} AS x FROM {DAILY}", ALLOWED)


def test_unparseable_sql_is_a_validation_error():
    with pytest.raises(SqlValidationError):
        validate_sql("SELECT FROM WHERE (", ALLOWED)


# ---------------------------------------------------------------- generation and retry


def test_prompt_gives_the_date_and_the_catalog():
    messages = sql.build_sql_messages("Price yesterday?", CATALOG, dt.date(2026, 11, 9))
    user = messages[1]["content"]
    assert "2026-11-09 (Monday); yesterday: 2026-11-08" in user
    assert f"Table {DAILY}:" in user and "price_avg_eur_mwh double" in user


def test_generate_sql_parses_the_json_reply():
    def chat(messages):
        return '{"sql": "SELECT 1"}', {}

    assert sql.generate_sql("q", CATALOG, dt.date(2026, 11, 9), chat=chat) == "SELECT 1"


def test_query_retries_once_with_the_validation_error():
    calls = []

    def generate(question, catalog, today, previous_sql, error):
        calls.append((previous_sql, error))
        return "DROP TABLE x" if len(calls) == 1 else f"SELECT 1 AS one FROM {DAILY}"

    result = sql.query(
        "q",
        CATALOG,
        dt.date(2026, 11, 9),
        generate=generate,
        run=lambda s: {"columns": ["one"], "types": ["INT"], "rows": [[1]]},
    )
    assert result["attempts"] == 2 and result["rows"] == [[1]]
    assert calls[0] == (None, None)
    assert calls[1][0] == "DROP TABLE x" and "DROP" in calls[1][1]


def test_query_gives_up_after_the_retry_with_a_clear_error():
    def run(query):
        raise SqlExecutionError("the query failed: [UNRESOLVED_COLUMN] price")

    with pytest.raises(SqlToolError, match="failed after 2 attempts.*UNRESOLVED_COLUMN") as info:
        sql.query(
            "q",
            CATALOG,
            dt.date(2026, 11, 9),
            generate=lambda *a, **k: f"SELECT price FROM {DAILY}",
            run=run,
        )
    assert info.value.sql == f"SELECT price FROM {DAILY} LIMIT 200"


# ---------------------------------------------------------------- execution


def _response(state, rows=None, error=None):
    columns = [
        SimpleNamespace(name="date_local", type_name=SimpleNamespace(value="DATE")),
        SimpleNamespace(name="price", type_name=SimpleNamespace(value="DOUBLE")),
        SimpleNamespace(name="n", type_name=SimpleNamespace(value="LONG")),
    ]
    return SimpleNamespace(
        statement_id="s1",
        status=SimpleNamespace(state=SimpleNamespace(value=state), error=error),
        manifest=SimpleNamespace(schema=SimpleNamespace(columns=columns)),
        result=SimpleNamespace(data_array=rows, next_chunk_index=None) if rows else None,
    )


class _Api:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def execute_statement(self, **kwargs):
        self.calls.append(("execute", kwargs))
        return self.responses.pop(0)

    def get_statement(self, statement_id):
        self.calls.append(("get", statement_id))
        return self.responses.pop(0)


def test_run_sql_polls_until_done_and_converts_types(monkeypatch):
    monkeypatch.setattr(sql, "POLL_SECONDS", 0)
    api = _Api(
        [
            _response("PENDING"),
            _response("RUNNING"),
            _response(
                "SUCCEEDED", rows=[["2026-11-08", "242.13", "24"], ["2026-11-09", None, "0"]]
            ),
        ]
    )
    result = sql.run_sql("SELECT 1", client=SimpleNamespace(statement_execution=api), warehouse="w")
    assert result == {
        "columns": ["date_local", "price", "n"],
        "types": ["DATE", "DOUBLE", "LONG"],
        "rows": [["2026-11-08", 242.13, 24], ["2026-11-09", None, 0]],
    }
    execute = api.calls[0][1]
    assert execute["warehouse_id"] == "w" and execute["wait_timeout"] == "30s"
    assert [c[0] for c in api.calls] == ["execute", "get", "get"]


def test_run_sql_failure_is_an_execution_error():
    error = SimpleNamespace(message="Table or view not found")
    api = _Api([_response("FAILED", error=error)])
    with pytest.raises(SqlExecutionError, match="failed: Table or view not found"):
        sql.run_sql("SELECT 1", client=SimpleNamespace(statement_execution=api), warehouse="w")


def test_warehouse_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv(sql.WAREHOUSE_ENV, raising=False)
    assert sql.warehouse_id() == "4228ff3a910b0b5e"
    monkeypatch.setenv(sql.WAREHOUSE_ENV, "abc")
    assert sql.warehouse_id() == "abc"


def test_catalog_tells_error_questions_to_select_mape_mae_and_baseline_mape():
    accuracy = next(t for t in CATALOG["tables"] if t["name"] == "gold_forecast_accuracy")
    rule = "select mape, mae and baseline_mape"
    assert rule in accuracy["description"]
    assert any(rule in note for note in CATALOG["notes"])
    assert rule in sql.format_catalog(CATALOG)  # So the model sees it.
