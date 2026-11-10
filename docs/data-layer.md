# Data layer: state and plan

Written 2026-11-10. The data-layer work began in the `break-point` repo on 2026-11-07 and moved here on 2026-11-10. This file is the starting point for that work: what is built, how to run it, what is decided, and what comes next. Decisions with their alternatives are in `docs/decisions/`.

## 1. What is built

| Piece | State | Checked 2026-11-10 |
|---|---|---|
| Python tooling: uv, Python 3.13, ruff, pyright, pytest, hypothesis, pre-commit config, CI workflow | in place | `ruff check` passed; `ruff format --check` clean; `pyright` 0 errors; `pytest` 1 passed |
| Object store: SeaweedFS 4.52 in Docker (`infra/docker-compose.yml`, compose project `stat-cave`) | running | container `stat-cave-seaweedfs-1` up; S3 endpoint answers (403 without credentials, as it should) |
| Store data: named volume `stat-cave_seaweed-data`, inside Docker Desktop's disk image on `M:\docker` | in place | master reports Max 500, Free 493 volumes |
| Bucket `raw` | created | holds one object, `_smoke/hello.txt` |
| `scripts/s3_smoke.py` | passes | bucket created, 57-byte object written, read back identical |

Not built: the catalog, any engine, any fetcher, any table.

## 2. How to run it

From `infra/`:

    docker compose config --quiet    # validate; prints nothing when the file is good
    docker compose up -d             # start the store
    docker compose ps                # state

From the repo root:

    uv run --env-file infra/.env python scripts/s3_smoke.py

- S3 API from the host: `http://localhost:8333`. From another container on the compose network: `http://seaweedfs:8333`.
- Master status: `http://localhost:9333` (JSON at `/dir/status`).
- `infra/.env` holds `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_ENDPOINT` and is gitignored. `infra/.env.example` carries every key, with placeholders for the secrets. `docker compose config` without `--quiet` prints the secrets.

## 3. Decided

| # | Decision | Where |
|---|---|---|
| 0001 | Lakehouse: object storage + Iceberg + REST catalog + Spark / Trino / DuckDB / PyIceberg; local Docker first, cloud twin later | `docs/decisions/0001-lakehouse-data-layer.md` |
| 0002 | SeaweedFS as the local object store; named volume; capped at 500 x 1 GB volumes | `docs/decisions/0002-object-store.md` |

Conventions settled along the way:

- **Layout:** `infra/` holds compose and per-service config; `scripts/` holds run-not-import scripts; code enters `src/stat_cave/` when it is imported by something; `docs/decisions/NNNN-<slug>.md` records each choice as chose / alternatives / why / revisit when.
- **Raw lives in the store.** Source responses go into the `raw` bucket through the S3 API. Folders on `M:` are import sources and an offline backup, not the system of record.
- **Few buckets, many prefixes.** Each SeaweedFS bucket pre-allocates about seven 1 GB volumes; layers are buckets, everything else is a prefix.
- **Clients:** explicit credentials and endpoint (never the default AWS credential chain), `region_name` set, path-style addressing, paginated listing.
- **Tool choices:** a tool that is more than this repo needs is acceptable when the excess is only unnecessary, not when it is harmful (cost, run burden on the small machine, lock-in). Images must be multi-arch (one machine is arm64).
- **Per-machine differences** live in `.env` or a gitignored compose override, never in the tracked compose file.

## 4. Stack still to stand up

In this order, one unit at a time:

| Unit | What it is for | Open choice |
|---|---|---|
| Iceberg REST catalog | the one place that knows each table's current metadata file; what makes commits atomic across engines | which implementation: `apache/iceberg-rest-fixture`, Lakekeeper, Apache Polaris, Nessie, Gravitino. Not yet compared |
| Spark | loads and `MERGE INTO`; runs in a container, driven from host Python over Spark Connect | image and version |
| Trino | interactive SQL over the same tables | image and version |
| DuckDB, PyIceberg | in-process reads and small appends from Python | none; `uv add` |

Python clients resolve together on 3.13 (resolver check 2026-11-07, not installed): pyspark 4.3.0, pyiceberg 0.12.0, trino 0.340.0, duckdb 2.0.0, polars 2.1.0, pyarrow 26.0.1.

## 5. Builds after the stack

The outline as first drawn up. Which data each build uses, and in what order the sources are loaded, is not decided.

1. **Lakehouse core:** raw into the store, an arrival manifest, first Iceberg tables, incremental merge.
2. **Prices as a tick store:** quotes over time, best price across books, as-of joins to games.
3. **Exchange tick capture:** trades and order-book data from Kalshi, captured live.
4. **Point-in-time reference data:** teams, players and aliases with validity dates.
5. **Write-audit-publish:** quality gates on a branch before a load is published.
6. **Footprint and service levels:** storage and compute per table, freshness targets, and a wide fan-out workload to size the engines. (The original item was a feature-selection fan-out, which is modelling and sits outside this repo's boundary; what replaces it is open.)
7. **Cloud twin and migration:** the same tables on S3 + Glue + Athena, a dual run, and a parity and cost comparison.

## 6. Working cycle

Each unit of work goes: write, validate, run, check, record (decision entry where a choice was made), commit. A unit is not done until the check has output to show for it.

## 7. Open

1. The catalog choice (section 4).
2. A backup routine for the store. A Docker Desktop reset wipes the named volume; until a routine exists, raw is also kept as files on `M:`.
3. The 500-volume cap is a first number; revisit after the first large load.
4. Whether the store is ever needed from the other machines while the PC hosts it, and how the 16 GB Linux box is served.
5. Monitoring of store usage: nothing alerts before the cap or the disk limit is reached.
