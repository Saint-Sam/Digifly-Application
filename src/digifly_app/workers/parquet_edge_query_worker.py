#!/usr/bin/env python3
"""Stream a selected Male-CNS chemical subgraph from an external Parquet file.

This helper runs inside the user's configured scientific Python.  Digifly does
not import or bundle the multi-gigabyte contact table; only selected rows are
returned to the app for a run-owned edge manifest.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--neuron-id", action="append", dest="neuron_ids", required=True)
    args = parser.parse_args()
    source = Path(args.source).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Male-CNS contact table is missing: {source}")
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "The configured scientific Python needs the 'duckdb' package to query "
            "Male-CNS contacts without importing the 7.8 GB dataset into Digifly."
        ) from exc

    selected = tuple(dict.fromkeys(str(value).strip() for value in args.neuron_ids))
    placeholders = ",".join("?" for _value in selected)
    query = (
        "SELECT id AS source_edge_id, CAST(pre AS VARCHAR) AS pre_id, "
        "CAST(post AS VARCHAR) AS post_id, x, y, z, confidence, syn_top_nt, "
        "syn_top_p, acetylcholine, gaba, glutamate, dopamine, serotonin, "
        "octopamine, histamine FROM read_parquet(?) "
        f"WHERE CAST(pre AS VARCHAR) IN ({placeholders}) "
        f"AND CAST(post AS VARCHAR) IN ({placeholders}) "
        "ORDER BY pre_id, post_id, x, y, z, source_edge_id"
    )
    connection = duckdb.connect(database=":memory:")
    try:
        cursor = connection.execute(query, (str(source), *selected, *selected))
        columns = tuple(item[0] for item in cursor.description)
        serial = 0
        while True:
            batch = cursor.fetchmany(4096)
            if not batch:
                break
            for values in batch:
                serial += 1
                row = dict(zip(columns, values))
                payload = {
                    "source_edge_rowid": serial,
                    "source_edge_id": (
                        format(float(row["source_edge_id"]), ".17g")
                        if row["source_edge_id"] is not None
                        else ""
                    ),
                    "pre_id": str(row["pre_id"]),
                    "post_id": str(row["post_id"]),
                    "weight_uS": None,
                    "delay_ms": None,
                    "tau1_ms": None,
                    "tau2_ms": None,
                    "syn_e_rev_mV": None,
                    "pre_x": None,
                    "pre_y": None,
                    "pre_z": None,
                    "post_x": float(row["x"]) * 0.001,
                    "post_y": float(row["y"]) * 0.001,
                    "post_z": float(row["z"]) * 0.001,
                    "confidence": row["confidence"],
                    "neurotransmitter": row["syn_top_nt"],
                    "neurotransmitter_confidence": row["syn_top_p"],
                    "neurotransmitter_probabilities": {
                        key: row[key]
                        for key in (
                            "acetylcholine",
                            "gaba",
                            "glutamate",
                            "dopamine",
                            "serotonin",
                            "octopamine",
                            "histamine",
                        )
                    },
                }
                print(json.dumps(payload, separators=(",", ":")), flush=False)
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
