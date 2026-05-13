"""Mappers: wire DTOs <-> domain commands/views for fields."""

from uuid import UUID

from leitstand_backend.adapters.inbound.web.fields.dto import FieldCreate, FieldUpdate, FieldView
from leitstand_backend.domain.model.field import Field
from leitstand_backend.ports.inbound.field_management import (
    CreateFieldCommand,
    DeleteFieldCommand,
    UpdateFieldCommand,
)


def to_create_command(req: FieldCreate) -> CreateFieldCommand:
    return CreateFieldCommand(name=req.name, geometry=req.geometry, notes=req.notes)


def to_update_command(field_id: UUID, req: FieldUpdate) -> UpdateFieldCommand:
    return UpdateFieldCommand(
        field_id=field_id,
        name=req.name,
        geometry=req.geometry,
        notes=req.notes,
    )


def to_delete_command(field_id: UUID) -> DeleteFieldCommand:
    return DeleteFieldCommand(field_id=field_id)


def to_field_view(field: Field) -> FieldView:
    return FieldView(
        id=field.id,
        name=field.name,
        geometry=field.geometry,
        area_ha=field.area_ha,
        notes=field.notes,
        created_at=field.created_at,
        updated_at=field.updated_at,
    )
