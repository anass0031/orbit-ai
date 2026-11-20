"""Runs one Python transform in its own process (started with `python -I`). Not imported by the application.

argv: code.py in.parquet out.parquet memory_mb
"""
import builtins
import sys

ALLOWED = {"pandas", "numpy", "math", "datetime", "re", "statistics", "json", "decimal", "string"}


def _limits(memory_mb: int) -> None:
    try:
        import resource  # Linux / macOS only
    except ImportError:
        return
    resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024, memory_mb * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))


def main() -> None:
    code_path, src, dst, memory_mb = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    import numpy as np
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    df = pq.read_table(src).to_pandas()
    code = open(code_path, encoding="utf8").read()
    _limits(memory_mb)
    real_import = builtins.__import__

    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
        if name.split(".")[0] not in ALLOWED or level:
            raise ImportError(f"import of {name} is not allowed")
        return real_import(name, globals, locals, fromlist, level)

    safe = {k: getattr(builtins, k) for k in (
        "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float", "int", "isinstance", "len", "list",
        "map", "max", "min", "range", "reversed", "round", "set", "sorted", "str", "sum", "tuple", "zip", "frozenset",
        "Exception", "ValueError", "KeyError", "TypeError", "ZeroDivisionError", "IndexError", "ArithmeticError",
        "True", "False", "None", "slice", "iter", "next", "hash", "format", "repr", "chr", "ord", "pow", "print")
        if hasattr(builtins, k)}
    safe["__import__"] = safe_import
    env = {"__builtins__": safe, "pd": pd, "np": np, "__name__": "orbit_transform"}
    exec(compile(code, "transform.py", "exec"), env)  # noqa: S102 - restricted globals, checked by sandbox.check_code
    out = env["transform"](df.copy())
    if not isinstance(out, pd.DataFrame):
        raise TypeError(f"transform(df) must return a pandas DataFrame, got {type(out).__name__}")
    out.columns = [str(c) for c in out.columns]
    pq.write_table(pa.Table.from_pandas(out.reset_index(drop=True), preserve_index=False), dst)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
