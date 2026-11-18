# 0003: Apache Polaris as the Iceberg REST catalog

Date: 2026-11-12

Status: chosen; brought up on this repo's stack on 2026-11-18, where all
four bring-up checks passed: services healthy, table placement per layer
bucket, tables read back after `docker compose down` and `up -d`, and
every service back after a Docker Desktop restart. The same compose text
and `scripts/setup_stack.py` first ran on a throwaway project on
2026-11-12. Lakekeeper remains the fallback if the catalog has to be
replaced.

## Chose
Apache Polaris 1.8.0 (`apache/polaris`), with its metadata in Postgres
(`relational-jdbc` persistence, a named volume) instead of the in-memory
default, bootstrapped by `apache/polaris-admin-tool`. Engines use static
store keys; credential vending is off (`stsUnavailable`) and did not count
for or against any candidate.

## Selection test
Is this commonly run in production, the kind of component a high-volume
data platform is built on? Extra complexity is a price paid for that, not
a goal. Where two options are equal on that test, the simpler one wins.

## Alternatives
- Lakekeeper v0.15.0: the other finalist and the fallback. Passed the same
  probe (12 of 12 steps) and is lighter: 171 MiB idle with its Postgres
  against 492 MiB for Polaris with its Postgres, both unconstrained. It
  lost on the selection test: one vendor (Vakamo), open core, still 0.x,
  64 contributors with one person holding about three quarters of the
  human commits.
- `apache/iceberg-rest-fixture`: its README titles it a test fixture; no
  auth; the default catalog is a SQLite file in `/tmp`, lost when the
  container is recreated. Fits throwaway tests, not the foundation.
- Nessie: its docs call Iceberg REST support experimental, and it returns
  a single snapshot per table by design, so Iceberg's own table branches
  and tags (which write-audit-publish uses) are unavailable. Its
  catalog-wide branches would not carry to Glue.
- Gravitino: secondary reading only, not verified first-hand: multi-table
  commits not implemented, 1 GB default heap. Not taken further.
- SeaweedFS's bundled catalog: young commit-path code, and it ties the
  catalog to the store so neither can be swapped alone.

## Why Polaris
- Governance and adoption: Apache top-level project since 2026-02, 19 PMC
  members, 192 contributors with commits spread across them, 1.x releases.
  Snowflake and Dremio say their managed catalogs are built on it (vendor
  claims, read from search results, not from the vendors' pages). No
  independent named production user was found for either finalist.
- It passed the probe on SeaweedFS 4.52 (2026-11-11, throwaway
  containers): create, write and read a table with the client's own
  static keys; a branch and a tag, with a write to the branch leaving
  `main` unchanged; publish by moving `main` under
  `assert-ref-snapshot-id`, with a stale replay refused (409); a
  multi-table commit. Multi-arch images (amd64, arm64).
- Its cost is memory, and the cost is bounded: with `mem_limit: 768m` it
  used 263 MiB after start and 321 MiB after the probe workload.

## What was and was not tested
- Multi-table commit (`POST /v1/{prefix}/transactions/commit`) was tested
  for a rejected requirement: one table's requirement fails, the catalog
  answers 409, and neither table changes. Both finalists passed.
- It was not tested for a crash in the middle of a commit. Whether a
  Polaris process killed between the two table updates leaves one table
  moved and the other not is unknown.
- Polaris's `commitTransaction` carries an open `// TODO: Retries on
  failure` (`runtime/service/.../catalog/iceberg/IcebergCatalogHandler.java`,
  line 1593 at tag `apache-polaris-1.8.0`). Until that is tested or
  closed upstream, a load that needs several tables to move together
  should not assume it survives a failure mid-commit.

## Layer layout
One catalog. `bronze`, `silver` and `gold` are namespaces, each created
with its location set to its own bucket (`s3://statcave-bronze` and so
on); the catalog's allowed locations are those three buckets;
`statcave-raw` is not in the list. Tested on a throwaway Polaris 1.8.0:
- a table created without a location lands in its namespace's bucket;
- a table location in another layer's bucket, or in `statcave-raw`, is
  refused (403);
- with `polaris.config.allow.client-specified.table.location=false`, any
  explicit table location is refused (400);
- a namespace created without a location is refused (403: it would
  overlap `bronze`), as is a second namespace inside a layer's bucket.

This needs the catalog property
`polaris.config.namespace-custom-location.enabled=true`. Polaris classes
it as a behaviour-change flag: unstable, and removable in a later release.
The alternative that needs no such flag is one catalog per layer, at the
cost of three catalog registrations in every engine and no multi-table
commit across layers. The single catalog, named `statcave`, was agreed on
2026-11-12: a Glue database also carries its own location, so the layout
matches the cloud twin.

The move to one catalog per layer was tested on a throwaway Polaris 1.8.0
on 2026-11-12. It re-registers each table's current metadata file and
leaves every object where it is: after dropping the tables without purge,
deleting the single catalog and registering the tables in three new
catalogs, each table had the same metadata file, uuid, snapshots and rows,
and no object in the store had changed. Two limits were found:
- the per-layer catalogs cannot be created while the single catalog
  exists (400, overlapping locations), so there is no side-by-side run and
  the tables are in no catalog during the move;
- a table directly in a layer namespace sits at the bucket root, and
  registering it in a per-layer catalog is refused (403) unless that
  catalog sets `polaris.config.allow.unstructured.table.location=true`;
  a table in a second-level namespace registers with no extra property.

Tables therefore sit at least one namespace below the layer,
`<layer>.<source>.<table>` (agreed 2026-11-12), and the fallback depends on
that layout. Spark, Trino, DuckDB and PyIceberg each created, wrote and
read such a table against this catalog on a throwaway stack (2026-11-12).
Trino needs `server-assigned-table-location-enabled`, because the catalog
refuses a table location sent by the client.

The steps are in `docs/data-layer.md`, section 7, item 6.

## Revisit when
- The probe fails after an upgrade.
- An upgrade removes `namespace-custom-location.enabled`: move to one
  catalog per layer, by the tested steps.
- A load needs several tables to change together: test a crash
  mid-commit first.
- Polaris's memory use on the 16 GB machine crowds out Spark or Trino.
