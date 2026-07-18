"""Тести гігієни міграцій.

БД тут не потрібна — ці перевірки ловлять клас помилок, який інакше
виявляється лише в проді: неправильно названий файл (і рушій застосує
міграції не в тому порядку), порожній файл, або CREATE TABLE без
IF NOT EXISTS (і накат на живу базу, де таблиця вже є, впаде).
"""
import os
import re
import glob

import pytest

MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")

NAME_RE = re.compile(r"^\d{4}_[a-z0-9_]+\.sql$")


def _files():
    return sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.sql")))


def test_migrations_dir_exists():
    assert os.path.isdir(MIGRATIONS_DIR), "немає папки migrations/"
    assert _files(), "у migrations/ немає жодного .sql"


@pytest.mark.parametrize("path", _files(), ids=os.path.basename)
def test_filename_convention(path):
    """0001_baseline.sql — так, 0001-baseline.SQL або v2.sql — ні.
    Порядок накату визначається саме іменем файлу."""
    name = os.path.basename(path)
    assert NAME_RE.match(name), f"погане імʼя міграції: {name} (треба NNNN_назва.sql)"


def test_numbers_are_unique_and_sequential():
    """Два файли з номером 0003 — це гонка в мерджі двох гілок: обидва
    накотяться, але порядок буде випадковим. Дірка в нумерації — ознака
    видаленої міграції, яка в проді вже накочена."""
    nums = [int(os.path.basename(p)[:4]) for p in _files()]
    assert len(nums) == len(set(nums)), f"дублікати номерів міграцій: {nums}"
    assert nums == list(range(1, len(nums) + 1)), f"розрив у нумерації: {nums}"


@pytest.mark.parametrize("path", _files(), ids=os.path.basename)
def test_not_empty(path):
    body = "\n".join(l for l in open(path, encoding="utf-8")
                     if l.strip() and not l.strip().startswith("--"))
    assert body.strip(), f"{os.path.basename(path)}: немає жодної SQL-команди"


@pytest.mark.parametrize("path", _files(), ids=os.path.basename)
def test_create_statements_are_idempotent(path):
    """У проді схема вже стоїть (її накотили руками зі schema.sql), тому
    міграції мусять переживати повторний накат на непорожню базу."""
    sql = open(path, encoding="utf-8").read()
    sql = re.sub(r"--[^\n]*", "", sql)          # без коментарів
    for stmt in ("CREATE TABLE", "CREATE INDEX", "CREATE UNIQUE INDEX"):
        for m in re.finditer(stmt + r"\s+(\w+)", sql, re.IGNORECASE):
            assert m.group(1).upper() == "IF", (
                f"{os.path.basename(path)}: «{stmt}» без IF NOT EXISTS")
    for m in re.finditer(r"ADD\s+COLUMN\s+(\w+)", sql, re.IGNORECASE):
        assert m.group(1).upper() == "IF", (
            f"{os.path.basename(path)}: «ADD COLUMN» без IF NOT EXISTS")
