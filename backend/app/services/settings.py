from __future__ import annotations

import os

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
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
