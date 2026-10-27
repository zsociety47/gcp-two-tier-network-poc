"""Plan the two-tier network proof of concept.

Run from the repository root:

    python -m src.deploy --plan

`--plan` prints the names and calls nothing. It works when .env does not
exist yet. The live deploy is a later phase and needs your approval before
it creates a Google Cloud project.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.lib.config import (
    APIS,
    DB_PORT,
    DISPLAY_NAME,
    IAP_TCP_RANGE,
    IP_NAT,
    IP_WEB,
    NAT_NAME,
    PAGE_MARKER,
    PROJECT_LABEL,
    ROUTER_NAME,
    RULE_DENY,
    RULE_HTTP,
    RULE_POSTGRES,
    RULE_SSH,
    SA_DB,
    SA_TEST,
    SA_WEB,
    SECRET_NAME,
    SUBNETS,
    VPC_NAME,
    VMS,
    WEB_PORT,
    ConfigError,
    Settings,
    load_settings,
    validate_display_name,
    validate_fixed_address,
    validate_service_account_id,
)


def plan_lines(settings: Settings) -> list[str]:
    """The whole plan, as lines. No Google Cloud call, no secret in the text."""
    validate_display_name(DISPLAY_NAME)
    for subnet in SUBNETS:
        validate_fixed_address(subnet.prefix, subnet.address)

    if settings.project_id:
        project = settings.project_id
    else:
        project = "not set. Copy .env.example to .env and set GCP_PROJECT_ID."

    if settings.billing_account:
        billing = "set in .env. The id is not printed."
    else:
        billing = "not set. Live deploy refuses to run until .env has GCP_BILLING_ACCOUNT."

    lines = [
        "Plan only. Nothing is created in Google Cloud.",
        f"Project id: {project}",
        f"Display name: {DISPLAY_NAME}",
        f"Region: {settings.region}",
        f"Billing account: {billing}",
        (
            "Labels: lifetime=1d, owner=zeon, "
            f"project={PROJECT_LABEL}, created=<Coordinated Universal Time (UTC) "
            "like 2026-10-26t23-45-00z>"
        ),
        "A second deploy reuses what exists. It does not rotate the database password.",
        "",
        "Order:",
        "1. Create the project with those labels, or reuse it when the labels match.",
        "2. Link billing. The account id stays in .env.",
        "3. Enable application programming interfaces (APIs): " + ", ".join(APIS),
        "4. Wait until the first Compute Engine call is accepted. Retry only not-ready errors.",
        "5. Delete the default network and its allow rules.",
        f"6. Service accounts: {SA_WEB}, {SA_DB}, {SA_TEST}.",
        (
            f"   {SA_DB} is {len(SA_DB)} characters. The Identity and Access Management "
            "API requires 6 to 30. This plan does not rename it."
        ),
        (
            f"7. Secret {SECRET_NAME}, one replica in the region. "
            f"Readers: {SA_WEB} and {SA_DB} only. The password is not printed."
        ),
        (
            "8. Operator roles for Identity-Aware Proxy (IAP) shell access. "
            "The signed-in email is not printed."
        ),
        f"9. Virtual Private Cloud (VPC) {VPC_NAME}, custom mode, no automatic subnets.",
    ]
    for subnet in SUBNETS:
        if subnet.nat:
            nat = (
                f", Network Address Translation (NAT) gateway {NAT_NAME} on {IP_NAT}"
            )
        else:
            nat = ", no Network Address Translation"
        lines.append(
            f"   Subnet {subnet.name} {subnet.prefix}, address {subnet.address}, "
            f"Private Google Access on{nat}."
        )
    lines.extend(
        [
            (
                f"10. Firewall {RULE_HTTP}: Transmission Control Protocol (TCP) "
                f"port {WEB_PORT} from anywhere, target {SA_WEB}."
            ),
            (
                f"    Firewall {RULE_SSH}: Secure Shell (SSH), TCP port 22 from "
                f"{IAP_TCP_RANGE} only, targets {SA_WEB}, {SA_DB}, {SA_TEST}."
            ),
            (
                f"    Firewall {RULE_POSTGRES}: TCP port {DB_PORT} from {SA_WEB} "
                f"to {SA_DB} only."
            ),
            (
                f"    Firewall {RULE_DENY}: deny all other ingress, priority 65000, logging on."
            ),
            "    No rule uses a network tag. The IAP range is not the internet.",
            f"11. Reserved addresses: {IP_WEB} for the web page, {IP_NAT} for NAT.",
            f"12. Cloud Router {ROUTER_NAME}, then {NAT_NAME} serving snet-db only.",
            "13. Virtual machines, in this order. Each runs as its own service account.",
        ]
    )
    for vm in VMS:
        if vm.external_address:
            public = f"external address {vm.external_address}"
        else:
            public = "no external address"
        if vm.ready_attribute:
            ready = f"ready flag {vm.ready_attribute}"
        else:
            ready = "no ready flag"
        lines.append(
            f"    {vm.name} at {vm.address} ({public}), "
            f"service account {vm.service_account}, {ready}."
        )
    lines.extend(
        [
            (
                f"The web page on port {WEB_PORT} must contain the text {PAGE_MARKER}. "
                "The database password is read from Secret Manager at run time."
            ),
            "Build order for the machines: database, then web, then test.",
        ]
    )
    return lines


def print_plan(settings: Settings, out=None) -> None:
    sink = sys.stdout if out is None else out
    for line in plan_lines(settings):
        print(line, file=sink)


def _account_ids_that_fit() -> list[str]:
    """Used only so a bad rename of a legal id fails the plan before a live run."""
    fitting = []
    for account_id in (SA_WEB, SA_TEST):
        validate_service_account_id(account_id)
        fitting.append(account_id)
    return fitting


def main(argv: list[str] | None = None, env_path: Path | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan or, in a later phase, create the two-tier network proof."
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Print what would be created. Calls nothing.",
    )
    args = parser.parse_args(argv)
    if not args.plan:
        print(
            "Live deploy is not part of this phase. "
            "Run: python -m src.deploy --plan",
            file=sys.stderr,
        )
        return 2
    try:
        _account_ids_that_fit()
        settings = load_settings(env_path)
        print_plan(settings)
    except ConfigError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
