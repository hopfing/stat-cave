# Pipeline guidance gap check

Researched 2026-11-18 for issue #3: what established pipeline and lakehouse guidance treats as part of a pipeline's design that the fetch-to-gold map (issue #1) leaves out or gives too little weight. The map's areas were taken from issue #1 as it stood on that date. Repo context came from `docs/data-layer.md` and `docs/adr/0001` to `0003`.

## Method and how far to trust it

- **Sources.** Vendor and project pages only: AWS Well-Architected Data Analytics Lens, AWS Prescriptive Guidance *Using Apache Iceberg on AWS*, Apache Iceberg docs and spec, Databricks docs, dbt docs. No blog summaries were used.
- **How they were read.** The two AWS documents were downloaded as PDFs and their full text was searched and read in the sections cited ("read in full text" below). The other pages were read through a fetch tool that returns quoted extracts ("read via fetch"). The quotes are as the tool returned them. One search-engine summary was seen and is not relied on. Anything from memory is marked **(memory, not checked)**.
- **Fit.** Most of this guidance is written for teams on managed cloud services: separate accounts per environment, data owners, access matrices. This repo is one developer on a local Docker stack. Each gap below says where the guidance has to be scaled down.
- **Classification:** (a) the first build cannot be done without deciding it; (b) cheap now but costly to change later; (c) can wait.

## Checklist from the sources, compared with the map

| What the guidance treats as part of a pipeline's design | Main source | Map area | Verdict |
|---|---|---|---|
| Bronze keeps the source as received, append-only, with all history, so later layers can be rebuilt | Databricks medallion | Raw, Bronze | covered |
| Provenance metadata columns in bronze (source file name) | Databricks medallion | Bronze ("link back to the raw object") | covered |
| Bronze fields stored loosely typed (string/variant/binary) "to protect against unexpected schema changes" | Databricks medallion | Bronze (shape, schema), Bad data | covered as a question; useful input to it |
| Silver: validation, dedup, type casting, late or out-of-order data, quarantine of invalid records | Databricks medallion | Silver, Bad data | covered, apart from deletes and ordering (gap 1) |
| Gold: aggregates shaped for consumers | Databricks medallion | Gold | covered |
| One schema/namespace per layer; descriptive names | Databricks medallion | Naming, existing layout | covered |
| Idempotent jobs that can be rerun with the same result | AWS lens 6.4.4 | Running | covered |
| Automatic retries, a limit on reruns, logged attempts | AWS lens 6.4.1, 6.4.2 | Fetching (retries) | covered |
| Alert when source data has not arrived in time, or a job produces nothing | AWS lens 1.2.1, 1.2.2 | Tracking, Quality monitoring, Operations (alerting deferred) | covered: what is recorded now, alerting later |
| Freshness needs a load-time field on each table | dbt sources (`loaded_at_field`) | Quality monitoring | covered, if the load time is among the facts each load records |
| Quality checks as early as possible; completeness, accuracy, uniqueness | AWS lens 1.1.1, 7.2.1 | Bad data, Quality monitoring | covered |
| Backfill as a standard procedure | AWS lens 2.4 | Fetching (backfill, past seasons) | covered |
| Version control and infrastructure as code | AWS lens 2.1 | `scripts/setup_stack.py`, the repo | covered |
| Central catalog | AWS lens 7.1 | Polaris (ADR 0003) | covered |
| Retention policies, compaction, small files | AWS lens 3.7, 10.1.2; Iceberg maintenance | Operations (deferred) | can wait, except where it meets history (gap 1) |
| History of a mutable record: SCD2, deletes, ordering, business time and system time | dbt snapshots, Databricks AUTO CDC, Iceberg maintenance | Silver ("whether earlier versions are kept") | **thin, gap 1** |
| Rules for changing a published table's schema; contracts for consumers | Iceberg evolution and spec, dbt contracts, AWS lens 7.1.1 | Bronze/Silver/Gold "schema"; Bad data covers source drift only | **thin, gap 2** |
| Lineage covering the logic used, not only the origin; rebuild after a logic change | AWS lens 7.3.1, dbt incremental (`--full-refresh`) | Bronze link to raw; Running | **thin, gap 3** |
| Separate dev/test/prod; a fixed curated test dataset | AWS lens 2.2, 2.3.1; dbt environments | Code and tests | **absent, gap 4** |
| Checks run on a staging branch before publishing | Iceberg branching (audit branch) | Bad data (what fails); ADR 0001 and build 5 | **thin, gap 5** |
| Physical table defaults: format version, partitioning, sort order | AWS Iceberg guide; Iceberg evolution; AWS lens 10.4 | none | **absent, gap 6** |
| Backup and DR per layer, RPO/RTO, tested restores | AWS lens 6.5, 3.7.1 | Operations ("backup timing", deferred) | **underweighted, gap 7** |
| Privacy, data classification, owners, access matrices, encryption | AWS lens 3.x, 4.x, 5.x | none | not a gap at this scale (see the end) |

## Gaps

### 1. How silver keeps history, orders versions and records deletes

**What the guidance says.**
- Iceberg: "Regularly expiring snapshots is recommended to delete data files that are no longer needed." And: "Expiring old snapshots removes them from metadata, so they are no longer available for time travel queries." <https://iceberg.apache.org/docs/latest/maintenance/> (read via fetch)
- AWS: to keep particular past states while expiring the rest, tag the snapshots and set a retention on the tags, for example one snapshot per month kept for a year (`CREATE TAG ... RETAIN 365 DAYS`). It also says to "keep at least enough snapshots ... to meet your recovery point objective for time travel and rollback." AWS Prescriptive Guidance, *Using Apache Iceberg on AWS*, "Optimizing storage", "Archive or delete historic snapshots": <https://docs.aws.amazon.com/prescriptive-guidance/latest/apache-iceberg-on-aws/> (read in full text, PDF). Iceberg's own branching page: "Branching and tagging can be used for handling GDPR requirements and retaining important historical snapshots for auditing." <https://iceberg.apache.org/docs/latest/branching/> (read via fetch)
- dbt keeps history in rows, not in table versions: "Snapshots implement type-2 Slowly Changing Dimensions over mutable source tables", with `dbt_valid_from` and `dbt_valid_to`. Change is detected by a `timestamp` strategy or a `check` strategy (compare columns) "for tables which do not have a reliable `updated_at` column". Deletes in the source are a separate setting, `hard_deletes`: `ignore` (the default), `invalidate`, or `new_record` with `dbt_is_deleted`. <https://docs.getdbt.com/docs/build/snapshots> (read via fetch)
- Databricks AUTO CDC orders versions by a declared `sequence_by` key ("NULL sequencing values are not supported"), handles deletes through `apply_as_deletes`, and offers bitemporal targets that "track when each change took effect (business time) and when the system recorded it (system time)". <https://docs.databricks.com/aws/en/ldp/cdc> (read via fetch)

**Where the map is thin.** The Silver item asks "how re-fetches and corrections are absorbed, whether earlier versions are kept". It does not ask:
- **Where earlier versions live.** In rows (SCD2-style validity columns) or in Iceberg snapshots. ADR 0001 currently leans on snapshots ("Iceberg snapshots keep the previous state of a table after a correction is merged in"). Operations, which the map defers, covers table maintenance and retention, and Iceberg's own recommended maintenance (snapshot expiry) deletes exactly that history. The two items are coupled, and the deferred one can quietly undo the decided one. Snapshots also record when this repo loaded a version, not when the source changed it.
- **Which version wins.** The ordering key when two fetches of the same record disagree. Fetch time is the obvious key, but a backfill re-fetch of an old game arrives later than a daily fetch and is not necessarily more correct. ESPN responses may or may not carry a usable source-side update time.
- **Deletes.** What silver does when a record present in one fetch is missing from a later fetch of the same thing (a play removed from play-by-play, a game dropped from a schedule). Neither "correction" nor "bad data" names this case.
- **Which clocks a row carries.** Game time, source update time (if any), fetch time, load time; and the time zone rule. Iceberg distinguishes `timestamptz` ("values are stored as UTC and do not retain a source time zone") from `timestamp` ("without timezone"). <https://iceberg.apache.org/spec/> (read via fetch)

**Classification.**
- (a) for ordering and deletes: the ESPN shape is "re-fetched and corrected", so the first silver table needs both.
- (b) for rows versus snapshots: history in rows can be added later only from what raw still holds, and history in snapshots is lost at the first snapshot expiry.

**Fit.** No change-data-capture feed exists here; every source is fetched whole, so "deletes" means absence from a later fetch, not a delete event. A row-based history may be the simpler choice on this stack, because it survives any maintenance and reads the same in all four engines.

### 2. Rules for changing a published table's schema

**What the guidance says.**
- Iceberg schema changes are "metadata changes, so no data files need to be rewritten", and columns are tracked by ID: "When you add a column, it is assigned a new ID so existing data is never used by mistake." <https://iceberg.apache.org/docs/latest/evolution/> (read via fetch)
- Type changes are narrow. The spec's valid promotions are `int` to `long`, `float` to `double`, `decimal(P,S)` to `decimal(P',S)` with `P' > P`, and in v3 `date` to `timestamp` (promotion to `timestamptz` is not allowed). <https://iceberg.apache.org/spec/> (read via fetch). Any other type change means rewriting the table.
- dbt contracts: enforced contracts "must include every column's `name` and `data_type`". Breaking changes are listed as "Removing an existing column", "Changing the data_type of an existing column", removing or modifying a constraint, and removing a contracted model. "When the contract changes in a backwards-incompatible way, it is a breaking change that requires a bump in major version." <https://docs.getdbt.com/docs/mesh/govern/model-contracts> (read via fetch)
- dbt incremental models make the policy for an upstream schema change explicit: `on_schema_change` is one of `ignore`, `fail`, `append_new_columns`, `sync_all_columns`. "None of the `on_schema_change` behaviors backfill values in old records for newly added columns." <https://docs.getdbt.com/docs/build/incremental-models> (read via fetch)
- AWS: "You can create a schema contract between producers and consumers to improve data consumer awareness to data format changes." Data Analytics Lens, suggestion 7.1.1: <https://docs.aws.amazon.com/wellarchitected/latest/analytics-lens/analytics-lens.html> (read in full text, PDF)

**Where the map is thin.** Bronze, Silver and Gold each list "schema", and Bad data covers unexpected fields and types *from the source*. Nothing covers changes made *by this repo* to tables already published, although ADR 0001 makes the catalog "the contract with modelling" and other repos read gold by name. Three questions are open:
- which changes are allowed in place;
- what happens to a breaking change (a new table name, a version suffix, or a rebuild);
- whether an added silver or gold column is backfilled.

**Classification.** (b). Choosing narrow types now (an `int` that later needs to become a `string` id, a `timestamp` that should have been `timestamptz`) costs a table rewrite later. A versioning rule for gold costs nothing before a consumer exists and a migration after. The full policy can be decided with Gold; the type choices belong with the first silver schema.

**Fit.** There is one consumer, and it is the same developer. A one-paragraph rule is enough: additive changes in place, anything else as a new table. A contracts tool is not needed.

### 3. Run identity, lineage of the logic, and rebuilds

**What the guidance says.**
- AWS: "Data lineage information should include where data has come from, where the data is going, and who has access to the data. Data changes and the business logic used should also be tracked in the data lineage." Data Analytics Lens, suggestion 7.3.1 (read in full text, PDF; URL as in gap 2)
- Databricks: bronze "enables reprocessing and auditing by retaining all historical data" <https://docs.databricks.com/aws/en/lakehouse/medallion> (read via fetch)
- dbt: "If your incremental model logic has changed, the transformations on your new rows of data may diverge from the historical transformations ... In this case, you should rebuild your incremental model", through `--full-refresh`, which with a trailing `+` also rebuilds everything downstream. <https://docs.getdbt.com/docs/build/incremental-models> (read via fetch)

**Where the map is thin.**
- **Lineage.** The map has the bronze row's link back to its raw object (origin) and Tracking (arrivals). It has nothing that records *which run and which version of the code* wrote a bronze, silver or gold row.
- **Rebuilds.** Running asks "what makes it safe to rerun". That is an incremental rerun of the same input. It does not ask how a layer is rebuilt from raw after a parser or logic fix, which ADR 0001 promises ("A parser fix never needs a re-fetch"). Two questions follow: does a rebuild replace the table or write a new one beside it, and must a rebuild reproduce the same silver keys?

**Classification.** (b) for the run id or code version on written rows: it cannot be filled in truthfully for rows written before it existed. (c) for the rebuild procedure itself, but the incremental design should not rule it out: every layer must be derivable from the one below it alone.

**Fit.** Lineage graphs and visualisation tools (lens 7.3.2) are enterprise. Here a run id plus a git commit hash is enough, written on each row or kept in a run log that rows point to. Iceberg can also attach custom properties to a snapshot's summary, which would put the run id on each commit rather than on each row **(memory, not checked: the Spark write option `snapshot-property.<key>`)**.

### 4. Where development and test writes go

**What the guidance says.**
- AWS: "Use separate environments, such as development, test, and production, to allow feature development to be introduced without disrupting production systems." Also: "Analytics projects that are being developed should use the same curated dataset to compare results between tests of different versions of your code." Data Analytics Lens, best practice 2.2, suggestions 2.2.1 and 2.3.1 (read in full text, PDF; URL as in gap 2)
- dbt: "environments are used to enable engineers to develop and test code without impacting the users of their software", and self-hosted dbt keeps "separate deployment and development environments through the use of targets". <https://docs.getdbt.com/docs/environments-in-dbt> (read via fetch)

**Where the map is absent.** "Code and tests: layout and test seams" covers code. It does not say where a test, a trial load or a half-finished pipeline writes. There is one catalog `statcave`, four buckets, and three layer namespaces whose layout ADR 0003 fixes tightly: tables at `<layer>.<source>.<table>`, and the catalog refuses other locations. The repo already improvises: `_probe` namespaces, throwaway compose projects with other names and ports. Nothing settles it for pipelines. A related question is what the fixed test dataset is (a set of saved ESPN responses checked into tests, or held in raw), which overlaps with "how files already held enter" under Raw.

**Classification.** (a). The first build's integration tests and first trial loads need a target that is not the real tables. The options here are cheap: a throwaway compose project, a reserved `_dev`/`_test` source namespace, or an Iceberg branch. But the choice touches Naming: a namespace prefix shows in every table name.

**Fit.** Separate AWS accounts per environment (lens 2.2) do not fit. A second compose project, which the repo has already run for checks, is the local equivalent.

### 5. Whether a load is checked before or after it is published

**What the guidance says.**
- Iceberg's audit branch: "Writes are performed on a separate `audit-branch` independent from the main table history. A validation workflow can validate (e.g. data quality) the state of `audit-branch`. After validation, the main branch can be `fastForward` to the head of `audit-branch`." <https://iceberg.apache.org/docs/latest/branching/> (read via fetch)
- AWS describes the same Write-Audit-Publish pattern with Iceberg branching. *Using Apache Iceberg on AWS*, "Write-Audit-Publish pattern with Iceberg branching" (read in full text, PDF; only the end of the section, with its data-quality rules, was read closely)

**Where the map is thin.** ADR 0001 names quality gates on a branch as a goal, ADR 0003 tested branch publish on Polaris, and build 5 is write-audit-publish. The map's Bad data item decides *what* fails a load, rejects rows or loads with a flag. It does not decide *where in the run* the check sits: before the commit, after the commit to `main`, or on a branch that is then fast-forwarded. Running ("which engine does which step") does not name a publish step either.

**Classification.** (b). The first slice can commit straight to `main`, but only if every pipeline commits through one step that can later become "write to a branch, check, fast-forward". Retrofitting that seam into pipelines that each commit their own way is the costly part. Only the hard-to-reverse part needs deciding now: one publish step per run.

**Fit.** Matches the repo's own plan; nothing enterprise about it. Note the interaction with Polaris's untested crash mid-commit (ADR 0003) if a publish moves several tables.

### 6. Physical table defaults: format version, partitioning, sort order

**What the guidance says.**
- AWS on Iceberg format versions: V3 "is not yet supported by every consumer". Picking V3 or a V3 feature "that one of your readers can't handle produces a table that part of your pipeline can't consume, and it's a hard problem to unwind because V3 upgrades are one-way." *Using Apache Iceberg on AWS*, "General best practices" (read in full text, PDF)
- Iceberg: "When you evolve a partition spec, the old data written with an earlier spec remains unchanged ... Partition evolution is a metadata operation and does not eagerly rewrite files." The same holds for sort order. <https://iceberg.apache.org/docs/latest/evolution/> (read via fetch)
- AWS: "Partition data to support the most common query predicates", and partition by time where reports are periodic. Data Analytics Lens 10.4 (read in full text, PDF)

**Where the map is absent.** Nothing on the map covers a table's physical properties. The repo reads all tables with four engines (Spark, Trino, DuckDB, PyIceberg) and plans a cloud twin on Athena. A table's format version therefore has to be one that all of them, at the versions pinned in `docs/data-layer.md`, can read and write. Which version that is was not checked here.

**Classification.**
- Format version: (b). It is one line per table now and a one-way upgrade later, and engine support differs.
- Partitioning and sort order: (c). Iceberg evolves them without rewrites, and at today's sizes an unpartitioned first table is defensible. Note only that the default is "none until a table is large".

**Fit.** Fine at this scale, but the four-engine mix makes the format version matter more here than in a single-engine shop.

### 7. Backup before the first real load

**What the guidance says.**
- "Design the disaster recovery (DR) solution for each layer of the solution", and "Test your backup to ensure it is performing correctly by periodically restoring the data and validating the results." Data Analytics Lens, suggestions 6.5.2 and 6.5.3 (read in full text, PDF)
- Backups follow from "recovery point objective (RPO), recovery time objective (RTO)". Data Analytics Lens, suggestion 3.7.1 (read in full text, PDF)

**Where the map underweights it.** The map lists "backup timing" under Operations, named and deferred. `docs/data-layer.md` section 7 item 2 says the backup is to be set up *before the first real load*: raw synced to S3, plus a `pg_dump` of the catalog's Postgres, without which "which metadata file is current for each table has to be worked out and re-registered by hand". The first slice is that first real load, so the map's deferral contradicts the repo's own standing plan. The guidance adds a step neither document names: a restore test.

**Classification.** (a) by the repo's own rule, not by the map's. It is not a design question for the pipeline conventions, so it may belong on the build checklist rather than on the map, but it should not stay "deferred".

**Fit.** RPO/RTO talks with stakeholders do not apply. The local version is one sentence: raw and the catalog can be restored to within one day, and the restore has been tried once.

## Considered and not reported as gaps

- **Privacy, data classification, data owners, access matrices, encryption, audit logs** (Data Analytics Lens pillars 3 to 5). The data is public sports and market data with no personal data beyond public athlete names. There is one user, and credentials are already explicit (`docs/data-layer.md`, "Clients"). Not a gap at this scale.
- **Concurrent writers to one table.** Iceberg commits are atomic (compare-and-swap on the catalog pointer, per the AWS Iceberg guide), and one scheduled writer per table is the natural default here. (c), revisit with live capture.
- **Small files and compaction** (lens 10.1.2; Iceberg "Compact data files"). Correctly deferred under Operations. Append-heavy price history will make it matter sooner than the ESPN slice does.
- **Business metadata, column descriptions, glossary** (lens 7.1.2). `GLOSSARY.md` is planned. Column docs can be folded into gap 2's schema rule. (c)
- **Cost per step** (lens cost pillar). Build 6 already covers footprint. (c)

## Sources

All accessed 2026-11-18.

- AWS Well-Architected, Data Analytics Lens (December 2023): <https://docs.aws.amazon.com/wellarchitected/latest/analytics-lens/analytics-lens.html>; full text from <https://docs.aws.amazon.com/pdfs/wellarchitected/latest/analytics-lens/analytics-lens.pdf>. Sections read: 1.1, 1.2, 2.2 to 2.4, 3.7, 6.4, 6.5, 7.1 to 7.3, 10.1, 10.4.
- AWS Prescriptive Guidance, *Using Apache Iceberg on AWS*: <https://docs.aws.amazon.com/prescriptive-guidance/latest/apache-iceberg-on-aws/>; full text from <https://docs.aws.amazon.com/pdfs/prescriptive-guidance/latest/apache-iceberg-on-aws/apache-iceberg-on-aws.pdf>. Sections read: "General best practices", "Optimizing storage", snapshot retention notes, the end of the Write-Audit-Publish section.
- Apache Iceberg, Maintenance: <https://iceberg.apache.org/docs/latest/maintenance/>
- Apache Iceberg, Branching and Tagging: <https://iceberg.apache.org/docs/latest/branching/>
- Apache Iceberg, Evolution: <https://iceberg.apache.org/docs/latest/evolution/>
- Apache Iceberg, Table Spec (primitive types, valid type promotions): <https://iceberg.apache.org/spec/>
- Databricks, Medallion architecture: <https://docs.databricks.com/aws/en/lakehouse/medallion>
- Databricks, The AUTO CDC APIs: <https://docs.databricks.com/aws/en/ldp/cdc>
- dbt, Sources (freshness, tests): <https://docs.getdbt.com/docs/build/sources>
- dbt, Snapshots: <https://docs.getdbt.com/docs/build/snapshots>
- dbt, Model contracts: <https://docs.getdbt.com/docs/mesh/govern/model-contracts>
- dbt, Incremental models: <https://docs.getdbt.com/docs/build/incremental-models>
- dbt, Environments: <https://docs.getdbt.com/docs/environments-in-dbt>
