from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.secrets import SecretStorageError, protect_secret
from app.models import GatewayModel, ModelCapability, ModelConfig, ProcessingProfile
from app.repositories.catalog import CatalogRepository
from app.schemas.api import ModelCreate, ModelRead, ModelUpdate, ProfileCreate, ProfileRead, ProfileUpdate
from app.services.serializers import model_read, profile_read


CAPABILITY_FOR_FIELD = {
    "asr_model_id": "ASR",
    "ocr_model_id": "OCR",
    "text_model_id": "TEXT_PROCESSING",
}


class CatalogService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = CatalogRepository(session)

    def list_models(self, capability: str | None = None) -> list[ModelRead]:
        items = self.repo.list_models()
        if capability:
            capability = capability.upper()
            items = [item for item in items if capability in {cap.capability for cap in item.capabilities}]
        return [model_read(item) for item in items]

    def get_model(self, model_id: int) -> ModelRead:
        item = self.repo.get_model(model_id)
        if not item:
            raise NotFoundError("Model", model_id)
        return model_read(item)

    def create_model(self, payload: ModelCreate) -> ModelRead:
        values = payload.model_dump(exclude={"capabilities", "secret_value"})
        secret_value = payload.secret_value
        item = ModelConfig(**values)
        item.name = item.name.strip()
        item.provider = item.provider.strip() if item.provider else None
        item.base_url = item.base_url.strip().rstrip("/") if item.base_url else None
        if secret_value:
            try:
                item.secret_ciphertext = protect_secret(secret_value)
            except SecretStorageError as exc:
                raise ConflictError(str(exc)) from exc
            item.secret_env_name = None
        item.capabilities = [ModelCapability(capability=value.value) for value in payload.capabilities]
        self.session.add(item)
        self._commit_unique("A model with that name already exists")
        return model_read(self.repo.get_model(item.id) or item)

    def update_model(self, model_id: int, payload: ModelUpdate) -> ModelRead:
        item = self.repo.get_model(model_id)
        if not item:
            raise NotFoundError("Model", model_id)
        values = payload.model_dump(exclude_unset=True)
        capabilities = values.pop("capabilities", None)
        secret_value = values.pop("secret_value", None)
        clear_secret = bool(values.pop("clear_secret", False))
        if clear_secret:
            item.secret_ciphertext = None
            item.secret_env_name = None
        if secret_value:
            try:
                item.secret_ciphertext = protect_secret(secret_value)
            except SecretStorageError as exc:
                raise ConflictError(str(exc)) from exc
            item.secret_env_name = None
        elif "secret_env_name" in values and values["secret_env_name"]:
            item.secret_ciphertext = None
        for key, value in values.items():
            if key in {"name", "provider"} and value:
                value = value.strip()
            elif key == "base_url" and value:
                value = value.strip().rstrip("/")
            setattr(item, key, value)
        if capabilities is not None:
            if not capabilities:
                raise ConflictError("A model must have at least one capability")
            item.capabilities = [ModelCapability(capability=value.value) for value in capabilities]
        self._commit_unique("A model with that name already exists")
        return model_read(self.repo.get_model(item.id) or item)

    def delete_model(self, model_id: int) -> None:
        item = self.repo.get_model(model_id)
        if not item:
            raise NotFoundError("Model", model_id)
        self.session.delete(item)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError("Model is referenced by a processing profile") from exc

    def list_profiles(self) -> list[ProfileRead]:
        return [profile_read(item) for item in self.repo.list_profiles()]

    def get_profile(self, profile_id: int) -> ProfileRead:
        item = self.repo.get_profile(profile_id)
        if not item:
            raise NotFoundError("Processing profile", profile_id)
        return profile_read(item)

    def create_profile(self, payload: ProfileCreate) -> ProfileRead:
        values = payload.model_dump()
        self._validate_profile_models(values)
        if values.get("is_default"):
            self._clear_default()
        item = ProcessingProfile(**values)
        self.session.add(item)
        self._commit_unique("A processing profile with that name already exists")
        return profile_read(item)

    def update_profile(self, profile_id: int, payload: ProfileUpdate) -> ProfileRead:
        item = self.repo.get_profile(profile_id)
        if not item:
            raise NotFoundError("Processing profile", profile_id)
        values = payload.model_dump(exclude_unset=True)
        candidate = {column.name: getattr(item, column.name) for column in item.__table__.columns}
        candidate.update(values)
        self._validate_profile_models(candidate)
        if values.get("is_default"):
            self._clear_default()
        for key, value in values.items():
            setattr(item, key, value.strip() if key == "name" and value else value)
        self._commit_unique("A processing profile with that name already exists")
        return profile_read(item)

    def delete_profile(self, profile_id: int) -> None:
        item = self.repo.get_profile(profile_id)
        if not item:
            raise NotFoundError("Processing profile", profile_id)
        if item.is_builtin:
            raise ConflictError("Built-in processing profiles cannot be deleted")
        self.session.delete(item)
        self.session.commit()

    def _validate_profile_models(self, values: dict) -> None:
        for field, capability in CAPABILITY_FOR_FIELD.items():
            model_id = values.get(field)
            if model_id is None:
                continue
            model = self.repo.get_model(model_id)
            if not model:
                raise NotFoundError("Model", model_id)
            tags = {tag.capability for tag in model.capabilities}
            if capability not in tags:
                raise ConflictError(f"Model {model.name!r} does not have capability {capability}")
            if not model.enabled:
                raise ConflictError(f"Model {model.name!r} is disabled")
        for field, gateway_type in (
            ("asr_gateway_model_id", "ASR"),
            ("ocr_gateway_model_id", "OCR"),
        ):
            gateway_model_id = values.get(field)
            if gateway_model_id is None:
                continue
            gateway_model = self.session.get(GatewayModel, gateway_model_id)
            if gateway_model is None:
                raise NotFoundError("Gateway model", gateway_model_id)
            if gateway_model.gateway.gateway_type != gateway_type:
                raise ConflictError(
                    f"Gateway model {gateway_model.display_name!r} is not a {gateway_type} model"
                )
            if not gateway_model.enabled or not gateway_model.gateway.enabled:
                raise ConflictError(f"Gateway model {gateway_model.display_name!r} is disabled")
        if (
            values.get("ocr_enabled")
            and values.get("ocr_model_id") is None
            and values.get("ocr_gateway_model_id") is None
        ):
            raise ConflictError("An OCR-enabled profile must select an OCR gateway model")

    def _clear_default(self) -> None:
        for item in self.session.scalars(select(ProcessingProfile).where(ProcessingProfile.is_default.is_(True))):
            item.is_default = False

    def _commit_unique(self, message: str) -> None:
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError(message) from exc
