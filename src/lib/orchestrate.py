"""The order deploy and teardown follow. No Google Cloud calls live here."""

from __future__ import annotations

from src.lib.config import VMS


class DeployError(RuntimeError):
    """The deploy stopped. The message does not include a secret."""


def run_deploy(cloud) -> None:
    cloud.ensure_project()
    cloud.link_billing()
    cloud.enable_apis()
    cloud.wait_for_compute()
    cloud.delete_default_network()
    cloud.ensure_service_accounts()
    cloud.ensure_secret()
    cloud.grant_operator()
    cloud.ensure_network()
    cloud.ensure_subnets()
    cloud.ensure_firewall()
    cloud.ensure_addresses()
    cloud.ensure_nat()
    for spec in VMS:
        cloud.ensure_vm(spec)
        if spec.ready_attribute:
            ready = cloud.wait_ready(spec)
            if str(ready).startswith("FAILED"):
                raise DeployError(
                    f"{spec.name} reported {ready}. Read the serial console: "
                    f"gcloud compute instances get-serial-port-output {spec.name}"
                )


def run_teardown_resources(cloud) -> None:
    cloud.delete_vms()
    cloud.delete_router()
    cloud.delete_firewall()
    cloud.delete_subnets()
    cloud.delete_network()
    cloud.delete_addresses()
    cloud.delete_secret()
    cloud.delete_service_accounts()


def confirm_teardown(cloud, typed: str, project_id: str, *, resources_only: bool) -> str:
    """A wrong typed name deletes nothing."""
    if typed != project_id:
        return "refused"
    if resources_only:
        run_teardown_resources(cloud)
        return "resources"
    cloud.delete_project()
    return "project"
