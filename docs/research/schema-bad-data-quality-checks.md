# Schema enforcement, bad data and quality checks: documented practice

Written 2026-11-18 for issue #5. Research only: it lists options and trade-offs from documented practice and makes no decision. The owner decides later which option this repo takes.

The question: how established pipelines declare and enforce schemas at each layer (bronze, silver, gold, as defined in `docs/data-layer.md`), what they do with data that does not match, which quality checks they run, where those checks run and what a failure stops, how write-audit-publish works on Iceberg, and what each load records so that quality and freshness can be measured later. The sources here are undocumented third-party APIs, so drift in response shape is expected.

## How this was read

- Each claim carries a source tag such as [S4]. The tags are listed in [Sources](#sources) with the URL and how the source was read:
  - **raw**: the source file itself (documentation Markdown or code), downloaded and read in full or searched with grep.
  - **fetch**: the published page, read through a fetch tool that returns extracts and quotes rather than the whole page. Quotes from these pages are as the tool returned them.
  - **gh**: read with the GitHub CLI (pull requests).
- Statements marked **(inference)** are my reasoning from the cited sources, not something a source says. Statements marked **(memory)** come from prior knowledge and were not checked here.
- Versions matter. The Iceberg documentation and Spark integration code were read on the `main` branch, which can be ahead of the Iceberg 1.12.0 jars this stack used on 2026-11-12. PyIceberg was read at tag `pyiceberg-0.12.0`, the version this stack uses, and its docs also at `main`. The Trino docs were read on `master`, which is newer than Trino 484. All reading was done on 2026-11-18.

## Summary

- **Schemas by layer.** The documented pattern is to keep bronze loose and make silver and gold strict. Databricks recommends storing most bronze fields as string, VARIANT or binary "to protect against unexpected schema changes", with minimal validation [S14]. Its Auto Loader reads JSON columns as strings by default and puts anything unparsed into a `_rescued_data` column [S17]. Silver is where "schema enforcement", "data quality checks and enforcement", deduplication and quarantining invalid records happen [S14]. Gold is shaped for consumers. dbt's model contracts (declared column names and types, a build that fails when they don't match, breaking changes handled through versions) are the clearest documented model of a consumer-facing schema [S20].
- **Iceberg sets the limits on how a schema can change.** Columns are tracked by ID. Add, drop, rename, reorder and widen are changes to metadata only [S1]. The only type changes allowed are int→long, float→double, a wider decimal precision, and in v3 also date→timestamp and unknown→any [S2]. A source that switches a field from a number to a string therefore cannot be absorbed by evolving an existing numeric column (inference). Iceberg does not enforce row uniqueness: identifier fields are "not guaranteed or required" [S2].
- **When data does not match, there are six documented responses:** fail the load, drop rows, quarantine rows, load and flag (warn), keep unknown fields in a catch-all column, and evolve the schema automatically. Every major tool offers several of them and chooses per check, not per pipeline (Databricks [S15], dbt [S22], Glue [S32], Netflix [S47]).
- **Quality checks** fall into the same categories everywhere: completeness, uniqueness, validity, referential integrity, volume, freshness, schema, reconciliation against another dataset, and distribution or anomaly checks. They run in three places: on the data in flight before a write, on an unpublished write (write-audit-publish), or after publishing, where a failure blocks downstream steps or only raises an alert.
- **Write-audit-publish (WAP) on Iceberg:** write to a branch, check the branch, then fast-forward `main` to it [S3]. Spark supports this fully [S4, S7, S8]. PyIceberg 0.12.0 can write to a branch and move `main` with a guarded ref update, but `fast_forward_branch` appears only in docs newer than 0.12.0 [S9, S10]. Trino can read a branch but cannot write to one; that support is an open pull request [S11, S12], and Trino currently reads a branch with the wrong schema after a schema change [S13]. On a small stack the costs are a few extra commits per load, cleanup of failed branches, and one caveat: the schema is shared across branches, so a schema change is not held back until publish [S3].
- **What a load records** across the sources: a run identifier and timings; counts in, out, rejected and passed per rule; the result of each check; the snapshot produced; and source freshness as the newest load timestamp against the time of the check (dbt [S28, S29], Glue [S31, S32], Databricks [S15], Iceberg snapshot summaries [S2, S5, S9]). Comparisons over time, such as "row count above the average of the last k runs", need that history kept [S31].

## 1. Schemas by layer

### 1.1 Where schemas are declared

| Where | Examples | Who documents it |
|---|---|---|
| Inferred from the data, then stored | Auto Loader infers a schema and tracks it at a schema location; for JSON, "Auto Loader infers all columns as strings (including nested fields in JSON files)" unless `cloudFiles.inferColumnTypes` is set; schema hints enforce the parts you know | Databricks [S17] |
| Table DDL in the catalog | `CREATE TABLE` / `ALTER TABLE`; Iceberg keeps every schema version with its own ID [S2] | Iceberg [S1, S2]; Delta "schema enforcement" on write [S18] |
| Code-level models | Pandera `DataFrameModel` classes ("heavily inspired by pydantic"), with pandas and polars backends among others [S41, S44]; Pydantic models for record-level JSON, where `extra` decides what happens to unknown keys [S45] | Pandera, Pydantic |
| Contract files beside the code | dbt model contracts in YAML: every column's `name` and `data_type`, plus constraints [S20]; Soda data contracts ("a YAML document that contains data quality checks", one `dataset:` per contract) [S39]; the Open Data Contract Standard, a YAML contract with Schema, Data Quality and Service-Level Agreement sections [S48] | dbt, Soda, Bitol |

dbt contracts do not apply to sources, seeds or snapshots, and apply to views without constraints [S20]. In dbt terms, the input side (bronze) is checked with data tests and freshness, not with contracts.

### 1.2 How each layer is treated

**Bronze (raw parsed one-to-one).**
- Databricks: bronze "contains and maintains the raw state of the data source in its original formats", is "appended incrementally and grows over time", gets "minimal data validation", and the recommendation is to store "most fields as string, VARIANT, or binary to protect against unexpected schema changes" [S14].
- Auto Loader's default reading of JSON as strings, its rescued data column, and its evolution modes (section 2) are the documented way to keep a bronze load running through source drift [S17].
- Spark's own JSON reader offers the same choice at parse time: `PERMISSIVE` (the default) keeps the row, sets malformed fields to null and puts the malformed text into `columnNameOfCorruptRecord`; `DROPMALFORMED` drops the record; `FAILFAST` throws [S19].
- How change is allowed: added columns are the normal case (Auto Loader `addNewColumns`) [S17]. Iceberg handles added columns as a metadata-only change [S1].
- (inference) In this repo raw is kept as received (ADR 0001 [S49]), so a bronze table that drops or stringifies a field loses nothing for good. A parser fix can re-read raw. What raw alone does not provide is *detection*: something has to notice that the shape changed.

**Silver (cleaned, deduplicated, conformed).**
- Databricks lists "schema enforcement", "schema evolution", "data quality checks and enforcement" and "data deduplication" for silver, and its example cleans silver by "dropping nulls and quarantining invalid records" [S14].
- The schema is declared explicitly: DDL, a code model or a contract (1.1). Changes are deliberate: an explicit `ALTER TABLE`, or controlled automatic evolution such as dbt's `on_schema_change: fail` / `append_new_columns` for incremental models [S27] or Delta/Iceberg `mergeSchema` [S4, S18].
- Iceberg does not enforce uniqueness ([S2], identifier fields), so deduplication and key uniqueness in silver are checks run by the pipeline, not table constraints.

**Gold (consumer-shaped).**
- Databricks describes gold only as "aggregated data tailored for analytics and reporting" and says nothing about its schema [S14].
- dbt's contracts are aimed at this layer. A contract defines the "shape" of the result, and the model "does not build" if it does not conform [S20]. Breaking changes are named: removing a column, changing a `data_type`, removing or modifying a constraint, and removing a contracted model. For versioned models these raise an error [S20]. dbt recommends unit tests in particular for "public, contracted models or models directly upstream of an exposure" [S25].
- A vocabulary for "how change is allowed" toward consumers comes from schema registries: BACKWARD (new readers read old data: add optional fields, remove fields), FORWARD (old readers read new data), FULL (both: add or remove optional fields only), NONE [S46]. Confluent's default is BACKWARD [S46]. (inference) The same three words can describe what a gold table promises to the repos that read it.

### 1.3 Iceberg rules that apply to every layer

- **Allowed changes:** add, drop, rename, update (widen), reorder, including in nested structs. All are metadata changes, and "no data files need to be rewritten" [S1]. Columns are tracked by unique IDs, so a reused name never brings back old data [S1].
- **Type promotions** [S2]:

  | From | v1, v2 | v3+ |
  |---|---|---|
  | `int` | `long` | `long` |
  | `float` | `double` | `double` |
  | `decimal(P,S)` | `decimal(P',S)`, P' > P | same |
  | `date` | none | `timestamp`, `timestamp_ns` (not the tz types) |
  | `unknown` | none | any type |

  Nothing else is allowed: no number→string, no narrowing, no primitive→struct, and no regrouping of fields between structs [S2]. Type promotion is also restricted on partition source columns when the transform would produce a different value [S2].
- **Required fields and defaults.** Default values (`initial-default`, `write-default`) are a v3 feature [S2]. "When a required field is added, both defaults must be set to a non-null value", and a writer must fail when a required field has no write default or it evaluates to null [S2]. (inference) On a v2 table, a required column can be declared at create time but not added later. A column the source may stop sending is safer as optional.
- **Branches share one schema.** "The schema tracked for a table is valid across all branches", and a branch write is validated against the table's current schema [S3].
- **Not enforced: uniqueness.** "Uniqueness of rows by this identifier is not guaranteed or required by Iceberg and it is the responsibility of processing engines or data providers to enforce" [S2].
- **Coming in v4: constraints.** The spec on `main` defines table constraints for v4: `check`, `unique` and `primary-key`, each with an `enforced` flag and a per-snapshot status (`validated`, `valid`, `invalid`) [S2]. "Constraints are added in v4 and are not supported in v3 or earlier" [S2]. Not checked: whether any engine on this stack implements v4. (inference) Nothing can rely on it for now.
- **`variant` type (v3).** Semi-structured values with fields that vary by row [S2]. Trino documents `VARIANT` for v3 tables [S11]. Not confirmed: PyIceberg 0.12.0's Arrow conversion code has no variant handling (grep of `pyiceberg/io/pyarrow.py` [S10]), and Spark's variant support with this Iceberg version was not checked.

### 1.4 What the engines check on write

- **PyIceberg 0.12.0** checks the Arrow schema of every append and overwrite against the table schema [S10]:
  - a column the table lacks raises `ValueError` with the hint "Update the schema first (hint, use union_by_name)";
  - a `required` table field must be present and non-nullable in the Arrow schema;
  - an optional field may be missing;
  - a type must be equal or promotable.

  This checks the declared schema, not the values. Value checks are left to the caller (inference). `union_by_name` merges a new schema into the table without the caller handling field IDs [S9].
- **Spark (Iceberg runtime).** `spark.sql.iceberg.check-nullability` ("Validate that the write schema's nullability matches the table's nullability") and `check-ordering` are both on by default [S5]. Automatic evolution needs both the table property `write.spark.accept-any-schema=true` and the write option `mergeSchema`. New source columns are then added with nulls in existing rows, and a target column missing from the source is set to null on insert [S4]. The session setting `spark.sql.iceberg.merge-schema` "only adds missing columns" [S5].
- **Delta (for comparison).** "All inserted columns must exist in the target table" and data types must match, with safe casts [S18].

## 2. When data does not match

| Option | What happens | Documented by | For | Against |
|---|---|---|---|---|
| **Fail the load** | Nothing from the batch is published; someone has to act | Databricks `expect_or_fail`: "stop execution immediately"; a table update "atomically rolls back"; "manual intervention is required before reprocessing" [S15]. Auto Loader `failOnNewColumns`, and `addNewColumns`, which fails once and then restarts with the new schema [S17]. Spark `FAILFAST` [S19]. dbt contracts and `on_schema_change: fail` [S20, S27]. Glue "Fail job without loading to target data" [S32]. PyIceberg's default on extra columns [S10]. Netflix: row-count checks set to "fail job" [S47] | Bad data never reaches readers; the cause is found at once | One bad row holds back every good row, freshness suffers, and someone has to step in. Databricks records no metrics for a failed expectation [S15] |
| **Drop rows** | Invalid rows are removed and counted | Databricks `expect_or_drop`: "the count of dropped records is logged" [S15]. Spark `DROPMALFORMED` [S19]. Pandera `drop_invalid_rows=True` (requires `lazy=True`) [S42] | The table stays current for the valid rows | The dropped rows are gone unless kept elsewhere; data goes missing quietly behind a count |
| **Quarantine rows** | Invalid rows go to a separate table or carry a flag, kept for inspection and reprocessing | Databricks quarantine pattern: a flag column marks rows that fail any rule, a private table holds every row, and two views split valid from quarantined [S16]. Medallion silver example [S14]. Glue row-level outcomes (`DataQualityRulesPass`, `DataQualityRulesFail`, `DataQualityRulesSkip`, `DataQualityEvaluationResult`) for routing records [S32]. dbt `store_failures` saves failing records to an audit schema, replaced on every run [S26]. Pandera `failure_cases` [S43] | Keeps the evidence; good rows flow; bad rows can be replayed after a fix | Another table and a process to drain it; readers must never read it as data; dataset-level rules (row count, aggregates) cannot point at rows [S32] |
| **Load and flag (warn)** | All rows are written; the check is recorded as a warning | Databricks `expect` (the default): "invalid records are written to the target", with metrics [S15]. dbt `severity: warn` with `warn_if`/`error_if` thresholds [S22]. Glue "On ruleset failure: None" (the default) [S32]. Soda `level: warn` [S39]. Netflix null-count check set to "warn job" [S47] | Freshest data; suits soft rules and thresholds | Readers get the bad rows unless a flag column exists; warnings are easy to ignore |
| **Catch-all column** | Fields that are unknown or don't parse are kept in one column instead of being lost or failing the load | Databricks `_rescued_data`: data "that isn't parsed" because the column is missing from the schema, the type doesn't match or the case doesn't match, stored as a JSON blob with the source file path [S17]. Spark `columnNameOfCorruptRecord` [S19]. Pydantic `extra='allow'` stores unknown keys in `__pydantic_extra__` (`'ignore'` is the default, `'forbid'` rejects) [S45]. Pandera `strict=True` rejects extra columns; `strict='filter'` drops them [S41]. Iceberg v3 `variant` as a typed home for semi-structured data [S2] | No field is ever lost; drift shows up as non-null catch-all values that can be counted and alerted on | A semi-structured column to carry and watch; a type mismatch that lands there quietly nulls the typed column |
| **Evolve the schema automatically** | New columns are added to the table on write | Auto Loader `addNewColumns` [S17]. Iceberg Spark `mergeSchema` [S4]. PyIceberg `union_by_name` [S9]. dbt `append_new_columns` / `sync_all_columns` [S27]. Delta `mergeSchema` [S18] | Additive drift never stops a load | Types of new columns are guessed; in silver or gold, additions reach readers unannounced; type changes outside Iceberg's promotion rules still fail (1.3); dbt does not backfill old rows [S27] |

What the sources say about combining them:
- Severity is set per check, not per pipeline. Netflix set row-count checks to fail and a null-count check to warn [S47]. dbt [S22], Databricks [S15] and Glue [S32] all mix fail, drop or quarantine, and warn in one pipeline.
- Databricks places the loose options (strings, rescue) in bronze and enforcement plus quarantine in silver [S14, S17].
- The AWS Analytics Lens leaves the choice to the business: "it's usually more efficient to impute missing values, but in other cases it's more efficient to block processing until the data quality issue can be resolved at source" [S35].

(inference) How the kinds of drift an undocumented API can show line up with these options:

| Drift | Detected by | Handled by |
|---|---|---|
| New field | a non-null catch-all column, or a schema diff at parse time | ignore (raw keeps it), add the column automatically, or fail |
| Field missing | a required-column check (Soda `when required column missing`, Glue `ColumnExists`) | null in an optional column, or fail if the field is required |
| Type change | a parse failure | a catch-all column, a string column in bronze, or fail. Iceberg cannot change the column type outside the promotion rules |
| Null where there never was one | a completeness check | warn, quarantine or fail, by threshold |
| Same shape, different meaning (units, ids) | validity, distribution or reconciliation checks | quarantine or fail |

## 3. Quality checks

### 3.1 Categories and which tool names them

| Category | dbt [S21] | Glue DQDL [S31] | Soda [S39] | Other |
|---|---|---|---|---|
| Completeness | `not_null` | `IsComplete`, `Completeness` (ratio) | missing | AWS Lens names completeness, accuracy and uniqueness as the "critical attributes" [S35] |
| Uniqueness / keys | `unique` | `IsUnique`, `IsPrimaryKey`, `Uniqueness` | duplicate | Databricks primary-key uniqueness pattern [S16]; Iceberg does not enforce it [S2] |
| Validity | `accepted_values` | `ColumnValues`, `ColumnDataType`, `ColumnLength` (with thresholds) | invalid | Pandera `Check`s [S41] |
| Referential integrity | `relationships` | `ReferentialIntegrity` | | |
| Volume | | `RowCount`, including dynamic `RowCount > avg(last(5))` | row count | Netflix "RowCount >= prior value" [S47]; Databricks row-count equality between tables, "no rows dropped" [S16] |
| Freshness | `freshness` with `warn_after` / `error_after` on `loaded_at_field` [S24] | `DataFreshness "col" <= 24 hours` [S33] | freshness | AWS Lens: "alert when new data has not arrived or become available within the expected time" [S36] |
| Schema | model contracts [S20] | `ColumnCount`, `ColumnExists`, `SchemaMatch` (against a reference dataset) [S34] | schema check; by default, extra columns are not allowed [S39] | Soda v3 `when schema changes` (seen only in a search snippet, [S40]) |
| Reconciliation with a second dataset | | `DatasetMatch`, `RowCountMatch`, `AggregateMatch`, `ReferentialIntegrity`, using reference datasets with aliases [S31, S32] | Reconciliation (listed) [S39] | ADR 0001 plans "agreement with a second source" as a gate [S49] |
| Distribution / anomaly | | analyzers, `Distribution`, `DetectAnomalies`; observations [S31] | | Netflix null count against a normal distribution [S47] |
| Logic (not data) | unit tests on static fixtures, "development or CI environments" only [S25] | | | |

### 3.2 Where checks run

- **In flight, before the write.** Glue ETL checks are "proactive" and "help you identify and filter out bad data *before* you load a data set into your data lake" [S30]. Databricks expectations run inside the pipeline update [S15]. Pandera validates a dataframe in the process [S41]. The AWS Lens says to put a check stage "in the ETL pipeline as early as possible" to stop bad data "propagating downstream" [S37].
- **On the written but unpublished data (WAP).** The data is checked where it will live, so a check can compare it with the existing table and with earlier loads [S3, S9, S47]. See section 4.
- **After the build, blocking downstream steps.** In `dbt build`, "tests on upstream resources will block downstream resources from running", and a failing test makes the downstream models `SKIP`. `severity: warn` avoids the skip [S23].
- **At rest, on a schedule.** Glue Data Quality on Data Catalog tables runs on a schedule or when table metadata changes, for data governance "on an ongoing basis" [S30].
- **Logic tests** are separate: dbt runs unit tests on fixtures in development and CI, not in production [S25].

### 3.3 What a failure stops

- dbt: the failing node's downstream nodes skip; warnings do not block; `--warn-error` turns warnings into errors [S22, S23].
- Databricks: `fail` rolls back that update atomically. "In a triggered pipeline, the failure of a single flow does not cause other parallel flows to fail" [S15].
- Glue ETL: three choices. "None" (default; the job runs on), "Fail job after loading data to target" ("the job fails and no data is saved"), or "Fail job without loading to target data" [S32].
- WAP: a failed audit skips the publish. "The audit branch (and its data files) can then be inspected, rewritten, or removed ... without ever having polluted the data on `main`" [S9].
- Netflix: each check is configured as fail job or warn job [S47].

## 4. Write-audit-publish on Iceberg

### 4.1 Origin

Netflix described the pattern in "Whoops, the Numbers Are Wrong! Scaling Data Quality @ Netflix" (Michelle Ufford, Hadoop Summit 2017) [S47]:
1. Prep: create an audit table next to the target.
2. Write: write the new partition to the audit table, not to production.
3. Audit: run the checks against it, comparing it with the prior partition.
4. Publish: move it into the production table and set a "valid-thru" timestamp.

The checks shown: RowCount >= zero (fail job), RowCount >= prior value (fail job), NullCount within a normal distribution (warn job). The metrics were kept in Netflix's metadata services [S47]. I could not read the Tabular write-up of the Iceberg version or the ApacheCon 2022 slides (see [Could not check](#could-not-check)).

### 4.2 The two Iceberg mechanisms

**a) Staged snapshot with a WAP id** (the older mechanism)
- Set the table property `write.wap.enabled=true` ("Enables write-audit-publish writes", default false) [S6] and the Spark session property `spark.wap.id` ("snapshot staging ID") [S5].
- The write commits a snapshot that does not become current. Publish it with `cherrypick_snapshot(table, snapshot_id)` or `publish_changes(table, wap_id)` [S7].
- Limits: "Only append and dynamic overwrite snapshots can be cherry-picked", and `publish_changes` "will fail if there are multiple snapshots in the table with the provided `wap_id`" [S7].
- (inference) That rules it out for `MERGE`-based loads, which section 5 of `docs/data-layer.md` plans for incremental merge.

**b) Branch-based** (the current documented form)

The steps, as documented [S3]:
1. Set `write.wap.enabled`.
2. `CREATE BRANCH audit-branch ... RETAIN 7 DAYS`.
3. Write with `SET spark.wap.branch = audit-branch`.
4. Validate the branch.
5. `CALL system.fast_forward('db.table', 'main', 'audit-branch')`.
6. The branch reference expires with `expireSnapshots`.

Details:
- Spark can also target a branch directly with the identifier `prod.db.table.branch_audit`, for `INSERT`, `MERGE`, `UPDATE` and `DELETE`, and through DataFrames [S4].
- In the Spark 4.1 integration code on `main`, `write.wap.enabled` only gates the session-level `spark.wap.branch` / `spark.wap.id`. An explicit branch identifier or `branch` write option takes precedence and does not need the property. With the property on and `spark.wap.branch` set, reads in that session also go to the branch, if it exists [S8].
- The docs say "the branch must exist before performing write" [S4]. A code comment on `main` says the target branch "may be created during the write operation if it does not exist" [S8]. Not tested here.
- **Schema caveat.** The schema is table-wide [S3]. (inference) If a load evolves the schema to write its audit branch (say a new column), `main` sees the new schema straight away, before the audit passes. Only the data is held back. Schema changes therefore need their own decision; WAP does not cover them.

### 4.3 Engine support

| Engine | Write to a branch | Publish (move `main`) | Read a branch | Confirmed how |
|---|---|---|---|---|
| Spark + Iceberg runtime | yes: identifier, write option, or `spark.wap.branch` | `fast_forward`, `cherrypick_snapshot`, `publish_changes`, `set_current_snapshot` | yes; reads use the table schema [S3] | docs [S3, S4, S7] and source [S8], on `main`. Not re-checked against the 1.12.0 jars |
| PyIceberg 0.12.0 | yes: `append`, `overwrite`, `dynamic_partition_overwrite`, `delete` and `upsert` all take `branch=` | `manage_snapshots().set_current_snapshot(ref_name="audit")`, staged with an `AssertRefSnapshotId` requirement on `main`'s current snapshot, so a commit is refused if `main` moved in the meantime. It does not check that `main` is an ancestor of the branch. `fast_forward_branch` (which raises `NotAncestorError`) is in the `main` docs but **not in the 0.12.0 source**, so it needs a newer release | `scan(snapshot_id=table.refs()["audit"].snapshot_id)` as in the docs [S9] | source at tag [S10]; docs at `main` [S9]. `write.wap.enabled` does not appear in the 0.12.0 table code (grep), so PyIceberg ignores the property |
| Trino 484 | **no**: not in the connector docs on `master`. PR #31135 (open since 2026-09-15) adds `CREATE BRANCH`, `t@branch` DML and `ALTER BRANCH ... FAST FORWARD TO` | **no** (same PR) | yes: `FOR VERSION AS OF 'branch'`, plus a `$refs` table [S11]. **But** PR #30977 (open) reports that Trino reads a branch with the schema of the branch's head snapshot instead of the table's current schema, unlike Spark and Flink [S13] | docs [S11], PRs via gh [S12, S13] |
| DuckDB | not checked | not checked | not checked | |
| Polaris (catalog) | the 2026-11-11 probe on throwaway containers (the version chosen in ADR 0003) passed: a branch and a tag; a write to the branch leaving `main` unchanged; publish by moving `main` under `assert-ref-snapshot-id`, with a stale replay refused (409) | | | ADR 0003 [S49] |

(inference) On this stack today a WAP load can be written and published from Spark or PyIceberg. The audit queries can run in Spark, PyIceberg or DuckDB (not checked). Trino can join the audit only once #30977 is fixed, or for tables whose schema has not changed since the branch head.

### 4.4 Cost on a small stack

- **More commits.**
  - (inference from the APIs) One load becomes about four catalog commits: create the branch, write, publish, remove the branch. A direct append is one.
  - Each commit writes a new metadata file. On a stack with one writer per table this costs seconds, not resources (inference).
- **Failed audits leave files behind.**
  - An unpublished branch keeps its snapshots and data files until the branch is removed and `expire_snapshots` runs [S3, S9].
  - Branch retention (`max-ref-age-ms`, `RETAIN n DAYS`) bounds this [S2, S3].
  - (inference) Snapshot expiry, already listed as deferred "table maintenance" under Operations in issue #1, has to be in place before WAP is used routinely.
- **Concurrency.**
  - Fast-forward needs `main` to be an ancestor of the branch (PyIceberg docs [S9]); PyIceberg 0.12.0's ref update is refused if `main` moved [S10].
  - (inference) With one writer per table this is rare and only means rerunning the load.
- **One table at a time.**
  - WAP publishes per table.
  - Publishing several tables together needs a multi-table commit. On Polaris that was tested only for a rejected requirement, not for a crash mid-commit, and the commit path has an open retry TODO (ADR 0003 [S49]).
- **The schema is not isolated** (4.2).
- **The lighter alternative** (inference): validate the dataframe in memory (Pandera or plain assertions) and append once. That fails before writing, needs no branches, and works with every engine. WAP adds value when a check needs the written table:
  - duplicates against rows already in it;
  - counts against the previous snapshot;
  - reconciliation queries in SQL;
  - checks after a `MERGE`.

## 5. What a load records

| What | Documented example |
|---|---|
| Run identity and timing | dbt `run_results.json`: invocation `args`, `elapsed_time`, and per node `status`, `timing`, `execution_time`, `message`, `adapter_response.rows_affected` [S28]. Glue results: `ResultId`, `StartedOn`, `CompletedOn`, `JobRunId`, `RuleResults`, each with `Result`, `EvaluatedMetrics` and `Labels` [S31] |
| Counts in, out, rejected | Glue aggregated metrics: total rows processed, passed and failed; rules processed, passed and failed; per rule, rows passed, failed and skipped [S32]. Databricks: dropped-record counts and data quality metrics in the pipeline event log, but none for a `fail` [S15]. Iceberg snapshot summaries: `added-records`, `total-records`, `added-data-files` and more [S2] |
| Rejected rows themselves | dbt `store_failures` (overwritten on every run, so it holds no history) [S26]; Glue row-level outcomes [S32]; Pandera `failure_cases` with column, check and failure value [S43] |
| Labels on the commit | Spark `snapshot-property.<key>` write option, `spark.sql.iceberg.snapshot-property.*` and `CommitMetadata`, "for purposes such as auditing or change tracking" [S5]; PyIceberg `snapshot_properties={...}`, with `engine-name` and `engine-version` added automatically [S9]. (inference) A run id stamped on the snapshot links the table history to the run record |
| Check results over time | Glue dynamic rules compare a run with earlier ones (`RowCount > avg(last(5))`, `index(last(3), 0)`), and statistics are kept for up to two years [S30, S31]; Glue's data quality score is "the percentage of data quality rules that pass" [S30]; the AWS Lens suggests publishing a quality score per dataset in the catalog [S35]; Netflix compared each partition with the prior one [S47] |
| Source freshness | dbt `sources.json` per source: `max_loaded_at`, `snapshotted_at`, `max_loaded_at_time_ago_in_s`, `criteria`, `status` (`pass` / `warn` / `error` / `runtime error`) [S29]; freshness computed as `max(loaded_at_field)` against the current time, or from warehouse metadata [S24]; Glue `DataFreshness` on a date column or on the file modification time [S33]; Netflix "valid-thru" timestamp [S47] |
| Arrival and job health | AWS Lens 1.2: alert when new data has not arrived in the expected time, and when jobs "don't complete on time or don't produce results", because a scheduled pipeline "may still appear healthy" with no new data [S36]. ADR 0001 already plans an arrival manifest: key, fetch time, size and content hash per raw object [S49] |

(inference) Taken together, a per-load record in this repo would hold:
- run id, dataset, layer and target table;
- start and end time;
- the raw keys consumed, which link back to the arrival manifest;
- rows read, written, dropped and quarantined;
- each check with its metric value, threshold, severity and outcome;
- the snapshot id produced and whether it was published.

Freshness is then the newest fetch time per dataset against now. Raw-to-bronze-to-gold lag is a join between the manifest and the run records.

## 6. Decisions the owner faces

Where the sources lean one way, that is marked as **sources' position**. The choice stays with the owner.

1. **How strict bronze is.**
   - (a) Every source field as a string or variant, plus a catch-all column.
   - (b) Typed from a code model, plus a catch-all column for unknown or unparsed fields.
   - (c) Strictly typed; fail on mismatch.
   - Sources' position: Databricks recommends (a) and minimal validation in bronze [S14, S17].
   - Iceberg's narrow promotion rules make a typed bronze column hard to change later (1.3).
2. **What bronze does with an unknown field.**
   - Ignore it, since raw keeps it; but then drift must be detected some other way.
   - Keep it in a catch-all column.
   - Add the column automatically.
   - Fail.
   - Sources' position: rescue rather than fail, or add the column and restart (Auto Loader `rescue`, `addNewColumns`) [S17].
3. **Where silver and gold schemas are declared.**
   - (a) Table DDL in a setup script.
   - (b) Code models (Pandera, Pydantic) that also produce the DDL.
   - (c) Contract files (dbt contract, Soda contract, Open Data Contract Standard).
   - How change is allowed: additive changes applied automatically versus explicit migrations; whether a breaking gold change gets a new table version. dbt versions breaking changes [S20].
   - Fit for this stack: dbt and Soda bring a runtime and conventions of their own; Pandera and Pydantic are in-process libraries. Whether dbt adapters fit this stack (dbt-spark, dbt-trino, dbt-duckdb exist (memory)) was not checked.
4. **The response per kind of check:** fail, quarantine, drop, or warn.
   - Sources' position: set severity per check, not per pipeline [S15, S22, S32, S47]. Fail on structural or volume breaks, warn on soft thresholds (the Netflix example [S47]).
   - Also to decide: whether quarantine is a table per dataset, a flag column, or neither, given that raw already keeps everything.
5. **Whether to use WAP, and where.**
   - Options: none (validate in memory, append once); silver and gold only; or every table.
   - Mechanism: branch rather than WAP id, if `MERGE` loads are planned (4.2).
   - Publishing engine: Spark, or PyIceberg (0.12.0's guarded ref update, or a newer release for `fast_forward_branch`). Trino cannot write branches today and misreads branches after a schema change (4.3).
   - Also needed: branch retention and snapshot expiry (4.4).
6. **Which tool runs the checks.**
   - Hand-written SQL or Python checks; Pandera (in-process, polars backend [S44]); dbt tests; Soda or Great Expectations (checkpoints with actions [S38]).
   - Glue Data Quality and Databricks expectations are managed services. They are documented patterns, not options for the local stack. Glue fits only the cloud twin.
7. **What a load records, and where.** A run table as in section 5; the run id on the Iceberg snapshot summary; how long run history is kept, since run-over-run checks need it [S31].
8. **Freshness targets.** For each dataset, a warn age and an error age, measured from the arrival manifest (AWS Lens 1.2 [S36], dbt freshness [S24]).
9. **Reconciliation against a second source.** At which layer (silver is where cross-source ids live, per `docs/data-layer.md`), at what tolerance, and at which severity.

## Could not check

- **Trino 484:** branch support was read on `master` docs and open PRs, not tested.
- **DuckDB's iceberg extension:** reading branches was not checked.
- **Variant type:** PyIceberg 0.12.0 and Spark support for v3 `variant` on this Iceberg version was not checked. Grep found no variant handling in PyIceberg's Arrow conversion [S10].
- **Spark docs version:** the Iceberg Spark docs and code were read on `main`, not at 1.12.0.
- **Write-ups of Iceberg WAP from its originators:**
  - The Tabular page on `spark.wap.id` (`tabular.io`) no longer resolves (DNS failure).
  - The ApacheCon 2022 slides ("The Write-Audit-Publish Pattern via Apache Iceberg") downloaded as a PDF whose text could not be extracted.
  - Only the 2017 Netflix slides were read [S47].
- **Databricks:** no standalone rescued-data page was found (404). Rescued data is covered from the Auto Loader page [S17]. The quarantine example code on [S16] came back as a summary, not verbatim.
- **Soda:** the v3 schema-check page and the SodaCL overview hit redirect loops. The `when schema changes` options are from a search snippet only [S40].
- **AWS Analytics Lens:** two page URLs (`best-practice-1-1.html`, `best-practice-8-2.html`) returned only a title. The equivalent pages under other URLs were read [S35, S37]. The lens's numbering differs between versions.

## Sources

All accessed 2026-11-18.

| Tag | Source | Read |
|---|---|---|
| S1 | Iceberg, Evolution. https://iceberg.apache.org/docs/latest/evolution/ (source: https://github.com/apache/iceberg/blob/main/docs/docs/evolution.md) | raw, in full |
| S2 | Iceberg table spec. https://iceberg.apache.org/spec/ (source: https://github.com/apache/iceberg/blob/main/format/spec.md); sections Default values, Schema Evolution, Identifier Field IDs, Constraints, Snapshot References, Optional Snapshot Summary Fields | raw, sections read |
| S3 | Iceberg, Branching and Tagging. https://iceberg.apache.org/docs/latest/branching/ (source: docs/docs/branching.md on main) | raw, in full |
| S4 | Iceberg, Spark Writes. https://iceberg.apache.org/docs/latest/spark-writes/ (source: docs/docs/spark-writes.md on main) | raw, sections read |
| S5 | Iceberg, Spark Configuration. https://iceberg.apache.org/docs/latest/spark-configuration/ (source: docs/docs/spark-configuration.md on main) | raw, grep and sections |
| S6 | Iceberg, Configuration (table properties). https://iceberg.apache.org/docs/latest/configuration/ (source: docs/docs/configuration.md on main) | raw, grep |
| S7 | Iceberg, Spark Procedures. https://iceberg.apache.org/docs/latest/spark-procedures/ (source: docs/docs/spark-procedures.md on main) | raw, sections read |
| S8 | Iceberg source, `SparkTableUtil.java` (Spark 4.1 module, main). https://github.com/apache/iceberg/blob/main/spark/v4.1/spark/src/main/java/org/apache/iceberg/spark/SparkTableUtil.java; also `SparkWriteConf.java` in the same module | raw, relevant methods read |
| S9 | PyIceberg, Python API docs (main). https://py.iceberg.apache.org/api/ (source: https://github.com/apache/iceberg-python/blob/main/mkdocs/docs/api.md) | raw, sections read |
| S10 | PyIceberg 0.12.0 source: `pyiceberg/table/__init__.py`, `pyiceberg/table/update/snapshot.py`, `pyiceberg/io/pyarrow.py`, `pyiceberg/schema.py` at tag `pyiceberg-0.12.0`. https://github.com/apache/iceberg-python/tree/pyiceberg-0.12.0 | raw, grep and methods read |
| S11 | Trino, Iceberg connector. https://trino.io/docs/current/connector/iceberg.html (source: https://github.com/trinodb/trino/blob/master/docs/src/main/sphinx/connector/iceberg.md) | raw, grep |
| S12 | Trino PR #31135 "Add branch management to Iceberg connector" (open). https://github.com/trinodb/trino/pull/31135; superseded #28464 (closed) | gh |
| S13 | Trino PR #30977 "Use table schema when reading Iceberg branches" (open). https://github.com/trinodb/trino/pull/30977 | gh |
| S14 | Databricks, What is the medallion lakehouse architecture? https://docs.databricks.com/aws/en/lakehouse/medallion | fetch |
| S15 | Databricks, Manage data quality with pipeline expectations. https://docs.databricks.com/aws/en/ldp/expectations | fetch |
| S16 | Databricks, Expectation recommendations and advanced patterns. https://docs.databricks.com/aws/en/ldp/expectation-patterns | fetch (two passes; code came back as a summary) |
| S17 | Databricks, Auto Loader schema inference and evolution. https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/schema | fetch |
| S18 | Databricks, Schema enforcement. https://docs.databricks.com/aws/en/delta/schema-validation | fetch |
| S19 | Spark 4.3.0, JSON Files. https://spark.apache.org/docs/latest/sql-data-sources-json.html | fetch |
| S20 | dbt, Model contracts. https://docs.getdbt.com/docs/mesh/govern/model-contracts | fetch |
| S21 | dbt, Add data tests to your DAG. https://docs.getdbt.com/docs/build/data-tests | fetch |
| S22 | dbt, severity. https://docs.getdbt.com/reference/resource-configs/severity | fetch |
| S23 | dbt, build command. https://docs.getdbt.com/reference/commands/build | fetch |
| S24 | dbt, freshness. https://docs.getdbt.com/reference/resource-properties/freshness | fetch |
| S25 | dbt, Unit tests. https://docs.getdbt.com/docs/build/unit-tests | fetch |
| S26 | dbt, store_failures. https://docs.getdbt.com/reference/resource-configs/store_failures | fetch |
| S27 | dbt, Incremental models (`on_schema_change`). https://docs.getdbt.com/docs/build/incremental-models | fetch |
| S28 | dbt, Run results JSON file. https://docs.getdbt.com/reference/artifacts/run-results-json | fetch |
| S29 | dbt, Sources JSON file. https://docs.getdbt.com/reference/artifacts/sources-json | fetch |
| S30 | AWS Glue Data Quality. https://docs.aws.amazon.com/glue/latest/dg/glue-data-quality.html | fetch (returned in full) |
| S31 | AWS Glue, DQDL reference. https://docs.aws.amazon.com/glue/latest/dg/dqdl.html | fetch (returned in full) |
| S32 | AWS Glue, Evaluating data quality for ETL jobs in AWS Glue Studio. https://docs.aws.amazon.com/glue/latest/dg/tutorial-data-quality.html | fetch (returned in full) |
| S33 | AWS Glue, DQDL DataFreshness. https://docs.aws.amazon.com/glue/latest/dg/dqdl-rule-types-DataFreshness.html | fetch (returned in full) |
| S34 | AWS Glue, DQDL SchemaMatch. https://docs.aws.amazon.com/glue/latest/dg/dqdl-rule-types-SchemaMatch.html | fetch (returned in full) |
| S35 | AWS Well-Architected Data Analytics Lens, Best practice 1.1. https://docs.aws.amazon.com/wellarchitected/latest/analytics-lens/best-practice-1.1---validate-the-data-quality-of-source-systems-before-transferring-data-for-analytics..html | fetch (returned in full) |
| S36 | AWS Well-Architected Data Analytics Lens, Best practice 1.2. https://docs.aws.amazon.com/wellarchitected/latest/analytics-lens/best-practice-1.2---monitor-operational-metrics-of-data-processing-jobs-and-the-availability-of-source-data..html | fetch (returned in full) |
| S37 | AWS Well-Architected Data Analytics Lens, Best practice 7.2. https://docs.aws.amazon.com/wellarchitected/latest/analytics-lens/best-practice-7.2---monitor-for-data-quality-anomalies..html | fetch (returned in full) |
| S38 | Great Expectations, Create a Checkpoint with Actions. https://docs.greatexpectations.io/docs/core/trigger_actions_based_on_results/create_a_checkpoint_with_actions | fetch |
| S39 | Soda, Contract Language reference. https://docs.soda.io/reference/contract-language-reference | fetch |
| S40 | Soda v3, Schema checks. https://docs.soda.io/soda-documentation/soda-v3/sodacl-reference/schema | **not read** (redirect loop); search snippet only |
| S41 | Pandera, DataFrame Schemas (strict, `strict='filter'`) and DataFrame Models. https://pandera.readthedocs.io/en/stable/dataframe_schemas.html (source: https://github.com/unionai-oss/pandera/blob/main/docs/source/dataframe_schemas.md); https://pandera.readthedocs.io/en/stable/dataframe_models.html | raw (schemas); fetch (models) |
| S42 | Pandera, Dropping Invalid Rows. https://pandera.readthedocs.io/en/stable/drop_invalid_rows.html (source: docs/source/drop_invalid_rows.md) | raw, in full |
| S43 | Pandera, Lazy Validation. https://pandera.readthedocs.io/en/stable/lazy_validation.html | fetch |
| S44 | Pandera, Data Validation with Polars. https://pandera.readthedocs.io/en/stable/polars.html (source: docs/source/polars.md) | raw, opening read |
| S45 | Pydantic, Models (extra data). https://pydantic.dev/docs/validation/latest/concepts/models/ | fetch |
| S46 | Confluent, Schema Evolution and Compatibility. https://docs.confluent.io/platform/current/schema-registry/fundamentals/schema-evolution.html | fetch (first part of the page) |
| S47 | M. Ufford (Netflix), "Whoops, the Numbers Are Wrong! Scaling Data Quality @ Netflix", Hadoop Summit 2017. https://www.slideshare.net/slideshow/whoops-the-numbers-are-wrong-scaling-data-quality-netflix/77278406 | fetch (slide text) |
| S48 | Open Data Contract Standard. https://bitol-io.github.io/open-data-contract-standard/latest/ | fetch |
| S49 | This repo: `docs/adr/0001-lakehouse-data-layer.md`, `docs/adr/0003-iceberg-rest-catalog.md`, `docs/data-layer.md` | raw, in full |
