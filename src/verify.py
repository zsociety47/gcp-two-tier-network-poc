"""Check the ten success criteria.

    python -m src.verify
    python -m src.verify --after-teardown
    python -m src.verify --after-teardown --resources-only
"""

from __future__ import annotations

import argparse
import sys

from src.lib.config import PAGE_MARKER, ConfigError, load_settings
from src.lib.identity import assert_password_absent, secret_reader_emails
from src.lib.rules import audit_rules


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the two-tier proof.")
    parser.add_argument("--after-teardown", action="store_true")
    parser.add_argument("--resources-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as error:
        print(str(error), file=sys.stderr)
        return 1
    if settings.project_id is None:
        print("Set GCP_PROJECT_ID in .env.", file=sys.stderr)
        return 1
    from src.lib.live import LiveCloud

    cloud = LiveCloud(settings)
    if args.after_teardown:
        return _after(cloud, resources_only=args.resources_only)
    rows = (
        ("1 web page", _row_page(cloud)),
        ("2 database has no public address", _row_no_public(cloud)),
        ("3 web reaches the database", _row_port(cloud, "vm-web-01", "open")),
        ("4 test machine is blocked", _row_port(cloud, "vm-test-01", "timeout")),
        ("5 firewall audit", _row_firewall(cloud)),
        ("6 password absent from metadata", _row_password(cloud)),
        ("7 secret readers", _row_readers(cloud)),
        ("8 NAT and Private Google Access", _row_nat(cloud)),
        ("9 project labels", _row_labels(cloud)),
    )
    failed = 0
    for name, (ok, detail) in rows:
        print(f"{'pass' if ok else 'fail'}  {name}: {detail}")
        failed += not ok
    print(f"{9 - failed} passed, {failed} failed")
    return 1 if failed else 0


def _after(cloud, *, resources_only: bool) -> int:
    state = cloud.project_state()
    if resources_only:
        ok = state == "ACTIVE"
        detail = f"project state {state}"
    else:
        ok = state in {"DELETE_REQUESTED", "GONE", "DELETE_IN_PROGRESS"}
        detail = f"project state {state}"
    print(f"{'pass' if ok else 'fail'}  10 teardown: {detail}")
    return 0 if ok else 1


def _row_page(cloud) -> tuple[bool, str]:
    status, body = cloud.http_text()
    ok = status == 200 and PAGE_MARKER in body
    return ok, f"HTTP {status}"


def _row_no_public(cloud) -> tuple[bool, str]:
    from google.cloud import compute_v1

    client = compute_v1.InstancesClient(credentials=cloud.credentials())
    zone = cloud.zone_of("vm-db-01")
    instance = client.get(project=cloud.project, zone=zone, instance="vm-db-01")
    access = list(instance.network_interfaces[0].access_configs or [])
    return (not access), ("no external address" if not access else "external address present")


def _row_port(cloud, name: str, expected: str) -> tuple[bool, str]:
    try:
        result = cloud.port_test(name)
    except Exception as error:
        return False, str(error).splitlines()[0][:160]
    return result == expected, result


def _row_firewall(cloud) -> tuple[bool, str]:
    from google.cloud import compute_v1

    from src.lib.rules import FirewallRule

    client = compute_v1.FirewallsClient(credentials=cloud.credentials())
    rules = []
    for firewall in client.list(project=cloud.project):
        if firewall.allowed:
            action, entry = "ALLOW", firewall.allowed[0]
        elif firewall.denied:
            action, entry = "DENY", firewall.denied[0]
        else:
            continue
        rules.append(
            FirewallRule(
                name=firewall.name,
                priority=firewall.priority,
                action=action,
                protocol=entry.I_p_protocol,
                ports=tuple(entry.ports),
                source_ranges=tuple(firewall.source_ranges),
                source_service_accounts=tuple(firewall.source_service_accounts),
                target_service_accounts=tuple(firewall.target_service_accounts),
                log_enabled=bool(firewall.log_config and firewall.log_config.enable),
                target_tags=tuple(firewall.target_tags),
                source_tags=tuple(firewall.source_tags),
            )
        )
    problems = audit_rules(tuple(rules))
    return (not problems), ("clean" if not problems else "; ".join(problems))


def _row_password(cloud) -> tuple[bool, str]:
    from google.cloud import compute_v1, secretmanager

    client = secretmanager.SecretManagerServiceClient(credentials=cloud.credentials())
    resource = f"projects/{cloud.project}/secrets/twotier-db-password/versions/latest"
    response = client.access_secret_version(request={"name": resource})
    password = response.payload.data.decode()
    documents = []
    instances = compute_v1.InstancesClient(credentials=cloud.credentials())
    for spec_name in ("vm-web-01", "vm-db-01", "vm-test-01"):
        zone = cloud.zone_of(spec_name)
        instance = instances.get(project=cloud.project, zone=zone, instance=spec_name)
        for item in instance.metadata.items or []:
            documents.append(item.value or "")
    try:
        assert_password_absent(password, documents)
    except ConfigError:
        return False, "found or empty"
    finally:
        del password
    return True, "not found"


def _row_readers(cloud) -> tuple[bool, str]:
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient(credentials=cloud.credentials())
    resource = f"projects/{cloud.project}/secrets/twotier-db-password"
    policy = client.get_iam_policy(request={"resource": resource})
    found = []
    for binding in policy.bindings:
        if binding.role == "roles/secretmanager.secretAccessor":
            found.extend(binding.members)
    expected = {f"serviceAccount:{email}" for email in secret_reader_emails(cloud.project)}
    return set(found) == expected, f"{len(found)} readers"


def _row_nat(cloud) -> tuple[bool, str]:
    from google.cloud import compute_v1

    routers = compute_v1.RoutersClient(credentials=cloud.credentials())
    router = routers.get(project=cloud.project, region=cloud.region, router="router-twotier-poc")
    subnets = []
    for nat in router.nats:
        for sub in nat.subnetworks:
            subnets.append(sub.name)
    only_db = len(subnets) == 1 and subnets[0].endswith("/snet-db")
    client = compute_v1.SubnetworksClient(credentials=cloud.credentials())
    private = []
    for name in ("snet-web", "snet-db", "snet-test"):
        subnet = client.get(project=cloud.project, region=cloud.region, subnetwork=name)
        private.append(bool(subnet.private_ip_google_access))
    ok = only_db and all(private)
    return ok, ("snet-db only, Private Google Access on" if ok else "mismatch")


def _row_labels(cloud) -> tuple[bool, str]:
    labels = cloud.project_labels()
    expected = cloud.expected_labels()
    missing = [key for key, value in expected.items() if labels.get(key) != value]
    if "created" not in labels or ":" in labels.get("created", ""):
        missing.append("created")
    return (not missing), ("four labels" if not missing else ", ".join(missing))


if __name__ == "__main__":
    sys.exit(main())
