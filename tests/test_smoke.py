"""Smoke test: package imports + schema.sql ships in the wheel/install."""

from __future__ import annotations

from importlib import resources


def test_package_imports() -> None:
    import haravan_elt

    assert haravan_elt.__version__


def test_base_extractor_is_abstract() -> None:
    from haravan_elt.extractors.base import BaseExtractor

    assert BaseExtractor.__abstractmethods__  # protects subclasses from skipping contract


def test_schema_sql_is_packaged() -> None:
    sql = resources.files("haravan_elt").joinpath("meta/schema.sql").read_text(encoding="utf-8")
    assert "CREATE SCHEMA IF NOT EXISTS raw" in sql
    assert "CREATE TABLE IF NOT EXISTS meta.sync_state" in sql
    assert "CREATE TABLE IF NOT EXISTS meta.run_log" in sql
