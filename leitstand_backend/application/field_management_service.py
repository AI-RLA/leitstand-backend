"""FieldManagementService — audit-aware CRUD for fields."""

from uuid import UUID

from leitstand_backend.domain.errors import FieldNotFoundError
from leitstand_backend.domain.model.field import Field
from leitstand_backend.ports.inbound.field_management import (
    CreateFieldCommand,
    DeleteFieldCommand,
    FieldManagementUseCase,
    UpdateFieldCommand,
)
from leitstand_backend.ports.outbound.audit_log import AuditWriter
from leitstand_backend.ports.outbound.field_repository import FieldRepository


class FieldManagementService(FieldManagementUseCase):
    def __init__(self, repo: FieldRepository, audit: AuditWriter):
        self._repo = repo
        self._audit = audit

    async def create(self, command: CreateFieldCommand) -> Field:
        field = await self._repo.create(
            name=command.name,
            geometry=command.geometry,
            notes=command.notes,
        )
        await self._audit(
            "field.create",
            "field",
            str(field.id),
            command.model_dump(mode="json"),
        )
        return field

    async def update(self, command: UpdateFieldCommand) -> Field:
        before = await self._repo.get(command.field_id)
        if before is None:
            raise FieldNotFoundError(command.field_id)

        updated = await self._repo.update(
            field_id=command.field_id,
            name=command.name,
            geometry=command.geometry,
            notes=command.notes,
        )
        if updated is None:
            raise FieldNotFoundError(command.field_id)

        await self._audit(
            "field.update",
            "field",
            str(command.field_id),
            {
                "before": before.model_dump(mode="json"),
                "patch": command.model_dump(mode="json", exclude_none=True),
            },
        )
        return updated

    async def delete(self, command: DeleteFieldCommand) -> None:
        field = await self._repo.get(command.field_id)
        if field is None:
            raise FieldNotFoundError(command.field_id)
        snapshot = field.model_dump(mode="json")

        await self._audit("field.delete", "field", str(command.field_id), snapshot)

        deleted = await self._repo.delete(command.field_id)
        if not deleted:
            raise FieldNotFoundError(command.field_id)

    async def get(self, field_id: UUID) -> Field | None:
        return await self._repo.get(field_id)

    async def list(self) -> list[Field]:
        return await self._repo.list()
