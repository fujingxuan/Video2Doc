from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import ModelConfig, ProcessingProfile


class CatalogRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_models(self) -> list[ModelConfig]:
        return list(
            self.session.scalars(
                select(ModelConfig).options(selectinload(ModelConfig.capabilities)).order_by(ModelConfig.name)
            )
        )

    def get_model(self, model_id: int) -> ModelConfig | None:
        return self.session.scalar(
            select(ModelConfig)
            .where(ModelConfig.id == model_id)
            .options(selectinload(ModelConfig.capabilities))
        )

    def list_profiles(self) -> list[ProcessingProfile]:
        return list(self.session.scalars(select(ProcessingProfile).order_by(ProcessingProfile.name)))

    def get_profile(self, profile_id: int) -> ProcessingProfile | None:
        return self.session.get(ProcessingProfile, profile_id)
