"""Catalog probe: static keys on SeaweedFS, branches, tags, multi-table commit.

NOT YET ADAPTED TO THIS REPO'S STACK. This is the script as it ran on 2026-11-11 for the
catalog comparison (decision 0003), against throwaway containers: hostnames `sw`, `lk` and
`pol`, throwaway keys, buckets `probe-lk` / `probe-pol`. It is kept so the same checks can be
rerun after catalog upgrades; it is to be pointed at the real Polaris once that is up, and the
Lakekeeper path stays until then because Lakekeeper is the fallback.

It needs pyiceberg, pyarrow and requests, which are not project dependencies yet.

What it checks (12 steps, all passed on Lakekeeper v0.15.0 and Polaris 1.8.0):
- a table can be created, written and read with the client's own static S3 keys;
- a branch and a tag can be created, and a write to the branch leaves main unchanged;
- publish: `set-snapshot-ref` on main guarded by `assert-ref-snapshot-id` succeeds, and a
  replay with the stale snapshot id is refused with 409;
- a multi-table commit (`transactions/commit`) across two tables succeeds, and one with a
  failing requirement is refused and changes neither table.
Not checked: a crash in the middle of a multi-table commit.

Usage: python catalog_probe.py lk|pol [default|empty|drop]
The second argument is how the X-Iceberg-Access-Delegation header is sent to Polaris.
PyIceberg asks for vended credentials by default and Polaris then refuses with HTTP 400
when it has none to vend; `empty` or `drop` is needed with plain static keys.
"""

import json
import sys

import boto3
import pyarrow as pa
import requests
from pyiceberg.catalog import load_catalog

S3 = "http://sw:8333"
KEY, SECRET, REGION = "probekey", "probesecret", "us-east-1"
which = sys.argv[1]
DELEG = sys.argv[2] if len(sys.argv) > 2 else "default"
BUCKET = f"probe-{which}"
RESULTS = []


def step(name):
    def deco(fn):
        try:
            out = fn()
            RESULTS.append((name, "PASS", out))
            print(f"PASS  {name}: {out}", flush=True)
        except Exception as e:
            RESULTS.append((name, "FAIL", repr(e)[:600]))
            print(f"FAIL  {name}: {repr(e)[:600]}", flush=True)
        return fn

    return deco


s3 = boto3.client(
    "s3", endpoint_url=S3, aws_access_key_id=KEY, aws_secret_access_key=SECRET, region_name=REGION
)


@step("create bucket with static keys")
def _():
    names = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
    if BUCKET not in names:
        s3.create_bucket(Bucket=BUCKET)
    return BUCKET


client_s3 = {
    "s3.endpoint": S3,
    "s3.access-key-id": KEY,
    "s3.secret-access-key": SECRET,
    "s3.region": REGION,
    "s3.path-style-access": "true",
}

if which == "lk":
    MG = "http://lk:8181/management/v1"

    @step("lakekeeper bootstrap")
    def _():
        r = requests.post(f"{MG}/bootstrap", json={"accept-terms-of-use": True}, timeout=30)
        return f"{r.status_code} {r.text[:200]}"

    @step("lakekeeper create warehouse (sts off, signing off, static key)")
    def _():
        body = {
            "warehouse-name": "probe",
            "project-id": "00000000-0000-0000-0000-000000000000",
            "storage-profile": {
                "type": "s3",
                "bucket": BUCKET,
                "key-prefix": "wh",
                "endpoint": S3,
                "region": REGION,
                "path-style-access": True,
                "flavor": "s3-compat",
                "sts-enabled": False,
                "remote-signing-enabled": False,
            },
            "storage-credential": {
                "type": "s3",
                "credential-type": "access-key",
                "access-key-id": KEY,
                "secret-access-key": SECRET,
            },
        }
        r = requests.post(f"{MG}/warehouse", json=body, timeout=60)
        if r.status_code >= 300 and "already exists" not in r.text:
            raise RuntimeError(f"{r.status_code} {r.text[:500]}")
        return f"{r.status_code} {r.text[:200]}"

    cat = load_catalog(
        "lk", type="rest", uri="http://lk:8181/catalog", warehouse="probe", **client_s3
    )
else:
    POL = "http://pol:8181/api"
    H = {"Polaris-Realm": "POLARIS"}

    tok = requests.post(
        f"{POL}/catalog/v1/oauth/tokens",
        auth=("root", "s3cr3t"),
        headers=H,
        data={"grant_type": "client_credentials", "scope": "PRINCIPAL_ROLE:ALL"},
        timeout=30,
    )
    print("polaris token status", tok.status_code, flush=True)
    AH = {**H, "Authorization": f"Bearer {tok.json()['access_token']}"}

    @step("polaris create catalog (stsUnavailable, pathStyleAccess)")
    def _():
        body = {
            "catalog": {
                "name": "probe",
                "type": "INTERNAL",
                "readOnly": False,
                "properties": {"default-base-location": f"s3://{BUCKET}/wh"},
                "storageConfigInfo": {
                    "storageType": "S3",
                    "allowedLocations": [f"s3://{BUCKET}/"],
                    "endpoint": S3,
                    "stsUnavailable": True,
                    "pathStyleAccess": True,
                },
            }
        }
        r = requests.post(f"{POL}/management/v1/catalogs", headers=AH, json=body, timeout=60)
        if r.status_code >= 300 and r.status_code != 409:
            raise RuntimeError(f"{r.status_code} {r.text[:500]}")
        return f"{r.status_code} {r.text[:300]}"

    @step("polaris grant CATALOG_MANAGE_CONTENT")
    def _():
        r = requests.put(
            f"{POL}/management/v1/catalogs/probe/catalog-roles/catalog_admin/grants",
            headers=AH,
            json={"type": "catalog", "privilege": "CATALOG_MANAGE_CONTENT"},
            timeout=30,
        )
        if r.status_code >= 300:
            raise RuntimeError(f"{r.status_code} {r.text[:500]}")
        return r.status_code

    cat = load_catalog(
        "pol",
        type="rest",
        uri="http://pol:8181/api/catalog",
        warehouse="probe",
        credential="root:s3cr3t",
        scope="PRINCIPAL_ROLE:ALL",
        **client_s3,
        **({"header.X-Iceberg-Access-Delegation": ""} if DELEG == "empty" else {}),
    )
    if DELEG == "drop":
        del cat._session.headers["X-Iceberg-Access-Delegation"]
    print(
        "delegation header sent:",
        repr(cat._session.headers.get("X-Iceberg-Access-Delegation")),
        flush=True,
    )

prefix = cat.properties.get("prefix", "")
BASE = cat.properties["uri"].rstrip("/") + "/v1/" + (prefix + "/" if prefix else "")
print(
    "catalog config from server:",
    {k: v for k, v in cat.properties.items() if "secret" not in k and k != "credential"},
    flush=True,
)

schema = pa.schema([("id", pa.int64()), ("v", pa.string())])


def rows(start, n):
    return pa.table(
        {"id": list(range(start, start + n)), "v": [f"r{i}" for i in range(start, start + n)]},
        schema=schema,
    )


def count(t, ref=None):
    if ref is None:
        return t.scan().to_arrow().num_rows
    return t.scan(snapshot_id=t.refs()[ref].snapshot_id).to_arrow().num_rows


def post(path, body):
    return cat._session.post(
        BASE + path, data=json.dumps(body), headers={"Content-Type": "application/json"}
    )


@step("create namespace and two tables")
def _():
    if ("p",) not in cat.list_namespaces():
        cat.create_namespace("p")
    for n in ("t1", "t2"):
        if cat.table_exists(f"p.{n}"):
            cat.drop_table(f"p.{n}")
        cat.create_table(f"p.{n}", schema=schema)
    return [x[-1] for x in cat.list_tables("p")]


@step("append 3 rows to t1 and read back (client writes data with its own static keys)")
def _():
    t = cat.load_table("p.t1")
    print(
        "loadTable config keys:",
        sorted(k for k in t.io.properties if k.startswith(("s3.", "client."))),
        flush=True,
    )
    t.append(rows(0, 3))
    t = cat.load_table("p.t1")
    return {
        "rows": count(t),
        "metadata_location": t.metadata_location,
        "format-version": t.metadata.format_version,
    }


@step("paths stored in metadata (first data file, manifest list)")
def _():
    t = cat.load_table("p.t1")
    f = t.inspect.files().to_pylist()[0]["file_path"]
    return {
        "location": t.metadata.location,
        "data_file": f,
        "manifest_list": t.current_snapshot().manifest_list,
    }


@step("create branch 'audit' and tag 'v1' at current snapshot")
def _():
    t = cat.load_table("p.t1")
    sid = t.current_snapshot().snapshot_id
    t.manage_snapshots().create_branch(sid, "audit").create_tag(sid, "v1").commit()
    t = cat.load_table("p.t1")
    return {
        k: (
            v.snapshot_ref_type.value
            if hasattr(v.snapshot_ref_type, "value")
            else str(v.snapshot_ref_type)
        )
        for k, v in t.refs().items()
    }


@step("append 2 rows to branch 'audit'; main must stay at 3")
def _():
    t = cat.load_table("p.t1")
    t.append(rows(100, 2), branch="audit")
    t = cat.load_table("p.t1")
    out = {"main": count(t), "audit": count(t, "audit"), "tag v1": count(t, "v1")}
    assert out == {"main": 3, "audit": 5, "tag v1": 3}, out
    return out


@step("publish: set-snapshot-ref main -> audit head, guarded by assert-ref-snapshot-id (raw REST)")
def _():
    t = cat.load_table("p.t1")
    old, new = t.refs()["main"].snapshot_id, t.refs()["audit"].snapshot_id
    body = {
        "identifier": {"namespace": ["p"], "name": "t1"},
        "requirements": [{"type": "assert-ref-snapshot-id", "ref": "main", "snapshot-id": old}],
        "updates": [
            {"action": "set-snapshot-ref", "ref-name": "main", "type": "branch", "snapshot-id": new}
        ],
    }
    r = post("namespaces/p/tables/t1", body)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    r2 = post("namespaces/p/tables/t1", body)  # stale requirement, must be refused
    t = cat.load_table("p.t1")
    out = {"publish": r.status_code, "stale replay": r2.status_code, "main rows": count(t)}
    assert out["main rows"] == 5 and r2.status_code == 409, out
    return out


@step("drop branch 'audit' (remove-snapshot-ref)")
def _():
    r = post(
        "namespaces/p/tables/t1",
        {
            "identifier": {"namespace": ["p"], "name": "t1"},
            "requirements": [],
            "updates": [{"action": "remove-snapshot-ref", "ref-name": "audit"}],
        },
    )
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    return sorted(cat.load_table("p.t1").refs())


def mt_body(prop, u1, u2):
    def ch(name, uuid):
        return {
            "identifier": {"namespace": ["p"], "name": name},
            "requirements": [{"type": "assert-table-uuid", "uuid": str(uuid)}],
            "updates": [{"action": "set-properties", "updates": {prop: "1"}}],
        }

    return {"table-changes": [ch("t1", u1), ch("t2", u2)]}


@step("multi-table commit: POST transactions/commit across t1 and t2")
def _():
    t1, t2 = cat.load_table("p.t1"), cat.load_table("p.t2")
    r = post(
        "transactions/commit", mt_body("probe.mt", t1.metadata.table_uuid, t2.metadata.table_uuid)
    )
    assert r.status_code in (200, 204), f"{r.status_code} {r.text[:300]}"
    got = [cat.load_table(f"p.{n}").properties.get("probe.mt") for n in ("t1", "t2")]
    assert got == ["1", "1"], got
    return {"status": r.status_code, "props": got}


@step("multi-table atomicity: one bad requirement must leave both tables unchanged")
def _():
    t1 = cat.load_table("p.t1")
    r = post(
        "transactions/commit",
        mt_body("probe.mt2", t1.metadata.table_uuid, "00000000-0000-0000-0000-000000000001"),
    )
    got = [cat.load_table(f"p.{n}").properties.get("probe.mt2") for n in ("t1", "t2")]
    assert r.status_code >= 400 and got == [None, None], (r.status_code, got, r.text[:200])
    return {"status": r.status_code, "props": got}


print("\n==== SUMMARY", which)
for name, res, _ in RESULTS:
    print(f"{res}  {name}")
sys.exit(0 if all(r == "PASS" for _, r, _ in RESULTS) else 1)
