"""A source that is not set up is reported and left out; a failing Drive pass says why in the log, and nothing else does."""
import logging

from connectors.gdrive.client import DriveError
from connectors.ingestion import FakeEmbedder, Ingestor
from connectors.ingestion.__main__ import build_connectors
from simulators.jira.testing import SeededJira


def test_a_source_that_cannot_be_built_does_not_stop_the_others(caplog):
    def broken():
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_ID is not set: /run/brain-secrets/x.json")

    with caplog.at_level(logging.WARNING):
        built, unavailable = build_connectors(["jira", "gdrive"], {"jira": SeededJira, "gdrive": broken})
    assert list(built) == ["jira"] and unavailable == {"gdrive": "RuntimeError"}
    assert "gdrive is not set up (RuntimeError)" in caplog.text
    assert "/run/brain-secrets" not in caplog.text and "GOOGLE_OAUTH" not in caplog.text      # class only: messages can hold paths


def test_everything_builds_when_all_sources_are_set_up():
    built, unavailable = build_connectors(["jira"], {"jira": SeededJira})
    assert list(built) == ["jira"] and unavailable == {}


def test_drive_error_names_its_status_and_reason_for_the_log():
    assert DriveError(400, "token_refresh_failed").log_detail == "400 token_refresh_failed"


class Failing:
    source = "gdrive"

    def __init__(self, error: Exception):
        self.error = error

    def list_changes(self, cursor):
        raise self.error


def test_the_log_names_a_drive_failure_but_not_the_message_of_any_other(store, caplog):
    with caplog.at_level(logging.WARNING, logger="connectors.ingestion.pipeline"):
        Ingestor([Failing(DriveError(400, "token_refresh_failed"))], store, FakeEmbedder()).run_once()
        assert "gdrive: pass failed (DriveError: 400 token_refresh_failed)" in caplog.text
        caplog.clear()
        Ingestor([Failing(ValueError("file 1AbC secret-id at https://drive/x"))], store, FakeEmbedder()).run_once()
        assert "gdrive: pass failed (ValueError)" in caplog.text
        assert "secret-id" not in caplog.text and "https://drive" not in caplog.text
