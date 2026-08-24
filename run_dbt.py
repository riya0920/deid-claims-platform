"""Build the de-identified extract, then run dbt over it.

THE ORDER IS THE ARGUMENT
-------------------------
`src/export.py` writes the de-identified extract; dbt reads that and nothing
else. There is no connection configured to anything holding PHI, so "the
warehouse never sees an identifier" is a property of the wiring rather than a
rule somebody has to remember -- and
`dbt/tests/no_phi_column_reaches_the_warehouse.sql` fails the build if a column
named like an identifier ever appears in any model.

Run:  python run_dbt.py              # export -> dbt build
      python run_dbt.py --no-export  # dbt only, reuse the existing extract
      python run_dbt.py docs
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

DBT_DIR = os.path.join(ROOT, "dbt")
EXTRACT = os.path.join(ROOT, "out", "extract")
DUCKDB = os.path.join(DBT_DIR, "claims.duckdb")
SALT = "demo-salt-not-a-secret"


def export(n_members=8000, seed=17):
    import export as E
    import synth
    data = synth.generate(n_members=n_members, seed=seed)
    paths = E.build_extract(data, SALT, EXTRACT)
    print("extract written:")
    for name, path in paths.items():
        print("   %-16s %10d bytes" % (name, os.path.getsize(path)))
    return data


def _env():
    os.environ["EXTRACT_DIR"] = EXTRACT.replace("\\", "/")
    os.environ["DUCKDB_PATH"] = DUCKDB.replace("\\", "/")
    os.environ["DBT_PROFILES_DIR"] = DBT_DIR


def run(argv=None):
    _env()
    from dbt.cli.main import dbtRunner
    argv = list(argv or []) + ["--project-dir", DBT_DIR,
                               "--profiles-dir", DBT_DIR]
    return 0 if dbtRunner().invoke(argv).success else 1


def main():
    argv = sys.argv[1:]
    if "docs" in argv:
        return run(["docs", "generate"])
    if "--no-export" not in argv:
        if not os.path.exists(EXTRACT):
            os.makedirs(EXTRACT, exist_ok=True)
        export()
    return run(["build"])


if __name__ == "__main__":
    raise SystemExit(main())
