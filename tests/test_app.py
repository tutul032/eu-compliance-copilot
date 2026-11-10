"""The Databricks App's packaging (app/, resources/eucc_app.app.yml): mistakes here only show on
a deploy, as an app that does not start."""

import re
import tomllib
from pathlib import Path

import yaml

from eucc import ui

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "app"


def _bundle_file() -> dict:
    return yaml.safe_load((REPO / "resources" / "eucc_app.app.yml").read_text(encoding="utf-8"))


def _app() -> dict:
    return _bundle_file()["targets"]["dev"]["resources"]["apps"]["eucc_app"]


def _app_yaml() -> dict:
    return yaml.safe_load((APP / "app.yaml").read_text(encoding="utf-8"))


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _name(requirement: str) -> str:
    return requirement.split("==")[0].split(">")[0].split("<")[0].split("[")[0].strip()


def test_app_is_defined_for_dev_only():
    assert set(_bundle_file()) == {"targets"}
    assert set(_bundle_file()["targets"]) == {"dev"}
    app = _app()
    assert app["name"] == "eucc-copilot"
    assert app["source_code_path"] == "../app"


def test_app_resources_grant_the_endpoints_and_the_warehouse():
    resources = {r["name"]: r for r in _app()["resources"]}
    assert resources["llm-endpoint"]["serving_endpoint"] == {
        "name": "databricks-gpt-oss-120b",
        "permission": "CAN_QUERY",
    }
    assert resources["embedding-endpoint"]["serving_endpoint"] == {
        "name": "databricks-qwen3-embedding-0-6b",
        "permission": "CAN_QUERY",
    }
    assert resources["sql-warehouse"]["sql_warehouse"] == {
        "id": "${var.sql_warehouse_id}",
        "permission": "CAN_USE",
    }


def test_wheel_volume_is_an_app_resource():
    # The Apps docs: a wheel on a volume needs the volume as an app resource.
    resources = {r["name"]: r for r in _app()["resources"]}
    assert resources["artifacts-volume"]["uc_securable"] == {
        "securable_type": "VOLUME",
        "securable_full_name": "${var.catalog}.${var.schema}.artifacts",
        "permission": "READ_VOLUME",
    }


def test_app_yaml_runs_streamlit_and_reads_every_resource():
    config = _app_yaml()
    assert config["command"] == ["streamlit", "run", "app.py"]
    env = {e["name"]: e for e in config["env"]}
    assert {e["valueFrom"] for e in env.values() if "valueFrom" in e} == {
        "llm-endpoint",
        "embedding-endpoint",
        "sql-warehouse",
    }
    assert env["EUCC_SQL_WAREHOUSE_ID"]["valueFrom"] == "sql-warehouse"
    assert env["EUCC_TRACING"]["value"] == "0"


def test_app_yaml_config_matches_the_dev_target():
    values = {e["name"]: e.get("value") for e in _app_yaml()["env"]}
    env = {name: value for name, value in values.items() if value is not None}
    config = ui.AppConfig.from_env(env)
    assert (config.catalog, config.schema) == ("workspace", "compliance_dev")
    assert config.index == "workspace.compliance_dev.chunks_idx"
    assert config.tracing is False


def test_app_uses_uv_with_the_python_the_wheel_requires():
    # A requirements.txt would switch the app to pip and Python 3.11.
    assert not (APP / "requirements.txt").exists()
    assert (APP / "uv.lock").is_file()
    root = _toml(REPO / "pyproject.toml")["project"]
    assert _toml(APP / "pyproject.toml")["project"]["requires-python"] == root["requires-python"]


def test_app_dependencies_are_the_wheels_plus_streamlit_pinned_to_the_lock():
    root = _toml(REPO / "pyproject.toml")
    versions = {p["name"]: p.get("version") for p in _toml(REPO / "uv.lock")["package"]}
    app_dependencies = _toml(APP / "pyproject.toml")["project"]["dependencies"]
    pins = dict(d.split("==") for d in app_dependencies)
    assert len(pins) == len(app_dependencies)  # Every dependency is pinned.
    expected = {_name(d) for d in root["project"]["dependencies"]} | {"streamlit"}
    assert set(pins) == expected
    assert pins == {package: versions[package] for package in pins}


def test_app_lock_matches_the_app_dependencies():
    lock = {p["name"]: p.get("version") for p in _toml(APP / "uv.lock")["package"]}
    for requirement in _toml(APP / "pyproject.toml")["project"]["dependencies"]:
        package, version = requirement.split("==")
        assert lock[package] == version, package


def _wheel_requirements() -> list[str]:
    lines = (APP / "uv-requirements.txt").read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line.strip() and not line.startswith("#")]


def test_wheel_is_installed_from_the_artifacts_volume():
    root = _toml(REPO / "pyproject.toml")["project"]
    wheel = f"{root['name']}-{root['version']}-py3-none-any.whl"
    assert _wheel_requirements() == [f"/Volumes/workspace/compliance_dev/artifacts/{wheel}"]
    script = (REPO / "scripts" / "publish_app_wheel.ps1").read_text(encoding="utf-8")
    assert f'$Wheel = "{wheel}"' in script
    assert '"dbfs:/Volumes/workspace/compliance_dev/artifacts/$Wheel"' in script


def test_bundle_no_longer_builds_a_wheel_for_the_app():
    dev = _bundle_file()["targets"]["dev"]
    assert set(dev) == {"resources"}
    assert not (APP / "wheels").exists()


def test_terraform_grants_the_app_select_on_every_energy_table():
    catalog = yaml.safe_load(
        (REPO / "src" / "eucc" / "tools" / "energy_catalog.yaml").read_text(encoding="utf-8")
    )
    app_tf = (REPO / "infra" / "terraform" / "app.tf").read_text(encoding="utf-8")
    block = re.search(r"app_energy_tables = \[(.*?)\]", app_tf, re.DOTALL).group(1)
    assert re.findall(r'"(\w+)"', block) == [table["name"] for table in catalog["tables"]]
    assert f'energy_schema = "{catalog["schema"]}"' in app_tf
