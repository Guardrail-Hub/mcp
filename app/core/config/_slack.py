from pydantic import field_validator, model_validator


class SlackMixin:
    # =====================================================
    # Notifications — Slack (outbound)
    # =====================================================
    # Consumed by app.bootstrap.build_notification_flow_from_settings to register
    # the SlackNotificationChannel. Inbound (slash command) settings such as a
    # signing secret are intentionally NOT here — they belong with the Slash
    # Commands feature that will actually read them.
    slack_enabled: bool = False
    slack_bot_token: str | None = None
    slack_default_channel: str | None = None

    @field_validator("slack_bot_token", "slack_default_channel", mode="before")
    @classmethod
    def _slack_empty_string_to_none(cls, value):
        if value == "":
            return None
        return value

    @model_validator(mode="after")
    def _validate_slack_enabled(self):
        """Fail fast: enabling Slack requires a bot token and a default channel.

        Enforced in every environment (not just production), because enabling the
        channel without credentials is a misconfiguration regardless of env.
        """
        if self.slack_enabled:
            missing = []
            if not self.slack_bot_token:
                missing.append("SLACK_BOT_TOKEN")
            if not self.slack_default_channel:
                missing.append("SLACK_DEFAULT_CHANNEL")
            if missing:
                raise ValueError(
                    "SLACK_ENABLED=true requires: " + ", ".join(missing)
                )
        return self
