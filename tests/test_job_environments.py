"""Packaging and job definitions: mistakes here only show once a job runs on Databricks."""

import tomllib
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
ENTRY_POINTS = {
    "build_corpus_job": "eucc.jobs.build_corpus:main",
    "index_job": "eucc.jobs.index:main",
    "eval_retrieval_job": "eucc.jobs.eval_retrieval:main",
    "eval_rag_job": "eucc.jobs.eval_rag:main",
    "eval_agent_job": "eucc.jobs.eval_agent:main",
}
JOB_FILES = {
    "build_corpus": "build_corpus.job.yml",
    "eval_retrieval": "eval_retrieval.job.yml",
    "eval_rag": "eval_rag.job.yml",
    "eval_agent": "eval_agent.job.yml",
}


def _pyproject() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))


def _lock_versions() -> dict[str, str]:
    lock = tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))
    return {package["name"]: package.get("version") for package in lock["package"]}


def _job(name: str) -> dict:
    text = (REPO / "resources" / JOB_FILES[name]).read_text(encoding="utf-8")
    return yaml.safe_load(text)["resources"]["jobs"][name]


def _wheel_tasks() -> list[dict]:
    return [
        task["python_wheel_task"]
        for name in JOB_FILES
        for task in _job(name)["tasks"]
        if "python_wheel_task" in task
    ]


def test_entry_points_name_the_job_modules():
    assert _pyproject()["project"]["scripts"] == ENTRY_POINTS


def test_every_wheel_task_calls_a_declared_entry_point():
    tasks = _wheel_tasks()
    assert sorted(t["entry_point"] for t in tasks) == sorted(ENTRY_POINTS)
    for task in tasks:
        assert task["package_name"] == _pyproject()["project"]["name"]
        params = task["parameters"]
        assert params[:4] == ["--catalog", "${var.catalog}", "--schema", "${var.schema}"]


@pytest.mark.parametrize(
    ("name", "variable"),
    [
        ("eval_retrieval", "retrieval_experiment"),
        ("eval_rag", "rag_experiment"),
        ("eval_agent", "rag_experiment"),
    ],
)
def test_eval_logs_to_the_experiment_variable(name, variable):
    params = _job(name)["tasks"][0]["python_wheel_task"]["parameters"]
    assert params[params.index("--experiment") + 1] == f"${{var.{variable}}}"


def test_agent_eval_queries_the_warehouse_variable():
    params = _job("eval_agent")["tasks"][0]["python_wheel_task"]["parameters"]
    assert params[params.index("--warehouse-id") + 1] == "${var.sql_warehouse_id}"


def test_energy_catalog_ships_in_the_package():
    assert (REPO / "src" / "eucc" / "tools" / "energy_catalog.yaml").is_file()


def test_index_runs_after_the_build():
    tasks = {task["task_key"]: task for task in _job("build_corpus")["tasks"]}
    assert tasks["index"]["depends_on"] == [{"task_key": "build_corpus"}]


@pytest.mark.parametrize(
    ("source", "packaged"),
    [
        ("corpus/manifest.yaml", "eucc/corpus/manifest.yaml"),
        ("eval/questions.yaml", "eucc/eval/questions.yaml"),
    ],
)
def test_data_files_ship_in_the_wheel_where_the_loaders_look(source, packaged):
    include = _pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert include[source] == packaged
    assert (REPO / source).is_file()


@pytest.mark.parametrize("name", list(JOB_FILES))
def test_environment_pins_equal_the_lock_file(name):
    dependencies = _job(name)["environments"][0]["spec"]["dependencies"]
    pins = dict(d.split("==") for d in dependencies if "==" in d)
    versions = _lock_versions()
    assert pins == {package: versions[package] for package in pins}
    assert "../dist/*.whl" in dependencies


@pytest.mark.parametrize(
    ("name", "packages"),
    [
        ("build_corpus", {"beautifulsoup4", "lxml", "databricks-vectorsearch"}),
        ("eval_retrieval", {"databricks-vectorsearch"}),
        ("eval_rag", {"mlflow-skinny", "openai", "httpx"}),
        ("eval_agent", {"mlflow-skinny", "openai", "httpx", "langgraph", "sqlglot"}),
    ],
)
def test_environment_has_what_its_tasks_import(name, packages):
    dependencies = _job(name)["environments"][0]["spec"]["dependencies"]
    assert packages <= {d.split("==")[0] for d in dependencies}


@pytest.mark.parametrize("name", list(JOB_FILES))
def test_environment_runs_the_python_the_wheel_requires(name):
    # Version 2 is Python 3.11; 3 and later are 3.12, what requires-python asks for.
    assert _pyproject()["project"]["requires-python"] == ">=3.12"
    assert int(_job(name)["environments"][0]["spec"]["environment_version"]) >= 3
