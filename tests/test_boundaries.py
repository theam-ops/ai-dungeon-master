"""The port boundary, enforced.

The point of `game/ports.py` is that `DND_MODE=prod` can swap SQLite and in-process
queues for Postgres and Redis without the game noticing. That only stays true while
nothing above the ports reaches past them - and a direct `store.party(cid)` slipped in
"just this once" would keep working in lite mode and silently read the wrong database
in prod. So the imports are checked here, from the syntax tree rather than by grep:
a comment mentioning `store` is fine, an import of it is not.
"""

import ast
import io
import os
import sys

import pytest

import server
from game.ports import Repository

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Below the ports. Only adapters may import these.
INFRASTRUCTURE = {"game.store", "game.adapters", "game.adapters.lite",
                  "sqlite3", "redis", "asyncpg", "psycopg", "libsql_client", "server"}


def source(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


def imports_of(rel):
    """Every module a file imports, resolved to dotted names."""
    package = os.path.dirname(rel).replace(os.sep, ".").replace("/", ".")
    found = set()
    for node in ast.walk(ast.parse(source(rel))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                parts = parts[:len(parts) - node.level + 1]
                base = ".".join(parts + ([base] if base else []))
            found.add(base)
            found |= {f"{base}.{a.name}" for a in node.names}
    return found


def py_files(folder):
    return sorted(os.path.join(folder, f) for f in os.listdir(os.path.join(ROOT, folder))
                  if f.endswith(".py"))


ABOVE_THE_PORTS = py_files("game/services") + [
    "game/dm.py", "game/rules.py", "game/lore.py", "game/i18n.py", "game/ports.py"]


@pytest.mark.parametrize("rel", ABOVE_THE_PORTS)
def test_nothing_above_the_ports_imports_infrastructure(rel):
    reached = imports_of(rel) & INFRASTRUCTURE
    assert not reached, f"{rel} reaches past the ports: {sorted(reached)}"


def test_the_server_reaches_storage_only_through_the_adapters():
    """server.py may build adapters; it may not open the store itself."""
    reached = imports_of("server.py") & {"game.store", "sqlite3"}
    assert not reached, f"server.py imports {sorted(reached)} directly"


@pytest.mark.parametrize("rel", py_files("game/services"))
def test_services_use_the_adapters_they_are_handed(rel):
    """A service that read the server's global `A` would work today and break the day
    a job runs on a worker process with adapters of its own."""
    names = {n.id for n in ast.walk(ast.parse(source(rel)))
             if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    assert "A" not in names


def repo_methods_called(rel):
    """Attribute names called on anything named `repo` or `....repo`."""
    called = set()
    for node in ast.walk(ast.parse(source(rel))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            target = node.func.value
            if (isinstance(target, ast.Name) and target.id == "repo") or \
               (isinstance(target, ast.Attribute) and target.attr == "repo"):
                called.add(node.func.attr)
    return called


@pytest.mark.parametrize("rel", ["server.py", "game/dm.py"] + py_files("game/services"))
def test_every_repository_call_is_part_of_the_port(rel):
    """A method the port does not declare is one a prod adapter is not obliged to
    have - it would work against SQLite and fail on the first real deployment."""
    unknown = repo_methods_called(rel) - Repository.__abstractmethods__
    assert not unknown, f"{rel} calls repo methods the port does not declare: {unknown}"


def test_every_job_that_is_enqueued_is_registered():
    """Jobs travel by name, so a typo is a background task that never runs."""
    enqueued = set()
    for rel in ["server.py"] + py_files("game/services"):
        for node in ast.walk(ast.parse(source(rel))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "enqueue" and node.args
                    and isinstance(node.args[0], ast.Constant)):
                enqueued.add(node.args[0].value)
    assert enqueued, "found no enqueue calls - the check itself is broken"
    registered = set(server.fresh_adapters("lite").queue._jobs)
    assert enqueued <= registered, f"enqueued but never registered: {enqueued - registered}"


def test_the_checks_would_notice_a_violation(tmp_path, monkeypatch):
    """Guard the guard: a file that imports the store must be caught."""
    bad = tmp_path / "game" / "services"
    bad.mkdir(parents=True)
    (bad / "sneaky.py").write_text("from .. import store\nfrom ..adapters import lite\n")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", str(tmp_path))
    assert {"game.store", "game.adapters.lite"} <= imports_of("game/services/sneaky.py")
