#!/usr/bin/env python3
"""Read a user-selected aggregate pair Parquet and create an external lookup index.

No simulation, source modification, row filtering, geometry or conductance model.
Runs in the explicitly selected data-reader Python, not the embedded GUI runtime.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

COLUMNS = ("preId", "preType", "preInstance", "preNt", "postId", "postType", "postInstance", "postNt", "total_weight")


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_index(source: Path, destination: Path, *, temp_directory: Path | None = None) -> dict:
    try:
        import duckdb
    except ImportError as exc:
        raise ValueError("The selected data-reader Python needs duckdb. Choose an existing Python containing duckdb; no simulator installation is changed.") from exc
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not source.is_file() or source.suffix.casefold() != ".parquet":
        raise ValueError("Choose a regular Parquet file")
    if source.is_relative_to(destination):
        raise ValueError("The index folder must not contain or replace the source")
    if shutil.disk_usage(destination.parent).free < max(128 * 1024**2, source.stat().st_size * 20):
        raise ValueError("Insufficient free disk for a separate lookup index; source will not be modified")
    before = sha(source)
    data = duckdb.connect(database=":memory:")
    try:
        data.execute("SET threads=1")
        data.execute("SET memory_limit='512MB'")
        if temp_directory is not None:
            # Large official graphs spill only inside their new generation,
            # never into the launch folder or the installed application.
            data.execute('SET temp_directory=?', [str(temp_directory)])
        schema = data.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(source)]).fetchall()
        types = {row[0]: row[1] for row in schema}
        if not set(COLUMNS).issubset(types):
            raise ValueError("Expected aggregate pair columns: " + ", ".join(COLUMNS))
        if any(types[column] not in {"BIGINT", "INTEGER", "SMALLINT", "TINYINT"} for column in ("preId", "postId", "total_weight")):
            raise ValueError("Neuron IDs and total_weight must be signed integer columns; IDs are never cast through floating point")
        if any(types[column] != "VARCHAR" for column in COLUMNS if column not in {"preId", "postId", "total_weight"}):
            raise ValueError("Pair annotation columns must be strings")
        values = data.execute("SELECT COUNT(*), COUNT(DISTINCT (preId, postId)), COUNT(*) FILTER (WHERE preId IS NULL OR postId IS NULL OR total_weight IS NULL OR total_weight < 0), MIN(total_weight), MAX(total_weight), SUM(total_weight), COUNT(*) FILTER (WHERE preId=postId) FROM read_parquet(?)", [str(source)]).fetchone()
        rows, unique, invalid, minimum, maximum, weight_sum, self_pairs = values
        if rows == 0 or invalid or unique != rows:
            raise ValueError("Pair file must be nonempty with unique directed pairs, non-null IDs/weights and nonnegative weights; no rows are silently dropped or aggregated")
        neurons = data.execute("SELECT COUNT(*) FROM (SELECT preId AS id FROM read_parquet(?) UNION SELECT postId AS id FROM read_parquet(?))", [str(source), str(source)]).fetchone()[0]
        destination.mkdir(parents=False, exist_ok=False)
        index = destination / "pairs.sqlite"
        # The SQLite transaction context commits but does not close its handle.
        # Close explicitly so Windows can remove an uncommitted cancelled index.
        with closing(sqlite3.connect(index)) as db, db:
            db.execute("CREATE TABLE pairs (preId INTEGER NOT NULL, preType TEXT, preInstance TEXT, preNt TEXT, postId INTEGER NOT NULL, postType TEXT, postInstance TEXT, postNt TEXT, total_weight INTEGER NOT NULL)")
            cursor = data.execute("SELECT " + ",".join(COLUMNS) + " FROM read_parquet(?)", [str(source)])
            while batch := cursor.fetchmany(4096):
                db.executemany("INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?)", batch)
            db.execute("CREATE UNIQUE INDEX directed_pair ON pairs(preId,postId)")
            observed = db.execute("SELECT COUNT(*),SUM(total_weight) FROM pairs").fetchone()
            if observed != (rows, int(weight_sum)):
                raise ValueError("Index count/weight census differs from the supplied Parquet")
        if sha(source) != before:
            raise ValueError("Source file changed during indexing; registration refused")
        result = {"schema": "digifly-weighted-pair-graph-v1", "source_path": str(source), "source_sha256": before,
            "index": index.name, "index_sha256": sha(index), "rows": rows, "represented_neurons": neurons,
            "min_weight": minimum, "max_weight": maximum, "total_weight": int(weight_sum), "self_pairs": self_pairs,
            "columns": list(COLUMNS), "dataset": "user-supplied MANC pair graph; release not declared in file",
            "weight_units": "source aggregate total_weight; not conductance", "filtering": "none; all supplied rows preserved",
            "morphologies_in_file": False, "contact_coordinates_in_file": False, "simulation_policy": "not_selected_by_import"}
        with (destination / "manifest.json").open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write("\n")
        return result
    finally:
        data.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(build_index(args.source, args.destination), allow_nan=False))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
