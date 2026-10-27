"""Offline checks for names, labels, and the plan.

These tests do not call Google Cloud. They prove the validators and the
plan text. They do not prove that a request would be accepted.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from src.deploy import main, plan_lines
from src.lib import config, gcloud
from src.lib.config import ConfigError


def fake_billing_account() -> str:
    """A shape fixture. Not a real billing account."""
    return "a" * 6 + "-" + "b" * 6 + "-" + "c" * 6


class ProjectIdTests(unittest.TestCase):
    def test_accepts_a_normal_id(self):
        self.assertEqual(
            config.validate_project_id("twotier-poc-zeon01"),
            "twotier-poc-zeon01",
        )

    def test_accepts_six_characters(self):
        config.validate_project_id("abcdef")

    def test_rejects_short_uppercase_digit_hyphen_and_restricted_words(self):
        for project_id in (
            "abcd",
            "1twotier",
            "TwoTier",
            "twotier-",
            "twotier_poc",
            "my-google-project",
            "my-ssl-project",
            "null-project",
            "undefined-project",
            "a" * 31,
        ):
            with self.subTest(project_id=project_id):
                with self.assertRaises(ConfigError):
                    config.validate_project_id(project_id)

    def test_rejects_thirty_one_characters_and_accepts_thirty(self):
        config.validate_project_id("a" + "b" * 28 + "c")
        with self.assertRaises(ConfigError):
            config.validate_project_id("a" + "b" * 29 + "c")


class BillingAccountTests(unittest.TestCase):
    def test_accepts_the_documented_shape(self):
        account = fake_billing_account()
        self.assertEqual(config.validate_billing_account(account), account)
        config.validate_billing_account("012345-abcdef-ABCDEF")

    def test_rejects_a_bad_shape_without_echoing_it(self):
        bad = "not-an-account"
        with self.assertRaises(ConfigError) as caught:
            config.validate_billing_account(bad)
        self.assertNotIn(bad, str(caught.exception))

    def test_rejects_empty(self):
        with self.assertRaises(ConfigError):
            config.validate_billing_account("")


class LabelTests(unittest.TestCase):
    def test_created_label_has_no_colon(self):
        moment = datetime(2026, 10, 26, 23, 45, 0, tzinfo=timezone.utc)
        text = config.format_created_label(moment)
        self.assertEqual(text, "2026-10-26t23-45-00z")
        self.assertNotIn(":", text)
        config.validate_created_label(text)

    def test_colon_uppercase_and_empty_are_rejected(self):
        for value in ("2026-10-26T23:45:00Z", "Hello", "", "a" * 64):
            with self.subTest(value=value):
                with self.assertRaises(ConfigError):
                    config.validate_label_value(value)

    def test_key_must_start_with_a_letter(self):
        with self.assertRaises(ConfigError):
            config.validate_label_key("1created")
        config.validate_label_key("created")

    def test_our_labels_require_the_hyphen_form(self):
        labels = config.our_labels("2026-10-26t23-45-00z")
        self.assertEqual(
            labels,
            {
                "lifetime": "1d",
                "owner": "zeon",
                "project": "02-gcp-two-tier-network-poc",
                "created": "2026-10-26t23-45-00z",
            },
        )
        config.assert_our_labels(labels)

    def test_foreign_project_is_refused_without_echoing_its_values(self):
        foreign = {
            "lifetime": "keep",
            "owner": "someone-else",
            "project": "other-project",
            "created": "2026-10-26t23-45-00z",
        }
        with self.assertRaises(ConfigError) as caught:
            config.assert_our_labels(foreign)
        self.assertNotIn("someone-else", str(caught.exception))
        self.assertNotIn("other-project", str(caught.exception))

    def test_missing_created_is_refused(self):
        with self.assertRaises(ConfigError):
            config.assert_our_labels(
                {"lifetime": "1d", "owner": "zeon", "project": config.PROJECT_LABEL}
            )

    def test_created_with_a_colon_is_refused(self):
        with self.assertRaises(ConfigError):
            config.our_labels("2026-10-26T23:45:00Z")


class ServiceAccountAndAddressTests(unittest.TestCase):
    def test_database_account_id_meets_the_six_character_rule(self):
        self.assertEqual(config.SA_DB, "sa-db-01")
        config.validate_service_account_id(config.SA_DB)
        config.validate_service_account_id(config.SA_WEB)
        config.validate_service_account_id(config.SA_TEST)
        with self.assertRaises(ConfigError):
            config.validate_service_account_id("sa-db")

    def test_fixed_addresses_avoid_the_reserved_ends(self):
        for subnet in config.SUBNETS:
            config.validate_fixed_address(subnet.prefix, subnet.address)
        with self.assertRaises(ConfigError):
            config.validate_fixed_address("10.20.1.0/24", "10.20.1.1")
        with self.assertRaises(ConfigError):
            config.validate_fixed_address("10.20.1.0/24", "10.20.1.254")

    def test_display_name_fits_the_create_page_rule(self):
        self.assertEqual(config.validate_display_name(config.DISPLAY_NAME), config.DISPLAY_NAME)
        self.assertLessEqual(len(config.DISPLAY_NAME), 30)


class SettingsTests(unittest.TestCase):
    def test_missing_file_is_an_empty_plan(self):
        settings = config.load_settings(Path("/nonexistent/.env-twotier"))
        self.assertIsNone(settings.project_id)
        self.assertIsNone(settings.billing_account)
        self.assertEqual(settings.region, "us-east1")

    def test_present_invalid_project_id_fails(self):
        with self.assertRaises(ConfigError):
            config.settings_from_env({"GCP_PROJECT_ID": "NOPE"})

    def test_present_invalid_billing_account_does_not_echo(self):
        bad = "secret-billing-value"
        with self.assertRaises(ConfigError) as caught:
            config.settings_from_env({"GCP_BILLING_ACCOUNT": bad})
        self.assertNotIn(bad, str(caught.exception))


class PlanTests(unittest.TestCase):
    def test_plan_without_env_names_the_network_and_no_secret(self):
        settings = config.Settings(project_id=None, billing_account=None, region="us-east1")
        text = "\n".join(plan_lines(settings))
        self.assertIn("Nothing is created", text)
        self.assertIn("not set", text)
        self.assertIn(config.VPC_NAME, text)
        self.assertIn("snet-db", text)
        self.assertIn("no external address", text)
        self.assertIn(config.IP_WEB, text)
        self.assertIn("2026-10-26t23-45-00z", text)
        self.assertNotIn("23:45", text)

    def test_plan_never_prints_the_billing_account(self):
        account = fake_billing_account()
        settings = config.Settings(
            project_id="twotier-poc-zeon01",
            billing_account=account,
            region="us-east1",
        )
        text = "\n".join(plan_lines(settings))
        self.assertIn("twotier-poc-zeon01", text)
        self.assertIn("The id is not printed", text)
        self.assertNotIn(account, text)
        self.assertNotIn("bbbbbb", text)

    def test_main_plan_with_no_file_exits_zero(self):
        with redirect_stdout(io.StringIO()):
            code = main(["--plan"], env_path=Path("/nonexistent/.env-twotier"))
        self.assertEqual(code, 0)

    def test_live_deploy_without_env_is_refused(self):
        error = io.StringIO()
        with redirect_stderr(error):
            code = main([], env_path=Path("/nonexistent/.env-twotier"))
        self.assertEqual(code, 1)
        self.assertIn("GCP_BILLING_ACCOUNT", error.getvalue())


class GcloudCommandTests(unittest.TestCase):
    def test_create_command_uses_labels_and_not_the_default_project_flag(self):
        labels = config.our_labels("2026-10-26t23-45-00z")
        command = gcloud.create_project_command(
            "twotier-poc-zeon01", config.DISPLAY_NAME, labels
        )
        self.assertEqual(command[0:3], ["gcloud", "projects", "create"])
        self.assertIn("--name=02 two tier network poc", command)
        self.assertIn(
            "--labels=lifetime=1d,owner=zeon,project=02-gcp-two-tier-network-poc,"
            "created=2026-10-26t23-45-00z",
            command,
        )
        self.assertNotIn("--set-as-default", command)
        self.assertNotIn(":", " ".join(command))

    def test_billing_command_is_redacted_in_text(self):
        account = fake_billing_account()
        command = gcloud.link_billing_command("twotier-poc-zeon01", account)
        self.assertEqual(
            command,
            [
                "gcloud",
                "billing",
                "projects",
                "link",
                "twotier-poc-zeon01",
                f"--billing-account={account}",
            ],
        )
        shown = gcloud.redact("failed for " + account, (account,))
        self.assertNotIn(account, shown)
        self.assertIn("[redacted]", shown)

    def test_describe_parser_drops_the_project_number(self):
        view = gcloud.parse_project(
            {
                "projectId": "twotier-poc-zeon01",
                "projectNumber": "111111111111",
                "lifecycleState": "ACTIVE",
                "labels": {"lifetime": "1d"},
            }
        )
        self.assertEqual(view.project_id, "twotier-poc-zeon01")
        self.assertEqual(view.lifecycle_state, "ACTIVE")
        rendered = f"{view.project_id} {view.lifecycle_state} {view.labels}"
        self.assertNotIn("111111111111", rendered)

    def test_delete_is_quiet_and_enable_names_the_project(self):
        delete = gcloud.delete_project_command("twotier-poc-zeon01")
        self.assertEqual(delete[-1], "--quiet")
        enable = gcloud.enable_api_command("twotier-poc-zeon01", "compute.googleapis.com")
        self.assertIn("--project=twotier-poc-zeon01", enable)

    def test_create_rejects_a_colon_in_created(self):
        with self.assertRaises(ConfigError):
            gcloud.create_project_command(
                "twotier-poc-zeon01",
                config.DISPLAY_NAME,
                {
                    "lifetime": "1d",
                    "owner": "zeon",
                    "project": config.PROJECT_LABEL,
                    "created": "2026-10-26T23:45:00Z",
                },
            )


if __name__ == "__main__":
    unittest.main()
