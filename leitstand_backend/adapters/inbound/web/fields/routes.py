"""Field CRUD endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from leitstand_backend.adapters.inbound.web.fields.dto import FieldCreate, FieldUpdate, FieldView
from leitstand_backend.adapters.inbound.web.fields.mappers import (
    to_create_command,
    to_delete_command,
    to_field_view,
    to_update_command,
)
from leitstand_backend.domain.errors import FieldNotFoundError
from leitstand_backend.infrastructure.deps import get_field_management_use_case
from leitstand_backend.ports.inbound.field_management import FieldManagementUseCase

router = APIRouter(prefix="/api/v1/fields", tags=["fields"])


@router.get("/", response_model=list[FieldView], operation_id="list_fields")
async def list_fields(
    uc: FieldManagementUseCase = Depends(get_field_management_use_case),
) -> list[FieldView]:
    """List the agricultural fields, each with its name, area in hectares and boundary."""
    return [to_field_view(f) for f in await uc.list()]


@router.get("/{field_id}", response_model=FieldView, operation_id="get_field")
async def get_field(
    field_id: UUID,
    uc: FieldManagementUseCase = Depends(get_field_management_use_case),
) -> FieldView:
    """Return one agricultural field's name, area in hectares and boundary.

    Identify the field by field_id from list_fields, which is also how a field named by the
    operator is resolved.
    """
    field = await uc.get(field_id)
    if field is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "field not found")
    return to_field_view(field)


@router.post(
    "/", response_model=FieldView, status_code=status.HTTP_201_CREATED, operation_id="create_field"
)
async def create_field(
    body: FieldCreate,
    uc: FieldManagementUseCase = Depends(get_field_management_use_case),
) -> FieldView:
    """Create an agricultural field from a name and a GeoJSON polygon boundary.

    The boundary is normally drawn on the map or imported rather than typed.
    """
    field = await uc.create(to_create_command(body))
    return to_field_view(field)


@router.patch("/{field_id}", response_model=FieldView, operation_id="update_field")
async def update_field(
    field_id: UUID,
    body: FieldUpdate,
    uc: FieldManagementUseCase = Depends(get_field_management_use_case),
) -> FieldView:
    """Change a field's name, notes, or boundary. Identify it by field_id from list_fields."""
    try:
        field = await uc.update(to_update_command(field_id, body))
    except FieldNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "field not found")
    return to_field_view(field)


@router.delete("/{field_id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="delete_field")
async def delete_field(
    field_id: UUID,
    uc: FieldManagementUseCase = Depends(get_field_management_use_case),
) -> None:
    """Delete a field permanently. Identify it by field_id from list_fields."""
    try:
        await uc.delete(to_delete_command(field_id))
    except FieldNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "field not found")
