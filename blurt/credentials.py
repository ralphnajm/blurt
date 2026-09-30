import os

from gi.repository import Secret

SCHEMA = Secret.Schema.new(
    "dev.blurt.Blurt",
    Secret.SchemaFlags.NONE,
    {"service": Secret.SchemaAttributeType.STRING},
)
ATTRIBUTES = {"service": "openai"}


def get_api_key() -> str | None:
    stored = Secret.password_lookup_sync(SCHEMA, ATTRIBUTES, None)
    return stored or os.environ.get("OPENAI_API_KEY") or None


def set_api_key(key: str) -> None:
    Secret.password_store_sync(
        SCHEMA, ATTRIBUTES, Secret.COLLECTION_DEFAULT, "Blurt · OpenAI API key", key, None
    )


def clear_api_key() -> None:
    Secret.password_clear_sync(SCHEMA, ATTRIBUTES, None)
