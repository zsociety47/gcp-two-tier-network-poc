"""The gcloud command-line interface, for the project shell only.

Creating the project, linking billing, enabling an API, reading the
project's state, and deleting the project go through this wrapper.
Everything inside the project uses the Python Software Development Kit
(SDK). That split is a finding to confirm on the first live run: a Python
client signed in with Application Default Credentials (ADC) may bill the
create-project call to a quota project that does not exist yet. This file
does not claim that has been observed.

Command forms checked October 27, 2026 against the installed Google Cloud
SDK 571.0.0 and the current gcloud reference:

- `gcloud projects create PROJECT_ID --name=NAME --labels=KEY=VALUE`
- `gcloud billing projects link PROJECT_ID --billing-account=ACCOUNT_ID`
- `gcloud projects describe` and `gcloud projects delete` (delete uses the
  wide flag `--quiet`, which the reference lists for every command)
- `gcloud services enable`, the same form project 1 already ran

Nothing in phase 1 calls `run`. `--plan` only builds argument lists.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from src.lib.config import (
    ConfigError,
    validate_billing_account,
    validate_created_label,
    validate_label_key,
    validate_label_value,
    validate_project_id,
)


class GcloudError(RuntimeError):
    """gcloud failed. The message has already had secrets removed."""


def redact(text: str, secrets: tuple[str, ...]) -> str:
    cleaned = text
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned


def create_project_command(
    project_id: str, display_name: str, labels: dict[str, str]
) -> list[str]:
    """Build the create command. Does not set it as the gcloud default project."""
    validate_project_id(project_id)
    if not labels:
        raise ConfigError("A new project needs the four sweep labels.")
    pairs: list[str] = []
    for key in ("lifetime", "owner", "project", "created"):
        if key not in labels:
            raise ConfigError(f"The create command is missing the {key} label.")
        validate_label_key(key)
        validate_label_value(labels[key])
        pairs.append(f"{key}={labels[key]}")
    validate_created_label(labels["created"])
    return [
        "gcloud",
        "projects",
        "create",
        project_id,
        f"--name={display_name}",
        f"--labels={','.join(pairs)}",
    ]


def link_billing_command(project_id: str, billing_account: str) -> list[str]:
    validate_project_id(project_id)
    validate_billing_account(billing_account)
    return [
        "gcloud",
        "billing",
        "projects",
        "link",
        project_id,
        f"--billing-account={billing_account}",
    ]


def describe_project_command(project_id: str) -> list[str]:
    validate_project_id(project_id)
    return ["gcloud", "projects", "describe", project_id, "--format=json"]


def delete_project_command(project_id: str) -> list[str]:
    validate_project_id(project_id)
    return ["gcloud", "projects", "delete", project_id, "--quiet"]


def enable_api_command(project_id: str, api: str) -> list[str]:
    validate_project_id(project_id)
    if not isinstance(api, str) or not api.endswith(".googleapis.com"):
        raise ConfigError("An API id must end with .googleapis.com.")
    return ["gcloud", "services", "enable", api, f"--project={project_id}"]


@dataclass(frozen=True)
class ProjectView:
    """What callers are allowed to see. The project number is not here."""

    project_id: str
    lifecycle_state: str
    labels: dict[str, str]


def parse_project(payload: dict) -> ProjectView:
    """Drop the project number before anything can print the raw document."""
    project_id = payload.get("projectId", "")
    validate_project_id(project_id)
    labels = payload.get("labels") or {}
    if not isinstance(labels, dict):
        raise ConfigError("Project labels were not a map.")
    return ProjectView(
        project_id=project_id,
        lifecycle_state=str(payload.get("lifecycleState", "")),
        labels={str(key): str(value) for key, value in labels.items()},
    )


def parse_project_json(raw: str) -> ProjectView:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ConfigError("gcloud projects describe did not return JSON.") from error
    if not isinstance(payload, dict):
        raise ConfigError("gcloud projects describe did not return a JSON object.")
    return parse_project(payload)


def run(command: list[str], redact_values: tuple[str, ...] = ()) -> str:
    """Run gcloud. Do not call this from `--plan`.

    The command list is not included in the error, because the billing
    link command contains the billing account id.
    """
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    stdout = redact(completed.stdout, redact_values)
    stderr = redact(completed.stderr, redact_values)
    if completed.returncode != 0:
        message = stderr.strip() or stdout.strip() or f"gcloud exited {completed.returncode}"
        raise GcloudError(message)
    return stdout
