"""Names, addresses, and labels for the two-tier proof of concept.

Nothing in this file talks to Google Cloud. Deploy, verify, and teardown
import these names so they cannot drift apart. The project id and the
billing account id come from .env. They are not constants here.

The picture's project id, twotier-poc-zeon01, is a placeholder. Pick a
fresh id in .env at build time. A deleted project id cannot be reused
for up to 30 days.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Human name on the project. 4 to 30 characters, from the Resource Manager
# "Create projects" page (checked October 27, 2026).
DISPLAY_NAME = "02 two tier network poc"
PROJECT_LABEL = "02-gcp-two-tier-network-poc"
OWNER_LABEL = "zeon"
LIFETIME_LABEL = "1d"
DEFAULT_REGION = "us-east1"

# Starting size and image. Not checked against this account yet. Before the
# first virtual machine is created, ask the project whether e2-small is
# offered in each zone, and confirm the image family exists.
MACHINE_TYPE = "e2-small"
ZONES = ("us-east1-b", "us-east1-c", "us-east1-d")
IMAGE_FAMILY = "ubuntu-2404-lts-amd64"
IMAGE_PROJECT = "ubuntu-os-cloud"
DISK_GB = 20
DISK_TYPE = "pd-balanced"

# Design value. The firewall phase checks this against Identity-Aware Proxy
# (IAP) documentation before any rule is written.
IAP_TCP_RANGE = "35.235.240.0/20"

VPC_NAME = "vpc-twotier-poc"
ROUTER_NAME = "router-twotier-poc"
NAT_NAME = "nat-twotier-poc"

WEB_SUBNET = "snet-web"
DB_SUBNET = "snet-db"
TEST_SUBNET = "snet-test"
WEB_PREFIX = "10.20.1.0/24"
DB_PREFIX = "10.20.2.0/24"
TEST_PREFIX = "10.20.3.0/24"
WEB_ADDRESS = "10.20.1.10"
DB_ADDRESS = "10.20.2.10"
TEST_ADDRESS = "10.20.3.10"

IP_WEB = "ip-web-01"
IP_NAT = "ip-nat-01"

SA_WEB = "sa-web"
# The design name sa-db is 5 characters. The Identity and Access Management
# API requires 6 to 30, so the id is one step longer.
SA_DB = "sa-db-01"
SA_TEST = "sa-test"

SECRET_NAME = "twotier-db-password"
DB_PORT = 5432
WEB_PORT = 80
PAGE_MARKER = "database-connected"

VM_DB = "vm-db-01"
VM_WEB = "vm-web-01"
VM_TEST = "vm-test-01"

RULE_HTTP = "allow-http-to-web"
RULE_SSH = "allow-ssh-from-iap"
RULE_POSTGRES = "allow-postgres-web-to-db"
RULE_DENY = "deny-all-other-ingress"

# Product API ids. The first live enable is the check that each one is accepted.
APIS = (
    "compute.googleapis.com",
    "iam.googleapis.com",
    "iap.googleapis.com",
    "oslogin.googleapis.com",
    "secretmanager.googleapis.com",
)

PROJECT_ID_RE = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")
BILLING_ACCOUNT_RE = re.compile(r"[0-9A-Fa-f]{6}-[0-9A-Fa-f]{6}-[0-9A-Fa-f]{6}")
LABEL_KEY_RE = re.compile(r"[a-z][a-z0-9_-]{0,62}")
LABEL_VALUE_RE = re.compile(r"[a-z0-9_-]{1,63}")
CREATED_LABEL_RE = re.compile(r"\d{4}-\d{2}-\d{2}t\d{2}-\d{2}-\d{2}z")
REGION_RE = re.compile(r"[a-z]+(-[a-z0-9]+)+")
RESTRICTED_PROJECT_WORDS = ("google", "ssl", "null", "undefined")


class ConfigError(ValueError):
    """A name or a setting failed a check. The message never includes a secret."""


@dataclass(frozen=True)
class SubnetSpec:
    name: str
    prefix: str
    address: str
    nat: bool


@dataclass(frozen=True)
class VmSpec:
    name: str
    subnet: str
    address: str
    service_account: str
    external_address: str | None
    ready_attribute: str | None


SUBNETS = (
    SubnetSpec(WEB_SUBNET, WEB_PREFIX, WEB_ADDRESS, nat=False),
    SubnetSpec(DB_SUBNET, DB_PREFIX, DB_ADDRESS, nat=True),
    SubnetSpec(TEST_SUBNET, TEST_PREFIX, TEST_ADDRESS, nat=False),
)

# Database first, then the web tier, then the test machine.
VMS = (
    VmSpec(VM_DB, DB_SUBNET, DB_ADDRESS, SA_DB, None, "DB_READY"),
    VmSpec(VM_WEB, WEB_SUBNET, WEB_ADDRESS, SA_WEB, IP_WEB, "WEB_READY"),
    VmSpec(VM_TEST, TEST_SUBNET, TEST_ADDRESS, SA_TEST, None, None),
)


def validate_project_id(project_id: str) -> str:
    """Reject an id Google Cloud would reject.

    Rules from the Resource Manager "Create projects" page, checked
    October 27, 2026: 6 to 30 characters, lowercase letters, digits, and
    hyphens, starts with a letter, does not end with a hyphen, and does
    not contain google, ssl, null, or undefined.
    """
    if not isinstance(project_id, str) or PROJECT_ID_RE.fullmatch(project_id) is None:
        raise ConfigError(
            "GCP_PROJECT_ID must be 6 to 30 characters, start with a letter, "
            "end with a letter or digit, and use only lowercase letters, digits, and hyphens."
        )
    for word in RESTRICTED_PROJECT_WORDS:
        if word in project_id:
            raise ConfigError(
                "GCP_PROJECT_ID cannot contain google, ssl, null, or undefined."
            )
    return project_id


def validate_billing_account(billing_account: str) -> str:
    """Check the shape only. The value is never copied into the error."""
    if (
        not isinstance(billing_account, str)
        or BILLING_ACCOUNT_RE.fullmatch(billing_account) is None
    ):
        raise ConfigError(
            "GCP_BILLING_ACCOUNT must be three groups of 6 hexadecimal "
            "characters, separated by hyphens. The value is not printed."
        )
    return billing_account


def validate_region(region: str) -> str:
    if not isinstance(region, str) or REGION_RE.fullmatch(region) is None:
        raise ConfigError("GCP_REGION should look like us-east1.")
    return region


def validate_label_key(key: str) -> str:
    """Keys start with a lowercase letter. From `gcloud projects create --help`."""
    if not isinstance(key, str) or LABEL_KEY_RE.fullmatch(key) is None:
        raise ConfigError(
            "A label key must start with a lowercase letter and use only "
            "lowercase letters, digits, hyphens, and underscores."
        )
    return key


def validate_label_value(value: str) -> str:
    """Values cannot contain a colon. A colon is how `created` used to be written."""
    if not isinstance(value, str) or LABEL_VALUE_RE.fullmatch(value) is None:
        raise ConfigError(
            "A label value must be 1 to 63 characters and use only lowercase "
            "letters, digits, hyphens, and underscores. A colon is not allowed."
        )
    return value


def validate_created_label(value: str) -> str:
    """The sweep reads this form: 2026-10-26t23-45-00z."""
    validate_label_value(value)
    if CREATED_LABEL_RE.fullmatch(value) is None:
        raise ConfigError(
            "The created label must look like 2026-10-26t23-45-00z. "
            "A colon is not allowed."
        )
    return value


def format_created_label(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ConfigError("The created label needs a timezone-aware UTC time.")
    text = moment.astimezone(timezone.utc).strftime("%Y-%m-%dt%H-%M-%Sz")
    return validate_created_label(text)


def validate_service_account_id(account_id: str) -> str:
    """6 to 30 characters, from the Identity and Access Management API.

    The discovery document for CreateServiceAccountRequest says the account
    id must be 6 to 30 characters and match `[a-z]([-a-z0-9]*[a-z0-9])`.
    Checked October 27, 2026. `sa-db` is 5 characters, so this function
    rejects the design name. It is not renamed here.
    """
    if (
        not isinstance(account_id, str)
        or not 6 <= len(account_id) <= 30
        or re.fullmatch(r"[a-z]([-a-z0-9]*[a-z0-9])", account_id) is None
    ):
        raise ConfigError(
            "A service account id must be 6 to 30 characters, start with a "
            "lowercase letter, and use only lowercase letters, digits, and hyphens."
        )
    return account_id


def validate_fixed_address(prefix: str, address: str) -> None:
    """Reject the four reserved addresses in a subnet.

    The design treats the first two and the last two addresses as reserved,
    so the fixed addresses use .10. This check is arithmetic. It has not
    been confirmed by reserving an address in Google Cloud.
    """
    network = ipaddress.ip_network(prefix)
    host = ipaddress.ip_address(address)
    if host not in network:
        raise ConfigError(f"{address} is not inside {prefix}.")
    reserved = {
        network.network_address,
        network.network_address + 1,
        network.broadcast_address - 1,
        network.broadcast_address,
    }
    if host in reserved:
        raise ConfigError(f"{address} is reserved in {prefix}. Use a host such as .10.")


def our_labels(created: str) -> dict[str, str]:
    created = validate_created_label(created)
    labels = {
        "lifetime": LIFETIME_LABEL,
        "owner": OWNER_LABEL,
        "project": PROJECT_LABEL,
        "created": created,
    }
    for key, value in labels.items():
        validate_label_key(key)
        validate_label_value(value)
    return labels


def assert_our_labels(labels: dict[str, str]) -> None:
    """Refuse a project that does not carry this proof's labels.

    The created value may differ between runs. The other three must match.
    Foreign label values are not copied into the error.
    """
    expected = {
        "lifetime": LIFETIME_LABEL,
        "owner": OWNER_LABEL,
        "project": PROJECT_LABEL,
    }
    mismatched = [key for key, value in expected.items() if labels.get(key) != value]
    created = labels.get("created", "")
    try:
        validate_created_label(created)
    except ConfigError:
        mismatched.append("created")
    if mismatched:
        names = ", ".join(mismatched)
        raise ConfigError(
            "Refusing to touch this project. "
            f"These labels do not match this proof: {names}."
        )


def validate_display_name(name: str) -> str:
    """4 to 30 characters. From the same Create projects page as the id rules."""
    if (
        not isinstance(name, str)
        or not 4 <= len(name) <= 30
        or re.fullmatch(r"[A-Za-z0-9' !-]+", name) is None
        or re.fullmatch(r".*[A-Za-z0-9]", name) is None
    ):
        raise ConfigError(
            "The project display name must be 4 to 30 characters, use letters, "
            "digits, spaces, hyphens, quotes, or exclamation points, and end "
            "with a letter or digit."
        )
    return name


@dataclass(frozen=True)
class Settings:
    project_id: str | None
    billing_account: str | None
    region: str


def load_env_file(path: Path) -> dict[str, str]:
    """Read KEY=value lines. Missing file is an empty map, not an error."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def settings_from_env(env: dict[str, str]) -> Settings:
    """Validate what is present. Blank project id and billing id are allowed.

    `--plan` has to work before .env exists. A present but illegal value
    is an error, and the billing account id is not included in that error.
    """
    project_id = env.get("GCP_PROJECT_ID", "").strip()
    billing_account = env.get("GCP_BILLING_ACCOUNT", "").strip()
    region = env.get("GCP_REGION", "").strip() or DEFAULT_REGION
    return Settings(
        project_id=validate_project_id(project_id) if project_id else None,
        billing_account=validate_billing_account(billing_account) if billing_account else None,
        region=validate_region(region),
    )


def load_settings(env_path: Path | None = None) -> Settings:
    path = REPO_ROOT / ".env" if env_path is None else env_path
    return settings_from_env(load_env_file(path))
