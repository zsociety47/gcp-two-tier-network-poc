"""Service-account emails and the check that a password is absent."""

from __future__ import annotations

from src.lib.config import SA_DB, SA_WEB, ConfigError


def service_account_email(project_id: str, account_id: str) -> str:
    return f"{account_id}@{project_id}.iam.gserviceaccount.com"


def secret_reader_emails(project_id: str) -> tuple[str, str]:
    return (
        service_account_email(project_id, SA_WEB),
        service_account_email(project_id, SA_DB),
    )


def assert_password_absent(password: str, documents: list[str]) -> None:
    """Refuse an empty password. A hit means the value was stored somewhere."""
    if not password:
        raise ConfigError("An empty password is not proof that the secret is absent.")
    for document in documents:
        if password in document:
            raise ConfigError("The database password was found in instance metadata.")
