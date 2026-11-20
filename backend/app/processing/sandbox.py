"""Python transform node: user code `def transform(df): ... return df` run in a restricted, separate process.

Layers (best effort — the node is meant for trusted data engineers, who are the only role able to edit pipelines):
1. Static check (AST): only `pandas`, `numpy`, `math`, `datetime`, `re`, `statistics`, `json`, `decimal`, `string` may be
   imported; no dunder / private names; no eval / exec / open / getattr…; no file or network helpers
   (`pd.read_*`, `df.to_csv`, `np.load`, `pd.io`…).
2. Separate process (`python -I`): restricted builtins, custom `__import__` with the same allow-list, CPU-time and
   memory limits where the OS supports them (Linux / Docker), and a wall-clock timeout.
3. Data goes in and out as Parquet files in a temporary folder that is deleted afterwards.

`ORBIT_PYTHON_TRANSFORM_ENABLED=false` disables the node entirely.
"""
from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from app.core.config import get_settings

ALLOWED_IMPORTS = {"pandas", "numpy", "math", "datetime", "re", "statistics", "json", "decimal", "string"}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "open", "getattr", "setattr", "delattr", "globals", "locals", "vars",
                   "input", "breakpoint", "help", "memoryview", "__import__", "exit", "quit", "dir", "type", "super",
                   "classmethod", "staticmethod", "property"}
ALLOWED_TO = {"to_datetime", "to_numeric", "to_timedelta", "to_list", "to_dict", "to_numpy", "to_period", "to_frame",
              "to_string", "to_records", "to_timestamp", "to_pydatetime", "to_series", "to_flat_index"}
FORBIDDEN_ATTRS = {"io", "api", "core", "compat", "testing", "plotting", "util", "lib", "ctypes", "ctypeslib", "load",
                   "save", "savez", "savetxt", "loadtxt", "genfromtxt", "fromfile", "tofile", "memmap", "DataSource",
                   "eval", "query", "system", "popen", "f2py", "distutils", "show_config", "os", "sys", "builtins"}
MAX_CODE = 20_000
_cache: OrderedDict[str, pa.Schema] = OrderedDict()  # validation results (0-row runs) by code + input schema


def check_code(code: str) -> None:
    if not code.strip():
        raise ValueError("Write a function: def transform(df): ... return df")
    if len(code) > MAX_CODE:
        raise ValueError(f"The code is longer than {MAX_CODE:,} characters")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"Python syntax error line {exc.lineno}: {exc.msg}") from exc
    if not any(isinstance(n, ast.FunctionDef) and n.name == "transform" for n in tree.body):
        raise ValueError("Define a function named transform(df) that returns a DataFrame")
    for n in ast.walk(tree):
        if isinstance(n, ast.Import | ast.ImportFrom):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""]
            bad = [m for m in mods if m.split(".")[0] not in ALLOWED_IMPORTS or "." in m]
            if bad:
                raise ValueError(f"Import of '{bad[0]}' is not allowed (allowed: {', '.join(sorted(ALLOWED_IMPORTS))})")
        if isinstance(n, ast.Name) and (n.id.startswith("_") or n.id in FORBIDDEN_CALLS):
            raise ValueError(f"'{n.id}' is not allowed in a Python transform (line {n.lineno})")
        if isinstance(n, ast.Attribute):
            a = n.attr
            if a.startswith("_") or a in FORBIDDEN_ATTRS or (a.startswith("to_") and a not in ALLOWED_TO) or a.startswith("read_"):
                raise ValueError(f"'.{a}' is not allowed: Python transforms cannot read or write files, the network or the system "
                                 f"(line {n.lineno})")
        if isinstance(n, ast.ClassDef | ast.Global | ast.Nonlocal | ast.AsyncFunctionDef | ast.Await):
            raise ValueError(f"{type(n).__name__} is not allowed in a Python transform (line {n.lineno})")


def run_transform(code: str, table: pa.Table) -> pa.Table:
    s = get_settings()
    if not s.python_transform_enabled:
        raise ValueError("Python transforms are disabled on this platform (ORBIT_PYTHON_TRANSFORM_ENABLED=false)")
    check_code(code)
    key = hashlib.sha256((code + str(table.schema)).encode()).hexdigest()
    if table.num_rows == 0 and key in _cache:
        return _cache[key].empty_table()
    with tempfile.TemporaryDirectory(prefix="orbit-py-") as tmp:
        src, dst, prog = Path(tmp) / "in.parquet", Path(tmp) / "out.parquet", Path(tmp) / "code.py"
        pq.write_table(table, src)
        prog.write_text(code, encoding="utf8")
        runner = Path(__file__).with_name("_sandbox_runner.py")
        try:
            proc = subprocess.run([sys.executable, "-I", str(runner), str(prog), str(src), str(dst), str(s.python_memory_mb)],
                                  capture_output=True, text=True, timeout=s.python_timeout_s, cwd=tmp)
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"The Python transform ran longer than {s.python_timeout_s} s and was stopped") from exc
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip().splitlines()
            raise ValueError("Python transform failed: " + (err[-1] if err else f"exit code {proc.returncode}"))
        out = pq.read_table(dst)
    if table.num_rows == 0:
        _cache[key] = out.schema
        while len(_cache) > 200:
            _cache.popitem(last=False)
    return out
