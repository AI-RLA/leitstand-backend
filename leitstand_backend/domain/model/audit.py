"""The vocabulary an audit row uses to say who acted, and on whose authority.

A row is written for a change someone commanded. A change the world causes rather than an actor,
a robot dropping mid-mission or telemetry reporting a stage finished, writes no row here: its
durable trace is the mission record's status, errors and stage states. Silence in this log means
nobody commanded it, not that nothing happened.
"""

from __future__ import annotations

from typing import Literal

Actor = Literal["human", "ai_agent"]
ACTOR_HUMAN: Actor = "human"
ACTOR_AI_AGENT: Actor = "ai_agent"

# direct: the actor's own authority, operating the fleet or deciding on a proposal.
# approved_proposal: an agent write a human approved on screen. autonomous: an agent acting without
# approval, which nothing does today, so such a row is an alarm rather than a normal state.
#
# Loosening a capability so the agent may act unapproved needs a new value, never this one: retained
# rows already mean "nobody approved this", and reusing the value would rewrite what they say.
Authority = Literal["direct", "approved_proposal", "autonomous"]
AUTHORITY_DIRECT: Authority = "direct"
AUTHORITY_APPROVED_PROPOSAL: Authority = "approved_proposal"
AUTHORITY_AUTONOMOUS: Authority = "autonomous"
