"""Offline checks for rules, guests, retries, and deploy order."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from src.lib import config, identity, retry, rules, vms
from src.lib.guest import GUEST_DIR, load_script, startup_replacements
from src.lib.orchestrate import DeployError, confirm_teardown, run_deploy, run_teardown_resources


class FirewallTests(unittest.TestCase):
    def test_builder_targets_identities_and_logs_the_deny(self):
        built = rules.build_rules()
        self.assertEqual(built[-1].name, config.RULE_DENY)
        self.assertTrue(built[-1].log_enabled)
        postgres = next(rule for rule in built if rule.name == config.RULE_POSTGRES)
        self.assertEqual(postgres.source_service_accounts, (config.SA_WEB,))
        self.assertEqual(postgres.target_service_accounts, (config.SA_DB,))
        self.assertEqual(postgres.ports, ("5432",))
        ssh = next(rule for rule in built if rule.name == config.RULE_SSH)
        self.assertEqual(ssh.source_ranges, (config.IAP_TCP_RANGE,))
        http = next(rule for rule in built if rule.name == config.RULE_HTTP)
        self.assertEqual(http.target_service_accounts, (config.SA_WEB,))
        for rule in built:
            self.assertEqual(rule.target_tags, ())
            self.assertEqual(rule.source_tags, ())
        self.assertEqual(audit_clean(built), [])

    def test_audit_fails_on_each_mutation(self):
        base = list(rules.build_rules())
        cases = {
            "open database": replace(base, config.RULE_POSTGRES, source_ranges=("0.0.0.0/0",), source_service_accounts=()),
            "ssh from anywhere": replace(base, config.RULE_SSH, source_ranges=("0.0.0.0/0",)),
            "wrong source": replace(base, config.RULE_POSTGRES, source_service_accounts=(config.SA_TEST,)),
            "second database rule": base + [base[2]],
            "tag": replace(base, config.RULE_HTTP, target_tags=("web",)),
            "silent deny": replace(base, config.RULE_DENY, log_enabled=False),
            "missing deny": [rule for rule in base if rule.name != config.RULE_DENY],
        }
        for name, mutated in cases.items():
            with self.subTest(name=name):
                self.assertTrue(rules.audit_rules(tuple(mutated)))


class PasswordTests(unittest.TestCase):
    def test_absent_password_passes_and_a_hit_fails(self):
        identity.assert_password_absent("secret-value", ["startup without it"])
        with self.assertRaises(config.ConfigError):
            identity.assert_password_absent("secret-value", ["secret-value"])
        with self.assertRaises(config.ConfigError):
            identity.assert_password_absent("", ["anything"])


class RetryTests(unittest.TestCase):
    def test_retries_not_ready_then_succeeds(self):
        calls = {"n": 0}

        def action():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("The resource is not ready")
            return "ok"

        self.assertEqual(retry.call_until_ready(action, attempts=5), "ok")
        self.assertEqual(calls["n"], 3)

    def test_other_errors_stop_at_once(self):
        calls = {"n": 0}

        def action():
            calls["n"] += 1
            raise RuntimeError("permission denied")

        with self.assertRaises(RuntimeError):
            retry.call_until_ready(action, attempts=5)
        self.assertEqual(calls["n"], 1)

    def test_gives_up_after_the_cap(self):
        def action():
            raise RuntimeError("try again")

        with self.assertRaises(RuntimeError):
            retry.call_until_ready(action, attempts=3)


class VmTests(unittest.TestCase):
    def test_shapes_and_metadata(self):
        for spec in config.VMS:
            metadata = vms.metadata_for("echo hello")
            self.assertNotIn("password=", metadata["startup-script"])
            self.assertEqual(metadata["enable-oslogin"], "TRUE")
            self.assertEqual(metadata["enable-guest-attributes"], "TRUE")
            if spec.external_address is None:
                self.assertIsNone(spec.external_address)
            else:
                self.assertEqual(spec.external_address, config.IP_WEB)
            self.assertEqual(spec.service_account.startswith("sa-"), True)

    def test_zone_fallback(self):
        zones = config.ZONES
        chosen = vms.choose_zone(
            zones,
            offered=lambda zone: zone != "us-east1-b",
            exhausted=lambda zone: zone == "us-east1-c",
        )
        self.assertEqual(chosen, "us-east1-d")
        self.assertEqual(
            vms.choose_zone(zones, offered=lambda zone: True, exhausted=lambda zone: False),
            "us-east1-b",
        )


class GuestTests(unittest.TestCase):
    def test_scripts_have_no_password_and_pass_bash_n(self):
        for name in ("common.sh", "db-setup.sh", "web-setup.sh"):
            text = (GUEST_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("__DB_PASSWORD__", text)
            completed = subprocess.run(["bash", "-n", str(GUEST_DIR / name)], capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
        database_script = (GUEST_DIR / "db-setup.sh").read_text()
        self.assertIn("PASSWORD :'pw'", database_script)
        self.assertIn("DB_READY", database_script)
        self.assertIn("WEB_READY", (GUEST_DIR / "web-setup.sh").read_text())
        self.assertIn("FAILED at line", (GUEST_DIR / "common.sh").read_text())
        script = load_script("web-setup.sh", startup_replacements("twotier-poc-zeon01"))
        self.assertIn("Environment=PROJECT_ID=twotier-poc-zeon01", script)
        self.assertNotIn("__PROJECT_ID__", script)
        self.assertNotIn("token_urlsafe", script)


class FakeCloud:
    def __init__(self, ready="DB_READY", web_ready="WEB_READY"):
        self.events = []
        self.secret_versions = 0
        self.password = "generated-password-value"
        self.ready = ready
        self.web_ready = web_ready
        self.deleted = False

    def _mark(self, name):
        self.events.append(name)

    def ensure_project(self):
        self._mark("project")

    def link_billing(self):
        self._mark("billing")
        self.events.append("billing-id-hidden")

    def enable_apis(self):
        self._mark("apis")

    def wait_for_compute(self):
        self._mark("wait-compute")

    def delete_default_network(self):
        self._mark("default-network")

    def ensure_service_accounts(self):
        self._mark("service-accounts")

    def ensure_secret(self):
        self._mark("secret")
        if self.secret_versions == 0:
            self.secret_versions = 1

    def grant_operator(self):
        self._mark("operator")

    def ensure_network(self):
        self._mark("network")

    def ensure_subnets(self):
        self._mark("subnets")

    def ensure_firewall(self):
        self._mark("firewall")

    def ensure_addresses(self):
        self._mark("addresses")

    def ensure_nat(self):
        self._mark("nat")

    def ensure_vm(self, spec):
        self._mark(f"vm:{spec.name}")

    def wait_ready(self, spec):
        self._mark(f"ready:{spec.name}")
        if spec.name == config.VM_DB:
            return self.ready
        return self.web_ready

    def delete_vms(self):
        self._mark("delete-vms")

    def delete_router(self):
        self._mark("delete-router")

    def delete_firewall(self):
        self._mark("delete-firewall")

    def delete_subnets(self):
        self._mark("delete-subnets")

    def delete_network(self):
        self._mark("delete-network")

    def delete_addresses(self):
        self._mark("delete-addresses")

    def delete_secret(self):
        self._mark("delete-secret")

    def delete_service_accounts(self):
        self._mark("delete-service-accounts")

    def delete_project(self):
        self.deleted = True
        self._mark("delete-project")


class OrchestrateTests(unittest.TestCase):
    def test_deploy_order_and_a_rerun_does_not_add_a_secret_version(self):
        cloud = FakeCloud()
        run_deploy(cloud)
        self.assertEqual(
            cloud.events[:8],
            [
                "project",
                "billing",
                "billing-id-hidden",
                "apis",
                "wait-compute",
                "default-network",
                "service-accounts",
                "secret",
            ],
        )
        self.assertEqual(
            [event for event in cloud.events if event.startswith("vm:")],
            ["vm:vm-db-01", "vm:vm-web-01", "vm:vm-test-01"],
        )
        self.assertEqual(cloud.secret_versions, 1)
        run_deploy(cloud)
        self.assertEqual(cloud.secret_versions, 1)
        self.assertNotIn(cloud.password, "\n".join(cloud.events))
        self.assertNotIn("aaaaaa-bbbbbb-cccccc", "\n".join(cloud.events))

    def test_failed_guest_stops_before_the_web_machine(self):
        cloud = FakeCloud(ready="FAILED at line 12")
        with self.assertRaises(DeployError):
            run_deploy(cloud)
        self.assertIn("vm:vm-db-01", cloud.events)
        self.assertNotIn("vm:vm-web-01", cloud.events)

    def test_teardown_order_and_a_wrong_name(self):
        cloud = FakeCloud()
        self.assertEqual(confirm_teardown(cloud, "wrong", "twotier-poc-zeon01", resources_only=False), "refused")
        self.assertFalse(cloud.deleted)
        self.assertEqual(cloud.events, [])
        run_teardown_resources(cloud)
        self.assertEqual(
            cloud.events,
            [
                "delete-vms",
                "delete-router",
                "delete-firewall",
                "delete-subnets",
                "delete-network",
                "delete-addresses",
                "delete-secret",
                "delete-service-accounts",
            ],
        )


def replace(rules_list, name, **changes):
    updated = []
    for rule in rules_list:
        if rule.name == name:
            data = rule.__dict__.copy()
            data.update(changes)
            updated.append(rules.FirewallRule(**data))
        else:
            updated.append(rule)
    return updated


def audit_clean(built):
    return rules.audit_rules(built)


if __name__ == "__main__":
    unittest.main()
