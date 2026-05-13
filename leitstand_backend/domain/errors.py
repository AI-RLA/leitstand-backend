"""Domain-level errors. Raised by services + adapters at boundaries;
caught by routers and translated to HTTP status codes."""

from uuid import UUID


class DomainError(Exception):
    """Base for all domain-level errors."""


class RobotNotFoundError(DomainError):
    def __init__(self, robot_id: str):
        super().__init__(f"robot {robot_id!r} not found")
        self.robot_id = robot_id


class FieldNotFoundError(DomainError):
    def __init__(self, field_id: UUID):
        super().__init__(f"field {field_id} not found")
        self.field_id = field_id
