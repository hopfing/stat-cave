# 0001: A lakehouse for the data layer

Date: 2026-11-07, decided in the `break-point` repo; carried to stat-cave
2026-11-10 when the data-layer work moved here.

## Chose
Object storage (S3 API) holding raw captures and Iceberg tables, a REST
catalog, and separate compute engines (Spark for loads and merges, Trino
for interactive SQL, DuckDB/PyIceberg in-process from Python). Runs locally
in Docker first; a cloud twin on S3 + Glue + Athena follows for comparison.

## Alternatives
- Parquet files on disk read with DuckDB/Polars: simplest, and fast at
  today's size; no transactions, no schema evolution, no shared catalog,
  and every machine reads its own copy of the files.
- A database (Postgres, or a DuckDB file): fine for reference data; raw
  API responses, play-by-play and price ticks are semi-structured and
  append-heavy, which is not what a row store is for, and a single file
  does not serve three machines.
- Cloud-only from the start: pay-per-scan compute; egress makes local
  iteration expensive, and nothing works offline.

## Why this
Some of this is deliberately more than the data needs today. The stack is
the one a larger data operation might use in production (object storage, an
open table format, a shared catalog, engines chosen per workload), chosen
in part to build and operate those tools here rather than take the
single-machine shortcuts a project this size usually takes. The design also
fits what this repo is for
("pristine quality and timely data"), which is what makes it worth
building here rather than on a toy:

- Raw kept as received: every source response lands in the store
  unedited, under a key that carries source, league, game and fetch time.
  A parser fix never needs a re-fetch.
- Timeliness is measured, not assumed: an arrival manifest (one row per
  raw object: key, fetch time, size, content hash) turns lag and missed
  days into queries, and the hash separates a changed re-fetch from an
  identical one.
- Point in time: tables keep what was known when (a line or an injury
  report as fetched before the game), and Iceberg snapshots keep the
  previous state of a table after a correction is merged in.
- Quality gates: a load is written to a branch, checked (row counts,
  agreement with a second source, known defects), and published only if
  the checks pass.
- Incremental by default: `MERGE` into partitioned tables instead of full
  rebuilds as leagues and seasons are added.
- The catalog is the contract with modelling: other repos read tables by
  name and import no code from this one; commits are atomic, so a reader
  never sees a half-written load.
- One store, many machines, any engine: the PC hosts storage; the other
  machines run engines against it; the same tables serve Spark, Trino,
  DuckDB and Python, so an engine can change without a data migration.

Scale: the tennis raw estate is 300+ GB today and is in scope for this
repo, though not first. What the other sports add is unknown; ten leagues
of play-by-play, box scores and price history have only been sampled.

## Costs accepted
Several services to run before the first fetcher exists; two JVM engines
that are oversized for the sports data on disk today; a serving story for
the 16 GB Linux box that is not yet designed.

## Revisit when
- The parity/footprint comparison against plain Parquet + DuckDB shows no
  benefit at the real workload.
- Operating the stack takes more time than the data work it supports.
