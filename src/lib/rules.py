"""Firewall rules for the two-tier proof.

A rule names service accounts, never network tags. The audit is what
verify uses when it reads the rules back.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.lib.config import (
    DB_PORT,
    IAP_TCP_RANGE,
    RULE_DENY,
    RULE_HTTP,
    RULE_POSTGRES,
    RULE_SSH,
    SA_DB,
    SA_TEST,
    SA_WEB,
    WEB_PORT,
)


@dataclass(frozen=True)
class FirewallRule:
    name: str
    priority: int
    action: str
    protocol: str
    ports: tuple[str, ...]
    source_ranges: tuple[str, ...]
    source_service_accounts: tuple[str, ...]
    target_service_accounts: tuple[str, ...]
    log_enabled: bool
    target_tags: tuple[str, ...] = ()
    source_tags: tuple[str, ...] = ()


def build_rules() -> tuple[FirewallRule, ...]:
    accounts = (SA_WEB, SA_DB, SA_TEST)
    return (
        FirewallRule(
            name=RULE_HTTP,
            priority=1000,
            action="ALLOW",
            protocol="tcp",
            ports=(str(WEB_PORT),),
            source_ranges=("0.0.0.0/0",),
            source_service_accounts=(),
            target_service_accounts=(SA_WEB,),
            log_enabled=False,
        ),
        FirewallRule(
            name=RULE_SSH,
            priority=1000,
            action="ALLOW",
            protocol="tcp",
            ports=("22",),
            source_ranges=(IAP_TCP_RANGE,),
            source_service_accounts=(),
            target_service_accounts=accounts,
            log_enabled=False,
        ),
        FirewallRule(
            name=RULE_POSTGRES,
            priority=1000,
            action="ALLOW",
            protocol="tcp",
            ports=(str(DB_PORT),),
            source_ranges=(),
            source_service_accounts=(SA_WEB,),
            target_service_accounts=(SA_DB,),
            log_enabled=False,
        ),
        FirewallRule(
            name=RULE_DENY,
            priority=65000,
            action="DENY",
            protocol="all",
            ports=(),
            source_ranges=("0.0.0.0/0",),
            source_service_accounts=(),
            target_service_accounts=(),
            log_enabled=True,
        ),
    )


def _matches(value: str, short_name: str) -> bool:
    return value == short_name or value.startswith(f"{short_name}@")


def _has(values: tuple[str, ...], short_name: str) -> bool:
    return any(_matches(value, short_name) for value in values)


def audit_rules(rules: tuple[FirewallRule, ...] | list[FirewallRule]) -> list[str]:
    """Return one string per problem. An empty list is a pass."""
    problems: list[str] = []
    named = {rule.name: rule for rule in rules}

    for rule in rules:
        if rule.target_tags or rule.source_tags:
            problems.append(f"{rule.name} uses a network tag")

    database_rules = [
        rule
        for rule in rules
        if rule.action == "ALLOW" and (str(DB_PORT) in rule.ports or rule.protocol == "all")
        and (not rule.target_service_accounts or _has(rule.target_service_accounts, SA_DB))
    ]
    postgres = [rule for rule in database_rules if str(DB_PORT) in rule.ports]
    if len(postgres) != 1:
        problems.append("the database port is not exposed by exactly one rule")
    for rule in postgres:
        if rule.source_ranges and "0.0.0.0/0" in rule.source_ranges:
            problems.append(f"{rule.name} opens the database to 0.0.0.0/0")
        if not _has(rule.source_service_accounts, SA_WEB):
            problems.append(f"{rule.name} does not come from {SA_WEB}")
        if rule.source_service_accounts and not all(
            _matches(value, SA_WEB) for value in rule.source_service_accounts
        ):
            problems.append(f"{rule.name} has a source other than {SA_WEB}")
        if not _has(rule.target_service_accounts, SA_DB):
            problems.append(f"{rule.name} does not target {SA_DB}")

    ssh = named.get(RULE_SSH)
    if ssh is None:
        problems.append("the Secure Shell rule is missing")
    else:
        if ssh.source_ranges != (IAP_TCP_RANGE,):
            problems.append("Secure Shell is not limited to the Identity-Aware Proxy range")
        if "0.0.0.0/0" in ssh.source_ranges:
            problems.append("Secure Shell is open to 0.0.0.0/0")

    http = named.get(RULE_HTTP)
    if http is None or not _has(http.target_service_accounts, SA_WEB):
        problems.append("HTTP does not target the web identity only")
    elif any(not _matches(value, SA_WEB) for value in http.target_service_accounts):
        problems.append("HTTP targets an identity other than the web tier")

    deny = named.get(RULE_DENY)
    if deny is None:
        problems.append("the explicit deny is missing")
    elif not deny.log_enabled or deny.action != "DENY" or deny.priority != 65000:
        problems.append("the explicit deny is missing or silent")
    elif any(rule.priority > deny.priority for rule in rules if rule.name != RULE_DENY):
        problems.append("the explicit deny is not last")

    return problems
