"""Privacy-minimising security decision audit events."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import Protocol

logger = logging.getLogger("der_flex.security")


@dataclass(frozen=True)
class SecurityAuditEvent:
    outcome: str
    reason: str
    method: str
    route: str


class SecurityAuditRecorder(Protocol):
    def record(self, event: SecurityAuditEvent) -> None: ...


class LoggingSecurityAuditRecorder:
    """Record only the decision and route template; never identity or request data."""

    def record(self, event: SecurityAuditEvent) -> None:
        logger.warning(
            json.dumps(
                {"event": "security_decision", **asdict(event)},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
