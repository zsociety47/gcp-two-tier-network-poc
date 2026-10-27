"""Calls into Google Cloud. Deploy and teardown go through this object.

Project create, billing, and API enable use the gcloud command-line
interface. Everything else uses the Python client libraries, with this
project set as the quota project. A user access token is read from gcloud
and is never printed.
"""

from __future__ import annotations

import json
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from google.api_core.exceptions import NotFound
from google.cloud import compute_v1, iam_admin_v1, secretmanager
from google.oauth2.credentials import Credentials

from src.lib.config import (
    APIS,
    DB_ADDRESS,
    DB_PORT,
    DISK_GB,
    DISK_TYPE,
    DISPLAY_NAME,
    IMAGE_FAMILY,
    IMAGE_PROJECT,
    IP_NAT,
    IP_WEB,
    LIFETIME_LABEL,
    MACHINE_TYPE,
    NAT_NAME,
    OWNER_LABEL,
    PAGE_MARKER,
    PROJECT_LABEL,
    ROUTER_NAME,
    SECRET_NAME,
    VM_DB,
    VM_WEB,
    VPC_NAME,
    VMS,
    ZONES,
    Settings,
    assert_our_labels,
    format_created_label,
    our_labels,
)
from src.lib.gcloud import (
    create_project_command,
    delete_project_command,
    describe_project_command,
    enable_api_command,
    link_billing_command,
    parse_project_json,
    run,
)
from src.lib.guest import load_script, startup_replacements
from src.lib.identity import secret_reader_emails, service_account_email
from src.lib.orchestrate import DeployError
from src.lib.retry import call_until_ready, is_capacity_exhausted, is_not_ready
from src.lib.rules import FirewallRule, build_rules
from src.lib.vms import metadata_for

CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"
OPERATOR_ROLES = (
    "roles/iap.tunnelResourceAccessor",
    "roles/compute.osAdminLogin",
    "roles/iam.serviceAccountUser",
)


class LiveCloud:
    def __init__(self, settings: Settings) -> None:
        if settings.project_id is None or settings.billing_account is None:
            raise DeployError("GCP_PROJECT_ID and GCP_BILLING_ACCOUNT are required.")
        self.settings = settings
        self.project = settings.project_id
        self.region = settings.region
        self.zones: dict[str, str] = {}
        self._created = format_created_label(datetime.now(timezone.utc))

    def step(self, text: str) -> None:
        print(f"==> {text}", flush=True)

    def credentials(self) -> Credentials:
        completed = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or "Could not read a gcloud access token."
            raise DeployError(message)
        token = completed.stdout.strip()
        return Credentials(token).with_quota_project(self.project)

    def email(self, account_id: str) -> str:
        return service_account_email(self.project, account_id)

    def operator_email(self) -> str:
        completed = subprocess.run(
            ["gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            raise DeployError("gcloud is not signed in.")
        return completed.stdout.strip().splitlines()[0]

    def ensure_project(self) -> None:
        self.step(f"Project {self.project}")
        completed = subprocess.run(
            describe_project_command(self.project),
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            view = parse_project_json(completed.stdout)
            assert_our_labels(view.labels)
            self.step("Project already exists and the labels match. Reusing it.")
            return
        labels = our_labels(self._created)
        run(create_project_command(self.project, DISPLAY_NAME, labels))
        self.step("Project created.")

    def link_billing(self) -> None:
        self.step("Linking billing. The account id is not printed.")
        call_until_ready(
            lambda: run(
                link_billing_command(self.project, self.settings.billing_account or ""),
                redact_values=(self.settings.billing_account or "",),
            ),
            attempts=12,
            sleep_seconds=10,
        )

    def enable_apis(self) -> None:
        self.step("Enabling APIs")
        for api in APIS:
            call_until_ready(
                lambda api=api: run(enable_api_command(self.project, api)),
                attempts=12,
                sleep_seconds=10,
            )
            print(f"    {api}", flush=True)

    def wait_for_compute(self) -> None:
        self.step("Waiting until Compute Engine accepts a call")

        def ping() -> None:
            compute_v1.NetworksClient(credentials=self.credentials()).list(project=self.project)

        call_until_ready(ping, attempts=18, sleep_seconds=10)

    def delete_default_network(self) -> None:
        self.step("Deleting the default network")
        creds = self.credentials()
        firewalls = compute_v1.FirewallsClient(credentials=creds)
        for rule in firewalls.list(project=self.project):
            if str(rule.network).endswith("/networks/default"):
                self._gone(lambda name=rule.name: firewalls.delete(
                    project=self.project, firewall=name
                ).result(timeout=300))
        networks = compute_v1.NetworksClient(credentials=creds)
        self._gone(lambda: networks.delete(project=self.project, network="default").result(timeout=300))

    def ensure_service_accounts(self) -> None:
        self.step("Service accounts")
        client = iam_admin_v1.IAMClient(credentials=self.credentials())
        for spec in VMS:
            account_id = spec.service_account
            name = f"projects/{self.project}/serviceAccounts/{self.email(account_id)}"
            try:
                client.get_service_account(request={"name": name})
                print(f"    {account_id} already exists", flush=True)
                continue
            except NotFound:
                pass
            call_until_ready(
                lambda account_id=account_id: client.create_service_account(
                    request={
                        "name": f"projects/{self.project}",
                        "account_id": account_id,
                        "service_account": {"display_name": account_id},
                    }
                ),
                attempts=8,
                sleep_seconds=5,
            )
            print(f"    {account_id}", flush=True)

    def ensure_secret(self) -> None:
        self.step("Secret")
        client = secretmanager.SecretManagerServiceClient(credentials=self.credentials())
        resource = f"projects/{self.project}/secrets/{SECRET_NAME}"
        try:
            client.get_secret(request={"name": resource})
            self.step("Secret already exists. Not adding a version.")
        except NotFound:
            password = secrets.token_urlsafe(24)
            try:
                client.create_secret(
                    request={
                        "parent": f"projects/{self.project}",
                        "secret_id": SECRET_NAME,
                        "secret": {
                            "replication": {
                                "user_managed": {"replicas": [{"location": self.region}]}
                            }
                        },
                    }
                )
                client.add_secret_version(
                    request={"parent": resource, "payload": {"data": password.encode()}}
                )
            finally:
                del password
            self.step("Secret stored. The value was not printed.")
        call_until_ready(
            lambda: self._set_secret_readers(client, resource),
            attempts=12,
            sleep_seconds=10,
        )

    def _set_secret_readers(self, client, resource: str) -> None:
        from google.iam.v1 import policy_pb2

        members = [f"serviceAccount:{email}" for email in secret_reader_emails(self.project)]
        current = client.get_iam_policy(request={"resource": resource})
        policy = policy_pb2.Policy(etag=current.etag)
        for binding in current.bindings:
            if binding.role != "roles/secretmanager.secretAccessor":
                policy.bindings.append(binding)
        policy.bindings.append(
            policy_pb2.Binding(role="roles/secretmanager.secretAccessor", members=members)
        )
        client.set_iam_policy(request={"resource": resource, "policy": policy})

    def grant_operator(self) -> None:
        self.step("Granting operator roles. The account is not printed.")
        member = f"user:{self.operator_email()}"
        for role in OPERATOR_ROLES:
            completed = subprocess.run(
                [
                    "gcloud",
                    "projects",
                    "add-iam-policy-binding",
                    self.project,
                    f"--member={member}",
                    f"--role={role}",
                    "--condition=None",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                message = completed.stderr.replace(member, "user:[redacted]")
                raise DeployError(message.strip() or f"Could not grant {role}.")
            print(f"    {role}", flush=True)

    def ensure_network(self) -> None:
        self.step(f"Network {VPC_NAME}")
        client = compute_v1.NetworksClient(credentials=self.credentials())
        try:
            client.get(project=self.project, network=VPC_NAME)
            print("    already exists", flush=True)
            return
        except NotFound:
            pass
        network = compute_v1.Network()
        network.name = VPC_NAME
        network.auto_create_subnetworks = False
        network.routing_config = compute_v1.NetworkRoutingConfig(routing_mode="REGIONAL")
        client.insert(project=self.project, network_resource=network).result(timeout=300)

    def ensure_subnets(self) -> None:
        self.step("Subnets")
        client = compute_v1.SubnetworksClient(credentials=self.credentials())
        network = f"projects/{self.project}/global/networks/{VPC_NAME}"
        for spec in __import__("src.lib.config", fromlist=["SUBNETS"]).SUBNETS:
            try:
                client.get(project=self.project, region=self.region, subnetwork=spec.name)
                print(f"    {spec.name} already exists", flush=True)
                continue
            except NotFound:
                pass
            subnet = compute_v1.Subnetwork()
            subnet.name = spec.name
            subnet.network = network
            subnet.ip_cidr_range = spec.prefix
            subnet.private_ip_google_access = True
            call_until_ready(
                lambda subnet=subnet: client.insert(
                    project=self.project, region=self.region, subnetwork_resource=subnet
                ).result(timeout=300),
                attempts=8,
                sleep_seconds=5,
            )
            print(f"    {spec.name} {spec.prefix}", flush=True)

    def ensure_firewall(self) -> None:
        self.step("Firewall rules")
        client = compute_v1.FirewallsClient(credentials=self.credentials())
        for rule in build_rules():
            try:
                client.get(project=self.project, firewall=rule.name)
                print(f"    {rule.name} already exists", flush=True)
                continue
            except NotFound:
                pass
            firewall = self._firewall(rule)
            call_until_ready(
                lambda firewall=firewall: client.insert(
                    project=self.project, firewall_resource=firewall
                ).result(timeout=300),
                attempts=8,
                sleep_seconds=5,
            )
            print(f"    {rule.name}", flush=True)

    def _firewall(self, rule: FirewallRule) -> compute_v1.Firewall:
        firewall = compute_v1.Firewall()
        firewall.name = rule.name
        firewall.network = f"projects/{self.project}/global/networks/{VPC_NAME}"
        firewall.direction = "INGRESS"
        firewall.priority = rule.priority
        if rule.action == "ALLOW":
            allowed = compute_v1.Allowed()
            allowed.I_p_protocol = rule.protocol
            if rule.ports:
                allowed.ports = list(rule.ports)
            firewall.allowed = [allowed]
        else:
            denied = compute_v1.Denied()
            denied.I_p_protocol = rule.protocol
            firewall.denied = [denied]
        if rule.source_ranges:
            firewall.source_ranges = list(rule.source_ranges)
        if rule.source_service_accounts:
            firewall.source_service_accounts = [
                self.email(account) for account in rule.source_service_accounts
            ]
        if rule.target_service_accounts:
            firewall.target_service_accounts = [
                self.email(account) for account in rule.target_service_accounts
            ]
        if rule.log_enabled:
            firewall.log_config = compute_v1.FirewallLogConfig(enable=True)
        return firewall

    def ensure_addresses(self) -> None:
        self.step("Reserved addresses")
        client = compute_v1.AddressesClient(credentials=self.credentials())
        for name in (IP_WEB, IP_NAT):
            try:
                client.get(project=self.project, region=self.region, address=name)
                print(f"    {name} already exists", flush=True)
                continue
            except NotFound:
                pass
            address = compute_v1.Address()
            address.name = name
            address.address_type = "EXTERNAL"
            client.insert(
                project=self.project, region=self.region, address_resource=address
            ).result(timeout=300)
            print(f"    {name}", flush=True)

    def ensure_nat(self) -> None:
        self.step("Cloud Router and NAT")
        routers = compute_v1.RoutersClient(credentials=self.credentials())
        try:
            routers.get(project=self.project, region=self.region, router=ROUTER_NAME)
            print("    already exists", flush=True)
            return
        except NotFound:
            pass
        addresses = compute_v1.AddressesClient(credentials=self.credentials())
        nat_address = addresses.get(project=self.project, region=self.region, address=IP_NAT)
        subnets = compute_v1.SubnetworksClient(credentials=self.credentials())
        database_subnet = subnets.get(project=self.project, region=self.region, subnetwork="snet-db")
        nat = compute_v1.RouterNat()
        nat.name = NAT_NAME
        nat.nat_ip_allocate_option = "MANUAL_ONLY"
        nat.nat_ips = [nat_address.self_link]
        nat.source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"
        subnetwork = compute_v1.RouterNatSubnetworkToNat()
        subnetwork.name = database_subnet.self_link
        subnetwork.source_ip_ranges_to_nat = ["PRIMARY_IP_RANGE"]
        nat.subnetworks = [subnetwork]
        router = compute_v1.Router()
        router.name = ROUTER_NAME
        router.network = f"projects/{self.project}/global/networks/{VPC_NAME}"
        router.nats = [nat]
        routers.insert(project=self.project, region=self.region, router_resource=router).result(timeout=300)

    def ensure_vm(self, spec) -> None:
        self.step(f"Virtual machine {spec.name}")
        zone = self.zone_of(spec.name)
        if zone:
            self.zones[spec.name] = zone
            print("    already exists", flush=True)
            return
        startup = ""
        if spec.name == VM_DB:
            startup = load_script("db-setup.sh", startup_replacements(self.project))
        elif spec.name == VM_WEB:
            startup = load_script("web-setup.sh", startup_replacements(self.project))
        offered = [zone for zone in ZONES if self.zone_offers(zone)]
        if not offered:
            raise DeployError(f"No zone in {self.region} offers {MACHINE_TYPE}.")
        last: Exception | None = None
        for zone in offered:
            try:
                call_until_ready(
                    lambda zone=zone: self._insert_vm(spec, zone, startup),
                    attempts=6,
                    sleep_seconds=10,
                )
                self.zones[spec.name] = zone
                print(f"    started in {zone}", flush=True)
                return
            except Exception as error:
                if not is_capacity_exhausted(error):
                    raise
                print(f"    {zone} is full", flush=True)
                last = error
        raise DeployError(f"No zone could start {spec.name}.") from last

    def zone_offers(self, zone: str) -> bool:
        client = compute_v1.MachineTypesClient(credentials=self.credentials())
        try:
            client.get(project=self.project, zone=zone, machine_type=MACHINE_TYPE)
            return True
        except NotFound:
            return False

    def _insert_vm(self, spec, zone: str, startup: str) -> None:
        images = compute_v1.ImagesClient(credentials=self.credentials())
        image = images.get_from_family(project=IMAGE_PROJECT, family=IMAGE_FAMILY)
        addresses = compute_v1.AddressesClient(credentials=self.credentials())
        instance = compute_v1.Instance()
        instance.name = spec.name
        instance.machine_type = f"zones/{zone}/machineTypes/{MACHINE_TYPE}"
        params = compute_v1.AttachedDiskInitializeParams()
        params.source_image = image.self_link
        params.disk_size_gb = DISK_GB
        params.disk_type = f"zones/{zone}/diskTypes/{DISK_TYPE}"
        disk = compute_v1.AttachedDisk()
        disk.auto_delete = True
        disk.boot = True
        disk.initialize_params = params
        instance.disks = [disk]
        nic = compute_v1.NetworkInterface()
        nic.subnetwork = (
            f"projects/{self.project}/regions/{self.region}/subnetworks/{spec.subnet}"
        )
        nic.network_i_p = spec.address
        if spec.external_address:
            reserved = addresses.get(
                project=self.project, region=self.region, address=spec.external_address
            )
            access = compute_v1.AccessConfig()
            access.name = "external"
            access.type_ = "ONE_TO_ONE_NAT"
            access.nat_i_p = reserved.address
            nic.access_configs = [access]
        instance.network_interfaces = [nic]
        account = compute_v1.ServiceAccount()
        account.email = self.email(spec.service_account)
        account.scopes = [CLOUD_PLATFORM]
        instance.service_accounts = [account]
        metadata = compute_v1.Metadata()
        metadata.items = [
            compute_v1.Items(key=key, value=value)
            for key, value in metadata_for(startup).items()
        ]
        instance.metadata = metadata
        shield = compute_v1.ShieldedInstanceConfig()
        shield.enable_secure_boot = True
        shield.enable_vtpm = True
        shield.enable_integrity_monitoring = True
        instance.shielded_instance_config = shield
        instances = compute_v1.InstancesClient(credentials=self.credentials())
        instances.insert(
            project=self.project, zone=zone, instance_resource=instance
        ).result(timeout=600)

    def wait_ready(self, spec) -> str:
        self.step(f"Waiting for {spec.name} to report {spec.ready_attribute}")
        zone = self.zones.get(spec.name) or self.zone_of(spec.name)
        if not zone:
            raise DeployError(f"{spec.name} has no zone.")
        deadline = time.time() + 900
        while time.time() < deadline:
            value = self.guest_ready(spec.name, zone)
            if value:
                print(f"    {value}", flush=True)
                return value
            time.sleep(15)
        raise DeployError(
            f"{spec.name} did not report ready. Read the serial console: "
            f"gcloud compute instances get-serial-port-output {spec.name} --zone {zone}"
        )

    def guest_ready(self, name: str, zone: str) -> str:
        completed = subprocess.run(
            [
                "gcloud",
                "compute",
                "instances",
                "get-guest-attributes",
                name,
                "--project",
                self.project,
                "--zone",
                zone,
                "--query-path=twotier/ready",
                "--format=json",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            if is_not_ready(Exception(completed.stderr)):
                return ""
            return ""
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return ""
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            nested = payload.get("queryValue") or payload
            items = nested.get("items") if isinstance(nested, dict) else []
            if not items and payload.get("key") == "ready":
                return str(payload.get("value") or "")
        else:
            items = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            if item.get("namespace") == "twotier" and item.get("key") == "ready":
                return str(item.get("value") or "")
        return ""

    def zone_of(self, name: str) -> str:
        client = compute_v1.InstancesClient(credentials=self.credentials())
        for zone, scoped in client.aggregated_list(project=self.project):
            for instance in scoped.instances or []:
                if instance.name == name:
                    return zone.split("/")[-1]
        return ""

    def web_address(self) -> str:
        client = compute_v1.AddressesClient(credentials=self.credentials())
        reserved = client.get(project=self.project, region=self.region, address=IP_WEB)
        return reserved.address

    def http_text(self) -> tuple[int, str]:
        url = f"http://{self.web_address()}/"
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode(errors="replace")

    def port_test(self, name: str) -> str:
        command = (
            "set +e; "
            f"timeout 5 bash -c 'echo >/dev/tcp/{DB_ADDRESS}/{DB_PORT}'; "
            "code=$?; "
            'if [ "$code" -eq 0 ]; then echo RESULT=open; '
            'elif [ "$code" -eq 124 ]; then echo RESULT=timeout; '
            "else echo RESULT=refused; fi"
        )
        last = ""
        for _ in range(8):
            text, code = self.ssh(name, command)
            last = text
            for line in text.splitlines():
                if line.startswith("RESULT="):
                    return line.split("=", 1)[1].strip()
            if code == 0 and "RESULT=" in text:
                continue
            time.sleep(20)
        raise DeployError(f"The port test on {name} did not return a result. Last output was saved without secrets.")

    def ssh(self, name: str, command: str) -> tuple[str, int]:
        zone = self.zone_of(name)
        completed = subprocess.run(
            [
                "gcloud",
                "compute",
                "ssh",
                name,
                "--project",
                self.project,
                "--zone",
                zone,
                "--tunnel-through-iap",
                "--quiet",
                "--ssh-flag=-o StrictHostKeyChecking=no",
                "--command",
                command,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        return completed.stdout + completed.stderr, completed.returncode

    def project_labels(self) -> dict[str, str]:
        completed = subprocess.run(
            describe_project_command(self.project),
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            return {}
        return parse_project_json(completed.stdout).labels

    def project_state(self) -> str:
        completed = subprocess.run(
            describe_project_command(self.project),
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            return "GONE"
        return parse_project_json(completed.stdout).lifecycle_state

    def _gone(self, action) -> None:
        try:
            action()
        except NotFound:
            return
        except Exception as error:
            if "not found" in str(error).lower() or "was not found" in str(error).lower():
                return
            raise

    def delete_vms(self) -> None:
        self.step("Deleting virtual machines")
        client = compute_v1.InstancesClient(credentials=self.credentials())
        for spec in VMS:
            zone = self.zone_of(spec.name)
            if not zone:
                continue
            self._gone(
                lambda spec=spec, zone=zone: client.delete(
                    project=self.project, zone=zone, instance=spec.name
                ).result(timeout=600)
            )

    def delete_router(self) -> None:
        self.step("Deleting the router")
        client = compute_v1.RoutersClient(credentials=self.credentials())
        self._gone(
            lambda: client.delete(
                project=self.project, region=self.region, router=ROUTER_NAME
            ).result(timeout=300)
        )

    def delete_firewall(self) -> None:
        self.step("Deleting firewall rules")
        client = compute_v1.FirewallsClient(credentials=self.credentials())
        for rule in build_rules():
            self._gone(
                lambda rule=rule: client.delete(
                    project=self.project, firewall=rule.name
                ).result(timeout=300)
            )

    def delete_subnets(self) -> None:
        self.step("Deleting subnets")
        client = compute_v1.SubnetworksClient(credentials=self.credentials())
        from src.lib.config import SUBNETS

        for spec in SUBNETS:
            self._gone(
                lambda spec=spec: client.delete(
                    project=self.project, region=self.region, subnetwork=spec.name
                ).result(timeout=300)
            )

    def delete_network(self) -> None:
        self.step("Deleting the network")
        client = compute_v1.NetworksClient(credentials=self.credentials())
        self._gone(lambda: client.delete(project=self.project, network=VPC_NAME).result(timeout=300))

    def delete_addresses(self) -> None:
        self.step("Releasing addresses")
        client = compute_v1.AddressesClient(credentials=self.credentials())
        for name in (IP_WEB, IP_NAT):
            self._gone(
                lambda name=name: client.delete(
                    project=self.project, region=self.region, address=name
                ).result(timeout=300)
            )

    def delete_secret(self) -> None:
        self.step("Deleting the secret")
        client = secretmanager.SecretManagerServiceClient(credentials=self.credentials())
        resource = f"projects/{self.project}/secrets/{SECRET_NAME}"
        self._gone(lambda: client.delete_secret(request={"name": resource}))

    def delete_service_accounts(self) -> None:
        self.step("Deleting service accounts")
        client = iam_admin_v1.IAMClient(credentials=self.credentials())
        for spec in VMS:
            name = f"projects/{self.project}/serviceAccounts/{self.email(spec.service_account)}"
            self._gone(lambda name=name: client.delete_service_account(request={"name": name}))

    def delete_project(self) -> None:
        self.step(f"Deleting project {self.project}")
        run(delete_project_command(self.project))

    def expected_labels(self) -> dict[str, str]:
        return {
            "lifetime": LIFETIME_LABEL,
            "owner": OWNER_LABEL,
            "project": PROJECT_LABEL,
        }
