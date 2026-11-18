# Raw landing, arrival tracking and bronze shape: what established pipelines document

Research for issue #4, written 2026-11-18. It gathers the options that vendors and open-source ingestion tools document for the first three steps of a pipeline (landing raw data, tracking arrivals, shaping bronze), with their trade-offs for this repo's stack: SeaweedFS as S3-compatible storage, Iceberg tables in a Polaris REST catalog, PyIceberg first, then Spark, Trino and DuckDB. It recommends nothing. Where a source takes a position, that is marked as the source's position.

What is already decided and is taken as given here (`docs/data-layer.md`, section 3; `docs/adr/0001-lakehouse-data-layer.md`):

- Raw is the versioned bucket `statcave-raw`, holding source responses as received. It is not a table layer, and the catalog's allowed locations do not include it (ADR 0003).
- Bronze is raw parsed one-to-one into append-only Iceberg tables, with the source's own field names and the fetch time.
- ADR 0001 sketched, without deciding the details, a key carrying source, league, game and fetch time, and an arrival manifest with one row per raw object (key, fetch time, size, content hash).

The three shapes every option is checked against (issue #1, Notes):

1. **Re-fetched request/response data** (ESPN): the same URL fetched many times, the answer corrected over time; JSON or HTML.
2. **Whole files published by a third party** (sportsdataverse season files): a file downloaded as published; the publisher may replace it under the same name.
3. **Append-only price history from an API** (Kalshi, OddsPapi): many small pages over time, the data itself never revised.

## How to read the citations

Each claim carries a source URL and a marker:

- **[read]**: read directly on 2026-11-18, at that URL or in the file it serves.
- **[search]**: seen only in a search-result summary; the page itself could not be fetched.
- **[memory]**: from prior knowledge, not checked in this research.
- **[inference]**: this document's own reasoning from the cited facts, not a statement any source makes.

The Iceberg facts were read from the project's Markdown sources on the `main` branch of `apache/iceberg` and `apache/iceberg-python` (the same text the documentation site renders), because the rendered site pages were too long to fetch. The `main` branch of the spec already contains v4 draft text; only v3 facts are used below.

---

## Step 1: Landing raw data

### 1.1 Object key layout

Four layouts appear in the sources.

| Layout | Who documents it | How it works |
|---|---|---|
| **Arrival time first**: `prefix/YYYY/MM/dd/HH/<name>-<time>-<uuid>` | Amazon Data Firehose, its default [read: https://docs.aws.amazon.com/firehose/latest/dev/s3-object-name.html] | Firehose adds a `YYYY/MM/dd/HH` prefix by default, in UTC, and a suffix of stream name, stream version, a timestamp to the second and a UUID. The time is "the approximate arrival timestamp of the oldest record that's contained in the Amazon S3 object being written" [read: https://docs.aws.amazon.com/firehose/latest/dev/s3-prefixes.html]. |
| **Source and table, then date**: `source/source-region/table/year/month/day` | AWS Prescriptive Guidance, raw layer naming [search: https://docs.aws.amazon.com/prescriptive-guidance/latest/defining-bucket-names-data-lakes/raw-data-layer-naming-structure.html] | Paths add source, source region, table, then year/month/day partitions. The page returned 403 to a direct fetch; this is from the search summary only. Snowflake likewise recommends that you "partition your data in stages using logical, granular paths by date" [read: https://docs.snowflake.com/en/user-guide/data-load-considerations-load]. |
| **Load id in the key**: `{table_name}/{load_id}.{file_id}.{ext}` | dlt, filesystem destination default [read: https://dlthub.com/docs/dlt-ecosystem/destinations/filesystem] | Each run's files carry the id of the load package that wrote them; placeholders for dates and timestamps are available. |
| **Entity first, then fetch time**: `source/dataset/<entity ids>/<fetch time>` | No first-party convention found. It is the layout ADR 0001 sketched. | The key answers "every fetch of this game" by prefix, rather than "everything fetched on this day". |

Facts that bear on the choice, all for S3 itself:

- **Request rates do not argue for randomised prefixes at this size.** S3 serves "at least 3,500 PUT/COPY/POST/DELETE or 5,500 GET/HEAD requests per second per partitioned Amazon S3 prefix", with no limit on the number of prefixes [read: https://docs.aws.amazon.com/AmazonS3/latest/userguide/optimizing-performance.html]. Firehose offers a random-string prefix only "for attaining extremely high throughput" [read: https://docs.aws.amazon.com/firehose/latest/dev/s3-prefixes.html].
- **Listing order is lexicographic.** `ListObjectsV2` returns keys in lexicographic order for general-purpose buckets, up to 1,000 per request, and `StartAfter` starts the listing after a given key [read: https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html]. A key in which time sorts as text can therefore be listed from a cursor. Databricks Auto Loader detects when files arrive "with lexical ordering" and then makes far fewer listing calls [read: https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/file-detection-modes]. Whether SeaweedFS honours `StartAfter` the same way was not checked.
- **Characters.** Keys are up to 1,024 bytes of UTF-8. The colon (`:`), the equals sign (`=`), `+`, `&`, `,`, `?` and space are on S3's list of characters that "might require special handling" [read: https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-keys.html]. An ISO 8601 timestamp with colons (`2026-11-18T15:24:00Z`) and Hive-style `year=2026/` segments (as in Firehose's own examples) both fall on that list. The basic ISO form (`20261118T152400Z`) does not [inference].
- **Versioning and keys.** With bucket versioning on, writing to an existing key keeps the earlier bytes as an older version. The `If-None-Match: *` conditional write refuses to write when a key already exists (412 Precondition Failed), and in a versioned bucket it "only applies to the current version of an object" [read: https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html]. SeaweedFS lists conditional headers as supported [read: https://github.com/seaweedfs/seaweedfs/wiki/Amazon-S3-API]; ADR 0002 records `If-None-Match` as supported.

**The point the owner has to decide here: one key per fetch, or one key per thing with versions underneath.**

- *Fetch time in the key* (a new key for every fetch). Every fetch is visible in a plain listing; `If-None-Match` can make a write create-only, so nothing is overwritten by accident; deleting old fetches is per key. The key grows one segment, and "the latest response for game X" needs a listing of the prefix and a sort.
- *Stable key per thing, relying on bucket versioning* (the same key overwritten on each re-fetch). "The latest" is a plain GET; the history is in `ListObjectVersions`. But the fetch time is then not in the key, the earlier responses are reached only through version ids, and most tools that discover files by listing (Auto Loader, Glue bookmarks, Snowpipe, below) see only the current version [inference from the tracking sources in step 2]. `docs/data-layer.md` item 8 already notes that nothing expires old versions.
- *Fetch time in metadata or a sidecar only*: see 1.2.

How the three shapes differ: for shape 1 the entity-first layout groups re-fetches of one game; for shape 2 the publisher's own file name and the download time both matter, because the publisher may replace a file under the same name; for shape 3 there is no natural entity, and arrival-time layouts (Firehose style) are the documented pattern for streams of small records.

### 1.2 What is recorded with each response, and where

**Which facts.** The sources that store HTTP captures or extracted records keep these:

- WARC, the ISO standard format for web archives (version 1.1) [read: https://iipc.github.io/warc-specifications/specifications/warc-format/warc-1.1/]: every record has `WARC-Record-ID`, `WARC-Date` ("the instant that data capture for record creation began"), `WARC-Type` and `Content-Length`. Optional fields include `WARC-Target-URI` (the URI fetched), `WARC-Payload-Digest` and `WARC-Block-Digest` (hashes), `WARC-IP-Address`, and `WARC-Concurrent-To`, which links a `request` record to the `response` record of the same capture. A `response` record holds "the full HTTP response received over the network, including headers", status line included.
- Singer: a RECORD message may carry `time_extracted`, "the time the record was observed in the source" [read: https://hub.meltano.com/singer/spec/].
- Airbyte: `_airbyte_raw_id` (a random UUID per record) and `_airbyte_extracted_at` ("when Airbyte pulled the event from the data source") [read: https://docs.airbyte.com/platform/understanding-airbyte/airbyte-metadata-fields].
- Snowflake, per staged file: file name with full path, a checksum (`METADATA$FILE_CONTENT_KEY`) and the last-modified time [read: https://docs.snowflake.com/en/user-guide/querying-metadata].
- S3 keeps its own system metadata: `Content-Length`, `Last-Modified`, `ETag` (an MD5 of the data only for objects not uploaded in parts and not KMS-encrypted), optional checksums, and `x-amz-version-id` [read: https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingMetadata.html]. `Last-Modified` is the time the store accepted the object, not the time the source answered [inference]; tools that track arrivals by modification time (Glue bookmarks, the Dagster sensor example, below) are tracking store time.

Gathered together, the candidate facts for one response are: the request (URL, method, query parameters, request headers that change the answer), the fetch start and end time, the HTTP status and response headers, the body's size and hash, the store's version id, a fetch or run id, and the fetcher's version [inference: union of the above].

**Where they can live.**

| Place | Who documents it | Trade-offs |
|---|---|---|
| **In the key** | Firehose (time), dlt (load id) | Seen in every listing without opening anything; fixed forever; only a few facts fit and they must be key-safe. |
| **S3 user metadata** (`x-amz-meta-*`) | AWS [read: https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingMetadata.html] | Returned by `HEAD` without the body. Limited to 2 KB in total; keys are stored in lowercase; US-ASCII is advised; "After you upload the object, you can't modify this user-defined metadata" except by copying the object. SeaweedFS supports it on PutObject and returns it on HeadObject [read: https://github.com/seaweedfs/seaweedfs/wiki/Amazon-S3-API]. Not returned by a listing, so reading it for many objects costs one `HEAD` each [inference]. |
| **Object tags** | AWS, same page | Up to 10 per object, changeable after upload. Changeable is the opposite of what a record of the fetch wants [inference]. |
| **An envelope around the body** (the stored object is a record that contains request, status, headers and body) | WARC (above); Singer and Airbyte wrap each record with extraction facts | Facts and bytes cannot be separated or lose each other. The stored object is then no longer the bytes "as received" but the bytes inside a wrapper, so a third-party file (shape 2) would not be stored byte-for-byte unless the wrapper is skipped for it [inference]. |
| **A sidecar object** next to the body (`<key>.meta.json` or similar) | No first-party convention found for per-object sidecars. dlt's filesystem destination writes a JSON file per completed load into a `_dlt_loads` folder, which is a per-load sidecar [read: https://dlthub.com/docs/dlt-ecosystem/destinations/filesystem]. | Body stays byte-for-byte; facts are unlimited in size. Two writes per response, which can be half done [inference]. |
| **Only in the manifest** (step 2) | ADR 0001's manifest; dlt's `_dlt_loads`; Airbyte's per-record columns | One place to query. The raw object alone no longer says what it is; if the manifest is lost, the facts are lost with it unless they are also in the key [inference]. |

### 1.3 One object per response, or batched

- **Batched is the documented default for streams of small records.** Firehose buffers records into one object per buffer interval [read: https://docs.aws.amazon.com/firehose/latest/dev/s3-object-name.html]. Snowpipe advises "compressed files of roughly 100 to 250 MB" and, when data arrives slowly, a new file about once per minute [read: https://docs.snowflake.com/en/user-guide/data-load-snowpipe-intro]. A WARC file is "a simple concatenation of one or more records", each record ideally its own gzip member so one record can still be read alone [read: https://iipc.github.io/warc-specifications/specifications/warc-format/warc-1.1/].
- **Many small objects cost at listing and discovery time.** Glue job bookmarks list every file under each input partition, and "if there are too many files under a single partition the bookmark can run into driver OOM" [read: https://docs.aws.amazon.com/glue/latest/dg/monitor-continuations.html]. A listing returns at most 1,000 keys per request (above).
- **One object per response** keeps each response separately addressable, separately versioned and separately hashable, which is what lets an identical re-fetch be recognised by its hash (ADR 0001) and what WARC's `revisit` record does for unchanged re-captures (below).

By shape: shape 1 fits one object per response (each re-fetch is its own capture); shape 2 is one object per published file by nature; shape 3 is where batching per run or per time window is the documented pattern, at the cost of a page no longer being one object [inference from the above].

### 1.4 Compression

- WARC recommends gzip applied per record [read: WARC 1.1, above]. Firehose offers gzip, zip and Snappy at delivery [read: https://docs.aws.amazon.com/firehose/latest/dev/s3-object-name.html]. S3 records a `Content-Encoding` for the object, which the uploader sets [read: https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingMetadata.html].
- Decision for the owner: store bodies as received (an HTTP body that arrived gzip-encoded may be stored as the decoded bytes or the encoded bytes), or compress at rest. A third-party file that is already compressed (shape 2) is left as is in every option [inference].

### 1.5 Failed responses

- **Keep it, with its status.** A WARC `response` record keeps whatever response came back, status line and headers included [read: WARC 1.1, above]; nothing in the format restricts it to successful responses [inference].
- **Keep it apart.** Firehose writes failed records under a separate `ErrorOutputPrefix` that must contain the failure type, for example `myFirehoseFailures/processing-failed/2018/08/27/` [read: https://docs.aws.amazon.com/firehose/latest/dev/s3-prefixes.html].
- **Record it, do not store it.** Snowpipe's load history records files that failed to load as well as those that loaded, so a corrected file staged again under the same name is ignored too (paraphrased, not quoted) [read: https://docs.snowflake.com/en/user-guide/data-load-snowpipe-intro]. That is a load failure, not a fetch failure, but the same choice applies.
- **Do not fail, note the change.** Airbyte nulls a value it cannot type and records why in `_airbyte_meta.changes` rather than failing the sync [read: https://docs.airbyte.com/platform/using-airbyte/core-concepts/typing-deduping]. That is a bronze-shape choice (step 3) rather than a landing choice.

Trade-offs [inference]: keeping failures in raw makes outages, rate limiting and source errors measurable from raw alone (ADR 0001's "timeliness is measured"), and keeps evidence for a response that was a 200 with an error body; it also means every later step must filter on status. Keeping them under a separate prefix keeps the main prefixes clean but splits "every fetch of game X" across two places. Recording only in the manifest is lighter but loses the body.

### 1.6 What raw guarantees

- **Nothing is lost on overwrite or delete**: bucket versioning, already on [decided; AWS guidance recommends versioning the raw layer: search: https://docs.aws.amazon.com/prescriptive-guidance/latest/defining-bucket-names-data-lakes/data-layer-definitions.html].
- **Nothing is overwritten at all**: `If-None-Match: *` on every write, with a fetch-unique key (1.1). Bucket policies can enforce conditional writes on AWS [read: https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html]; whether SeaweedFS can enforce them by policy was not checked.
- **Identical re-fetches recognised**: a hash of the body. WARC's `revisit` record with the `identical-payload-digest` profile records a re-capture whose payload digest matches an earlier one, instead of storing the bytes again [read: WARC 1.1, above]. Whether an identical re-fetch is stored again, stored as a pointer, or only recorded in the manifest is a decision.
- **Object Lock** (write-once retention) exists on S3; whether SeaweedFS supports it was not checked.

---

## Step 2: Tracking arrivals, and knowing what is unprocessed

Two separate questions sit here [inference]: (a) what records that an object arrived, and (b) how each later step (raw to bronze, bronze to silver) knows what it has not yet processed. Sources answer them with six mechanisms.

### 2.1 The mechanisms

**A. List the store each run, and filter.**
- Databricks Auto Loader's default "directory listing" mode finds new files by listing the input path [read: https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/file-detection-modes].
- AWS Glue job bookmarks: for S3 sources, "a timestamp is used to filter the new files", using each object's last-modified time; a file modified since the last run is processed again [read: https://docs.aws.amazon.com/glue/latest/dg/monitor-continuations.html].
- Dagster's sensor example lists a directory and launches a run for each file whose modification time is newer than a stored cursor, then saves the largest modification time as the new cursor [read: https://dagster.io/docs/guides/automate/sensors].
- Trade-offs: no extra system; works on any S3-compatible store. A listing returns only key, size, ETag and last-modified, so other facts need a `HEAD` or a read per object [read: ListObjectsV2, above; inference]. Filtering by modification time is a watermark on store time, which misses an object whose write lands after the watermark moved past its time [inference]. Cost grows with the prefix unless keys sort by time and `StartAfter` is used as the cursor [inference].

**B. Event notifications from the store.**
- S3 event notifications "are designed to be delivered at least once", typically within seconds, sometimes a minute or more [read: https://docs.aws.amazon.com/AmazonS3/latest/userguide/EventNotifications.html]. They "aren't guaranteed to arrive in the same order that the events occurred"; a `sequencer` value orders events for one key only [read: https://docs.aws.amazon.com/AmazonS3/latest/userguide/notification-content-structure.html].
- Databricks recommends "file notification mode using file events" over directory listing "for most workloads" (the source's position) [read: Auto Loader file detection modes, above]. Snowpipe's auto-ingest uses storage event notifications [read: https://docs.snowflake.com/en/user-guide/data-load-snowpipe-intro].
- **Not available locally**: SeaweedFS marks `PutBucketNotificationConfiguration` and `GetBucketNotificationConfiguration` as not supported [read: https://github.com/seaweedfs/seaweedfs/wiki/Amazon-S3-API]. It would exist on the AWS twin only.

**C. A checkpoint of processed files, kept by the reader.**
- Auto Loader keeps each discovered file in "a scalable key-value store (RocksDB) in the checkpoint location", which "ensures that data is processed exactly once"; it "does not guarantee the order in which files are discovered or processed" [read: https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/]. The option `cloudFiles.allowOverwrites` controls "Whether to allow input directory file changes to overwrite existing data" [read: https://docs.databricks.com/aws/en/spark/api-options]; that it defaults to false, so a changed file is not processed again, is from memory.
- Glue keeps bookmark state per job and per `transformation_ctx`, saved atomically at `job.commit`; it can be rewound to an earlier run or reset, and a reset does not clean the targets [read: Glue job bookmarks, above].
- Snowflake keeps load metadata per table for 64 days; after that, whether an older file was loaded is uncertain, and such files are skipped by default [read: https://docs.snowflake.com/en/user-guide/data-load-considerations-load]. Snowpipe keeps 14 days of history per pipe and "ignores any staged file whose path and name match a file in this record", including when the file has changed; after the record expires, a re-staged file with the same name can load again and duplicate data [read: Snowpipe intro, above].
- Singer and Meltano keep a STATE of bookmarks per stream; a target passes a state message on "once all data that appeared in the stream before the state message has been processed" [read: https://hub.meltano.com/singer/spec/]. Meltano stores state in its system database by default, or in a file, S3 (including S3-compatible stores), Azure, GCS or Snowflake, keyed by `state_id`, with a lock per `state_id` [read: https://docs.meltano.com/concepts/state_backends].
- Trade-offs: the checkpoint belongs to one reader and one engine; it is opaque (RocksDB, a service database); and the expiry windows above show what happens when the record of "already loaded" is kept for less time than the files exist [inference].

**D. A load log as a table, joined to the loaded rows.**
- dlt writes one row per load to `_dlt_loads` (`load_id`, `schema_name`, `status`, `inserted_at`, `schema_version_hash`), where status 0 means the load completed, and stamps every loaded row with `_dlt_load_id`. Rows whose load id is not in `_dlt_loads` are "not yet completed" and are filtered out by a join [read: https://dlthub.com/docs/general-usage/destination-tables]. dlt also stores its incremental state in a `_dlt_pipeline_state` table in the destination, and restores it from there on a clean start [read: https://dlthub.com/docs/general-usage/state].
- AWS models the arrivals in a bucket as Iceberg tables: S3 Metadata's journal table records object creates, updates and deletes "in near real time", and its live inventory table lists every object and version; both are read-only Iceberg tables queryable by any Iceberg engine [read: https://docs.aws.amazon.com/AmazonS3/latest/userguide/metadata-tables-overview.html]. That is a managed AWS feature, not something SeaweedFS has, but it is a first-party precedent for "the arrival manifest is an Iceberg table".
- Trade-offs: the log is queryable by every engine and backed up with the tables; "unprocessed" becomes a set difference, which does not depend on arrival order. Someone must write the row, and the object write and the row write are two steps that a crash can split [inference].

**E. Iceberg snapshots as the cursor between table steps.**
- Spark reads "appended data incrementally" between `start-snapshot-id` (exclusive) and `end-snapshot-id` (inclusive). It "Currently gets only the data from `append` operation. Cannot support `replace`, `overwrite`, `delete` operations", and it is not available in Spark SQL syntax [read: `docs/docs/spark-queries.md`, https://iceberg.apache.org/docs/latest/spark-queries/#incremental-read].
- Spark structured streaming reads only append snapshots; an overwrite or delete snapshot raises an exception unless `streaming-skip-overwrite-snapshots` or `streaming-skip-delete-snapshots` is set [read: https://iceberg.apache.org/docs/latest/spark-structured-streaming/].
- PyIceberg 0.12.0 has `incremental_append_scan`, which "returns the rows added by append snapshots" between two snapshot ids; it is not in 0.11.0 [read: `pyiceberg/table/__init__.py` at tags `pyiceberg-0.12.0` and `pyiceberg-0.11.0` in https://github.com/apache/iceberg-python].
- Each snapshot's summary records `operation` (`append`, `replace`, `overwrite`, `delete`) and optional counts such as `added-records` and `added-data-files` [read: https://iceberg.apache.org/spec/#snapshots and appendix F]. Writers can add their own summary entries: Spark through the `snapshot-property.<key>` write option or the `spark.sql.iceberg.snapshot-property.<key>` session setting [read: https://iceberg.apache.org/docs/latest/spark-configuration/]; PyIceberg through the `snapshot_properties` argument of `append`, `overwrite`, `upsert` and `add_files` [read: PyIceberg 0.12.0 source, above]. A bronze commit can therefore record which run, or which range of raw objects, it covered.
- In format v3, every row carries `_row_id` and `_last_updated_sequence_number`, maintained by the engine [read: https://iceberg.apache.org/spec/#row-lineage].
- Trade-offs: no separate state for steps after bronze; the cursor is a snapshot id the reader stores. It works only while the table is append-only, and only while the starting snapshot has not been expired by maintenance [inference: snapshot expiry removes snapshots; not tested here]. It does not cover raw to bronze, because raw is not a table.

**F. Partitions as the unit of work.**
- Dagster partitions an asset by time window, static list, two dimensions, or a dynamic set that sensors can add to at run time; a successfully materialized partition is shown as done [read: https://dagster.io/docs/guides/build/partitions-and-backfills/partitioning-assets]. A sensor's `run_key` makes Dagster skip a request it has seen before [read: https://dagster.io/docs/guides/automate/sensors].
- Glue recommends a catalog table with partitions for bookmarks [read: Glue job bookmarks, above].
- Trade-offs: "what is unprocessed" is "which partitions are not materialized", which fits "process day D" and "re-process game G" well, and fits an orchestrator later. It needs a partition scheme fixed up front and a place that records the status per partition; outside Dagster that place is one of A to D [inference].

### 2.2 Where the manifest lives

The owner has named three candidates; the sources line up as follows.

| Manifest as | Precedent | For | Against |
|---|---|---|---|
| **An Iceberg table** | S3 Metadata journal and inventory tables (AWS); dlt's `_dlt_loads` in the destination | Every engine reads it; same backup and catalog as the tables; snapshot history of the manifest itself | Each commit is a snapshot plus files; one commit per response would make many tiny files and snapshots, so rows are better written per run or batch [inference]. Raw is not a table layer here, so the table needs a home namespace (bronze? a separate one?) [inference from ADR 0003's layer layout]. |
| **Object listings only** (no manifest) | Auto Loader directory listing; Glue bookmarks; Dagster's file sensor | Nothing extra to keep consistent; the store is the truth | Only key, size, ETag and last-modified per object; fetch facts must be in the key or fetched by `HEAD`; no record of fetches that produced no object (failures, if those are not stored) [inference]. |
| **A database table** (Postgres) | Meltano's default system database; Glue's service database; Snowflake's internal load metadata | Cheap per-row transactional writes; locking | A second system of record outside the lakehouse, not readable by the engines without a connector, and one more thing to back up (`docs/data-layer.md` item 2 already plans a `pg_dump` of the catalog database) [inference]. |

Who writes the arrival row is a separate choice [inference]:
- **The fetcher, at write time** (dlt, Singer, Airbyte all stamp extraction facts as they extract). The row carries fetch facts nobody else knows (request, status, timing). A crash between object and row leaves an object with no row, so a reconciling listing is still needed.
- **A discovery step, after the fact** (Auto Loader, Glue, Snowpipe). It sees every object, including ones written by hand or imported from `M:`, but only knows what the store knows.

### 2.3 Watermark or set difference

- A **watermark** (largest fetch time, last-modified time, or key seen) is what Glue bookmarks, the Dagster sensor example and Singer bookmarks use. It is small state, but every source in this research warns that arrival order is not guaranteed: S3 events are unordered, Auto Loader does not guarantee discovery order, Snowpipe "doesn't guarantee that files load in the order that they were staged" [read: pages above]. dlt's incremental cursor handles the boundary by making it inclusive and deduplicating records at the boundary by content hash or primary key [read: https://dlthub.com/docs/general-usage/incremental/cursor].
- A **set difference** (objects in the manifest whose key and version are not yet in bronze's lineage columns) is what dlt's `_dlt_loads` join and Auto Loader's per-file checkpoint amount to. It is immune to order, and it makes "re-process object X" a delete of one record, but it needs bronze to carry the raw key, or a processed-objects record, to subtract [inference].

### 2.4 By shape

- **Shape 1 (re-fetched ESPN data)**: every fetch of the same URL is a separate arrival. Tools that key their "already loaded" record by file name (Snowpipe, Auto Loader without `allowOverwrites`) would skip a re-fetch written to the same key; tools keyed by modification time (Glue) would pick it up [read: pages above]. A fetch-unique key, or tracking by version id, avoids depending on either behaviour [inference]. The content hash (ADR 0001) tells a changed re-fetch from an identical one.
- **Shape 2 (third-party files)**: the publisher can replace a file under the same name. The arrival record needs the publisher's name for the file, the download time and a hash, so that "the publisher changed this file" is detectable [inference]. Snowpipe's behaviour (ignore same-name changed files for 14 days) is the failure this guards against.
- **Shape 3 (price history)**: two cursors exist. The fetch cursor is the source's own time or sequence, the Singer/dlt bookmark pattern, with boundary deduplication because pages overlap. The arrival record can be per batch or per run rather than per page [inference].

---

## Step 3: Shaping bronze

### 3.1 How the payload is held

| Option | Who documents it | How it works | Trade-offs here |
|---|---|---|---|
| **Whole payload as a string** | Databricks medallion guidance: bronze "storing most fields as string, VARIANT, or binary to protect against unexpected schema changes" (source's position) [read: https://docs.databricks.com/aws/en/lakehouse/medallion]. Auto Loader infers every JSON column, nested fields included, as a string by default [read: https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/schema]. Airbyte's raw tables held each record as a JSON blob [read: https://docs.airbyte.com/platform/using-airbyte/core-concepts/typing-deduping; the column name `_airbyte_data` is from memory]. | One row per response or record, body in one string column. | Never fails on drift; every engine reads strings. Every query parses JSON; bronze then adds little over raw beyond being a table [inference]. Works for HTML as well as JSON. |
| **Whole payload as a variant** | Iceberg v3 adds the `variant` type, encoded as in Parquet [read: https://iceberg.apache.org/spec/ (primitive types)]. Databricks loads each record into one variant column with `singleVariantColumn`; variant "is flexible to schema and type changes" and keeps case and nulls; it cannot hold malformed records, holds at most 16 MB per record (on Databricks), and Databricks recommends "extracting frequently queried fields and storing them as non-variant columns" [read: https://docs.databricks.com/aws/en/ingestion/variant]. | Semi-structured value with typed access paths. | Needs format v3 tables and support in every engine used here; whether PyIceberg 0.12.0, Spark 4.1 with Iceberg 1.12.0, Trino 484 and DuckDB 2.0.0 can all write and read a v3 `variant` against Polaris 1.8.0 was **not checked** and would need a probe. Not for HTML. |
| **Typed columns, source's own names, schema evolving** | Auto Loader schema evolution modes: `addNewColumns` (stream stops, schema gains the column, restart continues), `rescue` (schema never evolves, unknown data goes to the rescued column), `failOnNewColumns`, `none` [read: Auto Loader schema page, above]. Airbyte's direct loads keep "a single destination table" per stream, typed [read: typing-deduping page, above]. | Parse into columns at load. | Queryable at once, and `docs/data-layer.md` already says bronze keeps the source's field names. Drift has to be handled at load time: new fields, type changes, renamed fields. That overlaps the bad-data area of issue #1. |
| **Typed columns plus a catch-all** | Auto Loader's `_rescued_data` column holds, as a JSON blob, fields missing from the schema, type mismatches and case mismatches, plus "the source file path of the record" [read: Auto Loader schema page, above]. Airbyte nulls a value it cannot type and records the change in `_airbyte_meta.changes` [read: Airbyte metadata fields and typing-deduping pages, above]. | Typed where the schema fits, the rest kept aside instead of failing or dropping. | Nothing is lost and the load does not stop; but a value can be in two places over time (rescued before the schema caught up, typed after) [inference]. |

**Third-party files and `add_files`.** Both Spark's `add_files` procedure and PyIceberg's `add_files` register existing Parquet files as table data without rewriting them. Spark warns "Schema is not validated" and "Files added by this method can be physically deleted by Iceberg operations"; PyIceberg warns that maintenance such as expiring snapshots will remove them [read: `docs/docs/spark-procedures.md`; PyIceberg `mkdocs/docs/api.md`]. Registering raw's files this way would hand raw objects to table maintenance, and the catalog's allowed locations do not include `statcave-raw` (ADR 0003), so it would be refused anyway [inference]. Copying the published file's rows into bronze keeps raw independent. CSV files cannot be registered this way at all; `add_files` reads Parquet (and in Spark also ORC and Avro sources) [read: Spark procedures page; memory for the ORC/Avro detail].

### 3.2 Lineage columns that bronze rows carry

What each tool stamps on a loaded row:

| Tool | Columns | Source |
|---|---|---|
| Databricks | `_metadata`: `file_path`, `file_name`, `file_size`, `file_modification_time`, `file_block_start`, `file_block_length`. The medallion page names `_metadata.file_name` as an example provenance column for bronze. | [read: https://docs.databricks.com/aws/en/ingestion/file-metadata-column; medallion page] |
| Snowflake | `METADATA$FILENAME` (full path), `METADATA$FILE_ROW_NUMBER`, `METADATA$FILE_CONTENT_KEY` (checksum), `METADATA$FILE_LAST_MODIFIED`, `METADATA$START_SCAN_TIME`; `COPY INTO` can load them into columns. | [read: https://docs.snowflake.com/en/user-guide/querying-metadata] |
| dlt | `_dlt_load_id` (the load package), `_dlt_id` (unique row key) | [read: https://dlthub.com/docs/general-usage/destination-tables] |
| Airbyte | `_airbyte_raw_id`, `_airbyte_extracted_at`, `_airbyte_loaded_at`, `_airbyte_meta` (sync id and changes), `_airbyte_generation_id` (incremented per refresh) | [read: https://docs.airbyte.com/platform/understanding-airbyte/airbyte-metadata-fields] |
| Iceberg v3 | `_row_id`, `_last_updated_sequence_number`, assigned by the engine, not written by the loader | [read: https://iceberg.apache.org/spec/#row-lineage] |

The candidates for this repo, as a union of the above [inference]: raw key; raw version id; content hash of the raw object; fetch time (and, for shape 3, the source's own timestamp, which is a data column, not lineage); position of the row within the raw object (Snowflake's file row number); the run or load id; bronze load time; parser version. The raw key plus version id is what makes the set-difference tracking in 2.3 possible.

### 3.3 Append-only and re-parsing

- Iceberg's `append` operation means "only data files were added and no files were removed" [read: https://iceberg.apache.org/spec/#snapshots]. Incremental reads in Spark and PyIceberg, and Spark streaming reads, follow appends only (2.1 E). Keeping bronze strictly append-only is what keeps those reads usable for the step after it [inference].
- When a parser is fixed and raw is parsed again (ADR 0001: "a parser fix never needs a re-fetch"), the options are [inference]: append the re-parsed rows with a new parser version and let silver choose; overwrite the rows of the affected raw objects, which produces `overwrite` snapshots that incremental and streaming readers refuse or skip; or build a new bronze table and swap. The sources do not prescribe one.
- Bronze validation is minimal in Databricks' guidance: "Minimal data validation is performed in the bronze layer", and bronze is "not for access by analysts and data scientists" (source's position) [read: medallion page].

### 3.4 By shape

- **Shape 1**: nested JSON, re-fetched. A whole-payload column (string or variant) plus a few extracted keys (game id, fetch time) is the pattern Databricks describes; typed columns face drift across seasons. HTML cannot go into a variant and needs either a string column or parsing at load [inference].
- **Shape 2**: CSV or Parquet with a published schema; typed columns that match the file are the natural fit, with the file's own name and hash as lineage [inference].
- **Shape 3**: narrow, high-volume, stable records; typed columns, partitioned by time; one raw batch object becomes many rows, so row position within the object matters as lineage [inference].

---

## Decisions the owner will have to make, with the options

**Raw contract**

1. **Key per fetch or key per thing.** (a) fetch time in the key, a new key every fetch, written with `If-None-Match`; (b) stable key per thing, history held by bucket versioning; (c) per shape: (a) for shapes 1 and 3, the publisher's file name plus download time for shape 2.
2. **Key order.** Entity first (`source/dataset/<ids>/<fetch time>`), arrival time first (Firehose style, sources' default for streams), or load id (dlt style). Timestamp format in keys: basic ISO without colons, or encoded.
3. **Where fetch facts live.** Key; S3 user metadata (2 KB, immutable, `HEAD` to read); an envelope around the body (WARC style); a sidecar object; the manifest only; or a combination, for example the key plus the manifest.
4. **Which facts.** From the union in 1.2: request (URL, parameters, relevant headers), fetch start and end, status, response headers, size, hash, version id, run id, fetcher version.
5. **One object per response or batched.** Per response for all; batched per run or time window for shape 3; files as published for shape 2.
6. **Failed responses.** Keep in raw with their status; keep under a separate prefix (Firehose style); record in the manifest only; or drop.
7. **Identical re-fetches.** Store again; store a pointer (WARC `revisit` style); record only in the manifest.
8. **Compression.** As received; decoded; gzip at rest.

**Tracking**

9. **What records an arrival.** The fetcher writes the row at fetch time; a discovery step lists and records; or both, with listing as reconciliation.
10. **Where the manifest lives.** An Iceberg table (and in which namespace, since raw is not a table layer); object listings only; a Postgres table.
11. **How raw to bronze knows what is new.** Set difference between manifest and bronze lineage; a watermark on fetch time or key with `StartAfter`; a per-reader checkpoint.
12. **How bronze to silver knows what is new.** Iceberg snapshot ids with incremental append reads (requires append-only bronze and snapshot retention); the same set difference on lineage columns; partitions marked done.
13. **Commit granularity of the manifest and of bronze.** Per response, per batch, per run.

**Bronze**

14. **Payload shape.** Whole payload as string; as variant (needs a v3 probe across all four engines first); typed columns; typed plus a rescued catch-all. Possibly different per shape.
15. **Drift handling.** Fail; evolve the schema and restart; rescue into a catch-all; null and note (Airbyte style). Shared with the bad-data area of the map.
16. **Lineage columns.** From the list in 3.2; at minimum, the sources agree on a pointer back to the source file (Databricks, Snowflake) and a load or run id (dlt, Airbyte).
17. **Re-parsing after a parser fix.** Append with parser version; overwrite by raw object; rebuild the table.
18. **Iceberg format version for bronze.** v2, or v3 for `variant` and engine-maintained row lineage, subject to the engine check.

Where the sources lean (their positions, not a recommendation): Databricks favours keeping bronze fields as string or variant with provenance columns, and file notifications over listing; Snowflake and AWS favour date-structured paths in landing storage, and AWS favours versioning the raw bucket; dlt, Airbyte, Singer and WARC all record an extraction or capture time per record; dlt keeps its load log and state as tables in the destination; AWS itself publishes bucket arrivals as Iceberg tables.

## Not checked, or could not be read

- AWS Prescriptive Guidance pages on data-lake layers and raw-layer naming returned 403 or 404 to direct fetches; their content above is from search summaries only.
- SeaweedFS: `StartAfter` behaviour in listings, enforcement of conditional writes by policy, and Object Lock were not checked. Its lack of S3 event notifications is from its own wiki's API table.
- Iceberg v3 `variant` support in PyIceberg 0.12.0, Spark 4.1.4 with Iceberg 1.12.0, Trino 484 and DuckDB 2.0.0, against Polaris 1.8.0, was not checked.
- How snapshot expiration interacts with a stored start snapshot id for incremental reads was not tested; the statement above is inference.
- dlt's behaviour on a failed run (whether state is committed) was not on the pages read.
- Airbyte's `_airbyte_data` column name is from memory.
- Dagster's backfill mechanics were not read.
