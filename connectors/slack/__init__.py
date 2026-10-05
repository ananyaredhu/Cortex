"""Slack connector: real workspace, read-only bot token. See connector.py."""
from connectors.slack.client import SlackClient, SlackError, SlackRateLimited
from connectors.slack.connector import SlackConnector
from connectors.slack.manifest import SeedManifest

__all__ = ["SeedManifest", "SlackClient", "SlackConnector", "SlackError", "SlackRateLimited"]
