# Data layer: state and plan

Written 2026-11-10, last updated 2026-11-12. The data-layer work began in the `break-point` repo on 2026-11-07 and moved here on 2026-11-10. This file is the starting point for that work: what is built, how to run it, what is decided, and what comes next. Decisions with their alternatives are in `docs/decisions/`.

## 1. What is built

| Piece | State | Checked 2026-11-10 |
|---|---|---|
| Python tooling: uv, Python 3.13, ruff, pyright, pytest, hypothesis, pre-commit config, CI workflow | in place | `ruff check` passed; `ruff format --check` clean; `pyright` 0 errors; `pytest` 1 passed |
| Object store: SeaweedFS 4.52 in Docker (`infra/docker-compose.yml`, compose project `stat-cave`) | running | container `stat-cave-seaweedfs-1` up; S3 endpoint answers (403 without credentials, as it should) |
| Store data: named volume `stat-cave_seaweed-data`, inside Docker Desktop's disk image on `M:\docker` | in place | 2026-11-12: master reports Max 500, Free 486 volumes |
| Buckets `statcave-raw`, `statcave-bronze`, `statcave-silver`, `statcave-gold` | created 2026-11-12 (the first bucket, `raw`, was renamed) | `statcave-raw` has versioning enabled and holds one object, `_smoke/hello.txt`; the other three are empty |
| `scripts/setup_stack.py` | written; not yet run on this stack (no catalog here yet) | 2026-11-12, throwaway stacks: on an empty store the first run created the four buckets, versioning on `statcave-raw`, the catalog `statcave`, its grant and the three layer namespaces; a rerun reported all of them as existing and changed nothing; with a catalog property altered it stopped and named the difference |
| `scripts/s3_smoke.py` | passes; checks only | 2026-11-12, this store: four buckets present, versioning `Enabled` on `statcave-raw`, object written with a version id, read back identical. On throwaway stores it failed, as it should, with the buckets missing and with versioning suspended |
| `scripts/catalog_probe.py` | not yet adapted | ran 2026-11-11 against throwaway containers (12 of 12 steps on Polaris and on Lakekeeper); to be pointed at this stack once the catalog is up, then rerun after catalog upgrades. When adapted it works only inside its own namespace (`bronze._probe`), drops only the tables it created in that run, and never purges |
| Docker volume guard: `.claude/settings.json`, `.claude/hooks/docker_guard.py`, `tests/test_docker_guard.py` | in place | 2026-11-12: `pytest` 44 passed (24 refused forms, 17 allowed, exit codes); a live `docker volume rm` from the assistant's Bash and PowerShell tools was refused |

Not built: the catalog, any engine, any fetcher, any table.

The store had stopped after a reboot on 2026-11-12 because the service had no restart policy; data in the volume was intact. Every service gets `restart: unless-stopped` (pending in the compose file).

## 2. How to run it

From `infra/`:

    docker compose config --quiet    # validate; prints nothing when the file is good
    docker compose up -d             # start the store
    docker compose ps                # state

From the repo root:

    uv run --env-file infra/.env python scripts/setup_stack.py   # set up: buckets, versioning, catalog, namespaces
    uv run --env-file infra/.env python scripts/s3_smoke.py      # check the store

Setting up and checking are separate steps. `setup_stack.py` creates what is missing and is safe to rerun, so one run rebuilds a fresh store and catalog; where a catalog or namespace exists but differs from what the script describes, it stops instead of changing it. `s3_smoke.py` creates nothing and fails when a bucket or the versioning on `statcave-raw` is missing. `setup_stack.py` needs the catalog up and the `requests` package.

- S3 API from the host: `http://localhost:8333`. From another container on the compose network: `http://seaweedfs:8333`.
- Master status: `http://localhost:9333` (JSON at `/dir/status`).
- `infra/.env` holds `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_ENDPOINT`, and for the catalog `POLARIS_DB_PASSWORD`, `POLARIS_CLIENT_ID`, `POLARIS_CLIENT_SECRET`, `POLARIS_ENDPOINT`. It is gitignored. `infra/.env.example` carries every key, with placeholders for the secrets. `docker compose config` without `--quiet` prints the secrets.

## 3. Decided

| # | Decision | Where |
|---|---|---|
| 0001 | Lakehouse: object storage + Iceberg + REST catalog + Spark / Trino / DuckDB / PyIceberg; local Docker first, cloud twin later | `docs/decisions/0001-lakehouse-data-layer.md` |
| 0002 | SeaweedFS as the local object store; named volume; capped at 500 x 1 GB volumes | `docs/decisions/0002-object-store.md` |
| 0003 | Apache Polaris as the Iceberg REST catalog, backed by Postgres; Lakekeeper is the fallback if the bring-up fails | `docs/decisions/0003-iceberg-rest-catalog.md` |

Conventions settled along the way:

- **Layout:** `infra/` holds compose and per-service config; `scripts/` holds run-not-import scripts; code enters `src/stat_cave/` when it is imported by something; `docs/decisions/NNNN-<slug>.md` records each choice as chose / alternatives / why / revisit when.
- **Raw lives in the store.** Source responses go into the `statcave-raw` bucket through the S3 API. The bucket is versioned, so an overwrite or a delete keeps the earlier bytes. Folders on `M:` are import sources and an offline backup, not the system of record.
- **Layer names:** medallion, with a raw zone in front. `raw`: source responses as received, objects in the store. `bronze`: raw parsed into tables one-to-one (the source's own field names, fetch time, append-only). `silver`: cleaned, deduplicated, conformed tables and cross-source ids. `gold`: consumer-shaped joins and aggregates; feature engineering stays outside this repo.
- **One bucket per layer, named as on AWS** (2026-11-12): `statcave-raw`, `statcave-bronze`, `statcave-silver`, `statcave-gold`, the same names as the AWS buckets in `us-east-2`. Iceberg metadata stores full `s3://bucket/key` paths, so a table moves between the local store and S3 without a path rewrite only when the bucket name is the same on both sides. Raw holds files as received and is not a table layer. Bronze, silver and gold are namespaces in one catalog, each created with its location set to its own bucket; the catalog's allowed locations list those three buckets and nothing else. Everything below a layer is a prefix: each SeaweedFS bucket pre-allocates about seven 1 GB volumes, so buckets are not added casually.
- **Tables sit at least one namespace below the layer** (2026-11-12): `<layer>.<source>.<table>`, for example `bronze.oddspapi.odds` at `s3://statcave-bronze/oddspapi/odds`. No table sits directly in a layer namespace. The fallback to one catalog per layer (section 7, item 6) depends on this layout.
- **Clients:** explicit credentials and endpoint (never the default AWS credential chain), region `us-east-2`, path-style addressing, paginated listing.
- **Tool choices:** a tool that is more than this repo needs is acceptable when the excess is only unnecessary, not when it is harmful (cost, run burden on the small machine, lock-in). Images must be multi-arch (one machine is arm64).
- **Per-machine differences** live in `.env` or a gitignored compose override, never in the tracked compose file.
- **Setup is a script, not a compose service** (2026-11-12). `polaris-bootstrap` stays a run-once compose service because it is the vendor's own tool in the vendor's image and the catalog cannot start without it. Buckets, the catalog and its namespaces are this repo's layout: they change as the layout changes, they are linted and reviewed as code, and they are applied on purpose, not on every `up`. It is the same split as on the cloud side, where buckets and Glue databases are provisioned apart from the services that use them.
- **Volumes are not deleted by an assistant** (2026-11-12). The store's files and the catalog's database are Docker named volumes. A Claude Code hook (`.claude/hooks/docker_guard.py`) refuses `docker compose down -v`, `docker volume rm`, `docker volume prune` and `docker system prune` anywhere in a command the assistant runs. It does not cover a person's own terminal. Throwaway test stacks are removed by the user, or run without named volumes.

## 4. Stack still to stand up

In this order, one unit at a time:

| Unit | What it is for | Open choice |
|---|---|---|
| Iceberg REST catalog | the one place that knows each table's current metadata file; what makes commits atomic across engines | chosen 2026-11-12: Apache Polaris 1.8.0 with Postgres (decision 0003), one catalog named `statcave`. The compose block ran on a throwaway project on 2026-11-12 (different project name, host ports and secrets; otherwise the same text). Bring-up on this stack is pending; if it fails, Lakekeeper is next |
| Spark | loads and `MERGE INTO`; runs in a container, driven from host Python over Spark Connect | image and version |
| Trino | interactive SQL over the same tables | image and version |
| DuckDB, PyIceberg | in-process reads and small appends from Python | none; `uv add` |

All four engines created, wrote and read a table two namespaces deep (`bronze.src_<engine>.odds`) on a throwaway Polaris 1.8.0 with the `statcave` catalog as `setup_stack.py` sets it up (2026-11-12). Every table landed at `s3://statcave-bronze/src_<engine>/odds`, and each engine read the tables the others wrote. Versions used were the newest available, not a choice: Spark 4.1.4 (`apache/spark:4.1.4`, the newest Spark with an Iceberg runtime; Iceberg 1.12.0 jars fetched from Maven Central at start), Trino 484 (`trinodb/trino:484`), DuckDB 2.0.0 with its `iceberg` extension, PyIceberg 0.12.0. What each needed beyond endpoint, keys, region and path-style addressing:

- **Spark:** nothing for the nesting; tables are named `statcave.bronze.<source>.<table>`. Set `oauth2-server-uri` to Polaris's token endpoint, or the Iceberg client warns that its fallback will be removed.
- **Trino:** `iceberg.rest-catalog.nested-namespace-enabled=true`; a schema is the quoted dotted name, `"bronze.<source>".<table>`. `iceberg.rest-catalog.server-assigned-table-location-enabled=true`: without it Trino sends its own table location, which the catalog refuses (`allow.client-specified.table.location=false`; 400 "Specifying a table or view location using the location field is not allowed"). With it, Trino 484's source commits a new table at once rather than staging it with the first write, so a failed `CREATE TABLE AS` can leave an empty table (read in the source, not tested). Also `iceberg.rest-catalog.oauth2.server-uri` (same warning as Spark) and `fs.s3.enabled` (`fs.native-s3.enabled` is deprecated in 484).
- **DuckDB:** `ATTACH ... (TYPE iceberg, SUPPORT_NESTED_NAMESPACES true, ACCESS_DELEGATION_MODE 'none')`; a schema is the quoted dotted name, `sc."bronze.<source>".<table>`. Without the option a quoted two-level name still reads, but listings show only `bronze`, `silver`, `gold`, and the unquoted `sc.bronze.<source>.<table>` is not found.
- **PyIceberg:** nothing for the nesting; identifiers are tuples, `("bronze", "<source>", "<table>")`. The header `X-Iceberg-Access-Delegation` set to empty, as before.

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

1. The catalog bring-up (section 4): compose services, `setup_stack.py`, and these checks, each with output to show:
   - every service is up, and `polaris` and `polaris-db` report `healthy`;
   - a table created without an explicit location lands only in its layer's bucket;
   - restart: create a table, `docker compose down` (without `-v`), `docker compose up -d`, read the table back;
   - restart Docker Desktop and confirm every service comes back without a hand-run `up`.

   The first three passed on a throwaway project on 2026-11-12. The Docker Desktop restart has not been tried anywhere.
2. Backup, to be set up before the first real load: `statcave-raw` synced to the `statcave-raw` bucket on AWS, and a `pg_dump` of the catalog's Postgres (`stat-cave_polaris-db`). The Postgres volume holds every table's pointer to its current metadata file; without it the data files are still in the store, but which metadata file is current for each table has to be worked out and re-registered by hand. A Docker Desktop reset wipes both named volumes; until the backup exists, raw is also kept as files on `M:`.
3. The 500-volume cap is a first number; revisit after the first large load.
4. Whether the store is ever needed from the other machines while the PC hosts it, and how the 16 GB Linux box is served.
5. Monitoring of store usage: nothing alerts before the cap or the disk limit is reached.
6. Giving each layer namespace its own bucket relies on the Polaris catalog property `polaris.config.namespace-custom-location.enabled`, which Polaris classes as a behaviour-change flag that may be removed. `scripts/catalog_probe.py` has to cover it so an upgrade that drops it is caught. The single catalog with the flag was agreed on 2026-11-12. The fallback is one catalog per layer (`statcave_bronze`, `statcave_silver`, `statcave_gold`, each allowed its own bucket only), and the move to it was tested on a throwaway Polaris 1.8.0 on 2026-11-12 with two tables per layer, each with two snapshots:
   1. Stop writers. Record every table's current metadata file (`metadata_location`) and take a `pg_dump`. From step 2 until step 5 the tables are in no catalog and that list is the only pointer to them. (In the test the current file was the highest-numbered `NNNNN-*.metadata.json` in the table's `metadata/` folder.)
   2. Drop every table without purge. All 54 objects in the store were unchanged afterwards (same keys, sizes, ETags and modified times).
   3. Drop the namespaces and delete the catalog `statcave`. This cannot be skipped: Polaris refuses a per-layer catalog while `statcave` exists (400, "locations overlaps with an existing catalog"), so the two layouts cannot run side by side.
   4. Create the three catalogs and a namespace in each.
   5. `register_table` each recorded metadata file. Afterwards every table had the same metadata file, location, table uuid, snapshot ids and row count as before, and no object had been moved, rewritten or deleted. A later append wrote new files beside the old ones.

   These steps depend on the table layout in section 3: every table at least one namespace below its layer. A table in `bronze.<source>` (at `s3://statcave-bronze/<source>/<table>`) registered into namespace `<source>` of `statcave_bronze` with no extra property. A table directly in a layer namespace would sit at the bucket root (`s3://statcave-bronze/<table>`), outside every namespace of the new catalog, and registering it is refused (403) unless the new catalog sets `polaris.config.allow.unstructured.table.location=true`; with that set it passed in the test. Deeper namespaces (`bronze.<source>.<x>`) were not tried in the move. Not carried over: grants and catalog roles, which belong to a catalog and are made again. Not tested: branches and tags (they are refs in the same metadata file); views; what an upgrade that removes the flag does to namespaces that already have a custom location.
7. Multi-table commits have been tested for a rejected requirement only, not for a crash in the middle of a commit (decision 0003).
8. Versioning on `statcave-raw` keeps every overwritten and deleted version; nothing expires old versions yet.
