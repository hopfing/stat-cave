"""Set up the store and the catalog: buckets, raw versioning, the catalog, the layer namespaces.

Safe to rerun. Each step looks first and creates only what is missing. Nothing that exists is
changed or deleted: where a catalog or namespace differs from what is described here, the
script stops and says what differs. The one setting it turns back on is versioning on the raw
bucket.

Checking is separate: scripts/s3_smoke.py (store) and scripts/catalog_probe.py (catalog).

Run from the repo root with the stack up (`docker compose up -d` in infra/):
    uv run --env-file infra/.env python scripts/setup_stack.py
"""

import os

import boto3
import requests
from botocore.config import Config

# Bucket names and region match the AWS buckets. Iceberg metadata stores full
# s3://bucket/key paths, so a table moves between stores without a path rewrite only when
# the bucket name is the same on both sides.
RAW_BUCKET = "statcave-raw"  # files as received; versioned; not a table layer
LAYERS = ["bronze", "silver", "gold"]  # one namespace and one bucket each
REGION = "us-east-2"

CATALOG = "statcave"
REALM = "POLARIS"  # POLARIS_REALM_CONTEXT_REALMS in the compose file
# The store as Polaris reaches it: the compose service name, not the host port.
STORE_FROM_POLARIS = "http://seaweedfs:8333"
TIMEOUT = 30


def layer_bucket(layer: str) -> str:
    return f"statcave-{layer}"


def layer_location(layer: str) -> str:
    return f"s3://{layer_bucket(layer)}"


ALLOWED_LOCATIONS = [f"{layer_location(layer)}/" for layer in LAYERS]  # raw is not in the list
CATALOG_PROPERTIES = {
    # Polaris requires a base location inside the allowed locations. Every layer namespace
    # sets its own location, so nothing is placed by this value; a namespace created without
    # a location would land inside bronze and is refused as an overlap.
    "default-base-location": f"{layer_location('bronze')}/",
    # Lets a namespace carry its own location (one bucket per layer). Polaris classes this
    # as a behaviour-change flag that may be removed: docs/data-layer.md, section 7.
    "polaris.config.namespace-custom-location.enabled": "true",
    # A table goes where its namespace puts it; a client cannot name another place.
    "polaris.config.allow.client-specified.table.location": "false",
}


def stop(message: str) -> None:
    raise SystemExit(f"STOP: {message}")


def expect(response: requests.Response, *statuses: int) -> requests.Response:
    if response.status_code not in statuses:
        request = response.request
        stop(f"{request.method} {request.url}: {response.status_code} {response.text[:300]}")
    return response


def setup_buckets() -> None:
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT"],
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        region_name=REGION,
        config=Config(s3={"addressing_style": "path"}),
    )
    existing = {b["Name"] for b in s3.list_buckets()["Buckets"]}
    for bucket in [RAW_BUCKET, *(layer_bucket(layer) for layer in LAYERS)]:
        if bucket in existing:
            print(f"bucket {bucket}: exists")
        else:
            s3.create_bucket(Bucket=bucket)
            print(f"bucket {bucket}: created")

    if s3.get_bucket_versioning(Bucket=RAW_BUCKET).get("Status") == "Enabled":
        print(f"versioning on {RAW_BUCKET}: enabled")
    else:
        s3.put_bucket_versioning(Bucket=RAW_BUCKET, VersioningConfiguration={"Status": "Enabled"})
        print(f"versioning on {RAW_BUCKET}: turned on")


def catalog_session(base: str) -> requests.Session:
    """Log in as the root principal made at bootstrap; return a session carrying the token."""
    try:
        token = requests.post(
            f"{base}/api/catalog/v1/oauth/tokens",
            auth=(os.environ["POLARIS_CLIENT_ID"], os.environ["POLARIS_CLIENT_SECRET"]),
            headers={"Polaris-Realm": REALM},
            data={"grant_type": "client_credentials", "scope": "PRINCIPAL_ROLE:ALL"},
            timeout=TIMEOUT,
        )
    except requests.ConnectionError:
        stop(f"no catalog at {base}; is the stack up?")
    expect(token, 200)
    session = requests.Session()
    session.headers.update(
        {"Polaris-Realm": REALM, "Authorization": f"Bearer {token.json()['access_token']}"}
    )
    return session


def setup_catalog(api: requests.Session, base: str) -> None:
    management = f"{base}/api/management/v1"
    found = expect(api.get(f"{management}/catalogs/{CATALOG}", timeout=TIMEOUT), 200, 404)
    if found.status_code == 404:
        body = {
            "catalog": {
                "name": CATALOG,
                "type": "INTERNAL",
                "readOnly": False,
                "properties": CATALOG_PROPERTIES,
                "storageConfigInfo": {
                    "storageType": "S3",
                    "allowedLocations": ALLOWED_LOCATIONS,
                    "endpointInternal": STORE_FROM_POLARIS,
                    "region": REGION,
                    "pathStyleAccess": True,
                    # Static keys from Polaris's own environment; no credential vending.
                    "stsUnavailable": True,
                },
            }
        }
        expect(api.post(f"{management}/catalogs", json=body, timeout=TIMEOUT), 201)
        print(f"catalog {CATALOG}: created")
    else:
        have = found.json()
        differs = [
            f"{key}: has {have['properties'].get(key)!r}, expected {value!r}"
            for key, value in CATALOG_PROPERTIES.items()
            if have["properties"].get(key) != value
        ]
        allowed = have["storageConfigInfo"].get("allowedLocations", [])
        if sorted(allowed) != sorted(ALLOWED_LOCATIONS):
            differs.append(f"allowedLocations: has {allowed}, expected {ALLOWED_LOCATIONS}")
        if differs:
            stop(f"catalog {CATALOG} exists but differs. " + "; ".join(differs))
        print(f"catalog {CATALOG}: exists")

    # The root principal can manage the catalog but not its contents until catalog_admin
    # is granted them.
    grants_url = f"{management}/catalogs/{CATALOG}/catalog-roles/catalog_admin/grants"
    grants = expect(api.get(grants_url, timeout=TIMEOUT), 200).json().get("grants", [])
    if any(g.get("privilege") == "CATALOG_MANAGE_CONTENT" for g in grants):
        print("grant CATALOG_MANAGE_CONTENT to catalog_admin: exists")
    else:
        grant = {"type": "catalog", "privilege": "CATALOG_MANAGE_CONTENT"}
        expect(api.put(grants_url, json=grant, timeout=TIMEOUT), 201)
        print("grant CATALOG_MANAGE_CONTENT to catalog_admin: created")


def setup_namespaces(api: requests.Session, base: str) -> None:
    namespaces = f"{base}/api/catalog/v1/{CATALOG}/namespaces"
    for layer in LAYERS:
        want = layer_location(layer)
        found = expect(api.get(f"{namespaces}/{layer}", timeout=TIMEOUT), 200, 404)
        if found.status_code == 404:
            body = {"namespace": [layer], "properties": {"location": want}}
            expect(api.post(namespaces, json=body, timeout=TIMEOUT), 200)
            print(f"namespace {layer} at {want}: created")
            continue
        have = found.json().get("properties", {}).get("location", "")
        if have.rstrip("/") != want:
            stop(f"namespace {layer} exists at {have!r}, expected {want!r}")
        print(f"namespace {layer} at {want}: exists")


def main() -> None:
    setup_buckets()
    base = os.environ["POLARIS_ENDPOINT"].rstrip("/")
    api = catalog_session(base)
    setup_catalog(api, base)
    setup_namespaces(api, base)
    print("setup complete")


if __name__ == "__main__":
    main()
