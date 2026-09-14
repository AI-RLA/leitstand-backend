"""One recorded status change of a run: who caused it, and whether the robot acknowledged it."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from leitstand_backend.domain.model.mission.run_lifecycle import Actor, RunTrigger
from leitstand_backend.domain.model.mission.run_status import RunStatus


class RunTransition(BaseModel):
    """A row of the run's transition log.

    ``report_header_id`` is the robot's report counter at the time of an operator request, so a
    later report can be told from one that predates the request. ``acknowledged`` is the receipt
    of a request: True when the robot replied that it applied it, False when it refused or stayed
    silent, None while unknown or for transitions that are not requests.
    """

    run_id: UUID
    from_status: RunStatus
    to_status: RunStatus
    trigger: RunTrigger
    at: datetime
    actor: Actor
    report_header_id: int | None = None
    acknowledged: bool | None = None
    detail: dict | None = None
