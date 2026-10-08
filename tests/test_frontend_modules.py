"""The front end's ES modules fit together.

There is no build step, so nothing checks the browser code before a browser runs it -
and a module split goes wrong in ways that only show up on a click: a function moved to
another file without its callers importing it, an import of something nobody exports, an
assignment to an imported binding (they are read-only), an accidental global (modules
are strict). These read every module and check each of those.

`node --check` confirms each one parses; it is skipped where Node is not installed,
which is fine - players never need Node; it is a development tool here.
"""

import os
import re
import shutil
import subprocess

import pytest

from . import jsmodules as J

STATIC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
JS = os.path.join(STATIC, "js")
BROWSER = {"window", "document", "location", "localStorage", "sessionStorage", "navigator",
           "console", "history", "fetch", "setTimeout", "clearTimeout", "setInterval",
           "clearInterval", "requestAnimationFrame", "matchMedia", "EventSource", "FormData",
           "Blob", "URL", "File", "FileReader", "Image", "TextEncoder", "TextDecoder",
           "crypto", "confirm", "alert", "prompt", "getComputedStyle", "structuredClone",
           "Math", "JSON", "Object", "Array", "String", "Number", "Boolean", "Date", "Promise",
           "Error", "Set", "Map", "RegExp", "Symbol", "parseInt", "parseFloat", "isNaN",
           "encodeURIComponent", "decodeURIComponent", "Intl", "Uint8Array", "DataView",
           "ArrayBuffer", "DOMParser", "AbortController", "Event", "CustomEvent", "Node",
           "HTMLElement", "atob", "btoa", "queueMicrotask", "performance", "screen",
           "devicePixelRatio", "Response", "Request", "Headers", "WeakMap", "WeakSet",
           "Infinity", "NaN", "globalThis", "TypeError", "RangeError", "BigInt"}


def modules():
    out = {}
    for base, _, files in os.walk(JS):
        for f in files:
            if f.endswith(".js"):
                path = os.path.join(base, f)
                rel = os.path.relpath(path, JS).replace(os.sep, "/")
                out[rel] = open(path, encoding="utf-8").read()
    return out


MODULES = modules()


def resolve(frm, spec):
    return os.path.normpath(os.path.join(os.path.dirname(frm), spec)).replace(os.sep, "/")


def test_there_are_modules_and_no_classic_scripts_left():
    assert "main.js" in MODULES and len(MODULES) > 10
    assert not os.path.exists(os.path.join(STATIC, "app.js"))
    assert not os.path.exists(os.path.join(STATIC, "i18n.js"))


def test_the_page_loads_only_the_entry_module():
    html = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()
    scripts = re.findall(r"<script[^>]*>", html)
    assert scripts == ['<script type="module" src="/js/main.js">']


@pytest.mark.parametrize("rel", sorted(MODULES))
def test_every_import_resolves_to_an_export(rel):
    src = MODULES[rel]
    for spec, names in J.imports(src).items():
        target = resolve(rel, spec)
        assert target in MODULES, f"{rel} imports {spec}, which does not exist"
        exported = set(J.exports(MODULES[target]))
        if "export default" in MODULES[target]:
            exported.add("default")
        missing = [n for n in names if n not in exported]
        assert not missing, f"{rel} imports {missing} from {spec}, which does not export them"
    for spec in J.side_imports(src):
        assert resolve(rel, spec) in MODULES, f"{rel} imports {spec}, which does not exist"


def defined_elsewhere(rel):
    out = {}
    for other, src in MODULES.items():
        if other != rel:
            for name in J.top_level_names(src):
                out[name] = other
    return out


@pytest.mark.parametrize("rel", sorted(MODULES))
def test_nothing_from_another_module_is_used_without_importing_it(rel):
    """The classic split bug: the function moved, a caller did not import it, and the
    ReferenceError waits for whoever clicks the button that calls it."""
    src = MODULES[rel]
    own = set(J.top_level_names(src)) | set(J.imported_as(src)) | J.declared_names(src)
    missing = sorted(n for n, where in defined_elsewhere(rel).items()
                     if n in J.used_names(src) and n not in own)
    assert not missing, f"{rel} uses {missing} without importing them"


@pytest.mark.parametrize("rel", sorted(MODULES))
def test_no_module_assigns_to_an_import(rel):
    src = MODULES[rel]
    clash = sorted(J.assigned_names(src) & set(J.imported_as(src)))
    assert not clash, f"{rel} assigns to imported {clash} - imports are read-only; add a setter"


@pytest.mark.parametrize("rel", sorted(MODULES))
def test_no_accidental_globals(rel):
    """A module is strict: assigning a name declared nowhere throws instead of quietly
    creating a global."""
    src = MODULES[rel]
    stray = sorted(J.assigned_names(src) - J.declared_names(src) - set(J.imported_as(src))
                   - BROWSER)
    assert not stray, f"{rel} assigns to {stray}, declared nowhere"


def test_no_name_is_defined_in_two_modules():
    seen = {}
    for rel, src in MODULES.items():
        for name in J.top_level_names(src):
            assert name not in seen, f"{name} is defined in both {seen[name]} and {rel}"
            seen[name] = rel


@pytest.mark.skipif(not shutil.which("node"), reason="Node is not installed")
@pytest.mark.parametrize("rel", sorted(MODULES))
def test_every_module_parses(rel):
    r = subprocess.run(["node", "--input-type=module", "--check"],
                       input=MODULES[rel], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, f"{rel}: {r.stderr.strip()[:300]}"
