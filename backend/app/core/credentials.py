from __future__ import annotations

import os
from typing import Protocol

from app.core.secrets import SecretStorageError, reveal_secret


class CredentialRecord(Protocol):
    secret_env_name: str | None
    secret_ciphertext: str | None


def credential_source(item: CredentialRecord) -> str:
    if item.secret_ciphertext:
        return "DIRECT"
    if item.secret_env_name:
        return "ENVIRONMENT"
    return "NONE"


def credential_is_configured(item: CredentialRecord) -> bool:
    if item.secret_ciphertext:
        try:
            return bool(reveal_secret(item.secret_ciphertext))
        except SecretStorageError:
            return False
    if item.secret_env_name:
        return bool(os.getenv(item.secret_env_name, "").strip())
    return True


def resolve_credential(item: CredentialRecord) -> str:
    if item.secret_ciphertext:
        return reveal_secret(item.secret_ciphertext).strip()
    if item.secret_env_name:
        return os.getenv(item.secret_env_name, "").strip()
    return ""


def resolve_model_credential(model_id: int) -> str:
    from app.db.session import SessionLocal
    from app.models import ModelConfig

    with SessionLocal() as session:
        item = session.get(ModelConfig, model_id)
        if item is None:
            raise SecretStorageError("Configured LLM model no longer exists")
        return resolve_credential(item)


def resolve_gateway_credential(gateway_id: int) -> str:
    from app.db.session import SessionLocal
    from app.models import GatewayConfig

    with SessionLocal() as session:
        item = session.get(GatewayConfig, gateway_id)
        if item is None:
            raise SecretStorageError("Configured Gateway no longer exists")
        return resolve_credential(item)
