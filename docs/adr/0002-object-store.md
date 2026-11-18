# 0002: SeaweedFS as the local object store

Date: 2026-11-07

## Chose
SeaweedFS 4.52 (`weed server -s3`), run via compose, data in a Docker
named volume on M: (Docker Desktop disk image moved to M:\docker).

## Alternatives
- MinIO: the usual choice; its free images were withdrawn from Docker Hub
  and quay.io in Sept 2026 and the repo is archived. Not viable.
- RustFS: MinIO-shaped, Apache-2.0, GA since Sept 2026. Open bugs on
  multipart (503) and conditional writes; Trino write support not claimed.
  Too young for the layer everything sits on.
- Garage: no conditional writes by design; Iceberg writers need them.
- Versity gateway: POSIX backend, no track record with Iceberg engines.
- S3 directly: ~$7/month for storage, but ~$0.09/GB egress makes local
  engines reading the full dataset cost ~$20 per pass. Deferred to the
  cloud-twin build, where compute sits beside the data (Athena).
- Plain local files, no object store: cheapest, but every engine and the
  ingest code would be written against a filesystem, then rewritten for S3.

## Why SeaweedFS
Documented, maintained setups for Trino, Spark and DuckDB; conditional
writes (`If-None-Match`) supported; weekly releases; Apache-2.0; multi-arch
image. Its admin UI is work-in-progress and its bundled Iceberg catalog is
not used.

## Named volume rather than bind mount
Bind mounts of Windows paths cross the VM file-sharing layer and are slow
for many small files; the data dir is opaque volume files either way, so
browsability is not lost. Cost: a Docker Desktop reset wipes the volume.
Raw data is also kept as plain files on M: (`M:\mvp-data\raw`,
`M:\sports-scratch`) until a backup routine for the store exists. Docker
Desktop's virtual disk limit is set below M:'s free space so the VM cannot
outgrow the drive, and the store itself is capped at 500 x 1 GB volumes
(`-volume.max=500` in compose).

## Revisit when
- Write/read throughput on the first large load is unacceptable.
- The Beelink or Mac needs the store and the PC cannot host it.
- The cloud-twin comparison shows S3 + Athena cheaper for the real workload.
