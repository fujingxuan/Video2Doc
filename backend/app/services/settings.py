from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.models import AppSetting, ProcessingProfile
from app.schemas.api import SettingsRead, SettingsUpdate
from app.services.serializers import settings_read


class SettingsService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self) -> SettingsRead:
        item = self._get_or_create()
        result = settings_read(item)
        result.api_key_configured = bool(os.getenv("ONEAPI_API_KEY"))
        return result

    def update(self, payload: SettingsUpdate) -> SettingsRead:
        item = self._get_or_create()
        values = payload.model_dump(exclude_unset=True)
        profile_id = values.get("last_profile_id")
        if profile_id is not None and self.session.get(ProcessingProfile, profile_id) is None:
            raise NotFoundError("Processing profile", profile_id)
        vault_value = values.get("obsidian_vault_path", item.obsidian_vault_path)
        inbox_value = values.get("inbox_path", item.inbox_path)
        if vault_value:
            vault = Path(vault_value)
            if not vault.is_absolute() or "\x00" in str(vault):
                raise ConflictError("Obsidian Vault path must be an absolute safe path")
            if inbox_value:
                inbox = Path(inbox_value)
                if inbox.is_absolute():
                    try:
                        inbox.relative_to(vault)
                    except ValueError as exc:
                        raise ConflictError(
                            "Absolute Inbox path must be inside the Obsidian Vault"
                        ) from exc
                elif ".." in inbox.parts:
                    raise ConflictError("Inbox path cannot traverse outside the Vault")
        elif inbox_value and not Path(inbox_value).is_absolute():
            raise ConflictError(
                "A relative Inbox path requires an Obsidian Vault path"
            )
        for key, value in values.items():
            setattr(item, key, value)
        self.session.commit()
        return self.get()

    def _get_or_create(self) -> AppSetting:
        item = self.session.get(AppSetting, 1)
        if item is None:
            item = AppSetting(id=1)
            self.session.add(item)
            self.session.commit()
        return item
