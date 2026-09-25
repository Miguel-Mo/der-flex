import ipaddress
from datetime import datetime, timedelta
from typing import Annotated, Self
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

from der_flex import __version__
from der_flex.domain.models import (
    Activation,
    ConsequenceType,
    FlexibilityAggregate,
    FlexibilityDirection,
    Reservation,
)
from der_flex.domain.store import InMemoryOfferStore, OfferStore
from der_flex.observability import (
    MetricsRegistry,
    RequestBodyLimitMiddleware,
    StructuredLoggingMiddleware,
)
from der_flex.reservations import (
    IdempotencyConflict,
    InsufficientCapacity,
    ReservationNotFound,
    ReservationService,
    ReservationStateConflict,
)
from der_flex.security import (
    AccessPrincipal,
    AuthenticationError,
    Authenticator,
    AuthorizationError,
    DevelopmentAuthenticator,
)
from der_flex.security_audit import (
    LoggingSecurityAuditRecorder,
    SecurityAuditEvent,
    SecurityAuditRecorder,
)


class FlexibilityResponse(BaseModel):
    data: list[FlexibilityAggregate]


class ReservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    zone_id: str = Field(min_length=1, max_length=64)
    interval_start: datetime
    interval_end: datetime
    direction: FlexibilityDirection
    power_kw: float = Field(gt=0, le=1_000_000)
    consequence_type: ConsequenceType = "DEFER"
    callback_url: AnyHttpUrl | None = None

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if self.interval_start.tzinfo is None or self.interval_end.tzinfo is None:
            raise ValueError("interval timestamps must include a timezone")
        if self.interval_end <= self.interval_start:
            raise ValueError("interval_end must be after interval_start")
        if self.interval_end - self.interval_start > timedelta(days=1):
            raise ValueError("reservation interval cannot exceed one day")
        if self.callback_url is not None:
            if self.callback_url.scheme != "https":
                raise ValueError("callback_url must use HTTPS")
            if self.callback_url.username or self.callback_url.password:
                raise ValueError("callback_url cannot contain credentials")
            host = self.callback_url.host
            if host is None or host.lower() == "localhost":
                raise ValueError("callback_url host is not allowed")
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                if not address.is_global:
                    raise ValueError("callback_url must use a globally routable address")
        return self


def create_app(
    store: OfferStore | None = None,
    reservation_service: ReservationService | None = None,
    authenticator: Authenticator | None = None,
    security_audit: SecurityAuditRecorder | None = None,
) -> FastAPI:
    offer_store = store or InMemoryOfferStore()
    reservations = reservation_service or ReservationService(offer_store)
    identity = authenticator or DevelopmentAuthenticator()
    audit = security_audit or LoggingSecurityAuditRecorder()
    app = FastAPI(
        title="DER Flex Aggregation API",
        version=__version__,
        description="Aggregated best-effort DER flexibility. No production guarantees.",
    )
    app.state.offer_store = offer_store
    app.state.reservation_service = reservations
    app.state.authenticator = identity
    app.state.security_audit = audit
    metrics = MetricsRegistry()
    bearer_scheme = HTTPBearer(auto_error=False, bearerFormat="JWT")
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=32_768)
    app.add_middleware(StructuredLoggingMiddleware, metrics=metrics)

    def require_access(scope: str):  # type: ignore[no-untyped-def]
        def dependency(
            credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
            authorization: Annotated[str | None, Header(alias="Authorization")] = None,
        ) -> AccessPrincipal:
            token = credentials.credentials if credentials is not None else None
            if (
                authorization is not None
                and credentials is None
                and not isinstance(identity, DevelopmentAuthenticator)
            ):
                raise AuthenticationError("invalid_authorization_scheme")
            principal = identity.authenticate(token)
            principal.require_scope(scope)
            return principal

        return dependency

    def require_zone(principal: AccessPrincipal, zone_id: str) -> None:
        principal.require_zone(zone_id)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, error: RequestValidationError) -> JSONResponse:
        # Never reflect invalid raw values: NaN/Infinity are not JSON serializable and
        # request bodies may contain sensitive data.
        details = [
            {
                "type": item["type"],
                "loc": item["loc"],
                "msg": item["msg"],
            }
            for item in error.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": details})

    @app.exception_handler(AuthenticationError)
    async def authentication_handler(request: Request, error: AuthenticationError) -> JSONResponse:
        audit.record(_audit_event(request, error.code))
        return JSONResponse(
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
            content={"detail": "authentication required"},
        )

    @app.exception_handler(AuthorizationError)
    async def authorization_handler(request: Request, error: AuthorizationError) -> JSONResponse:
        audit.record(_audit_event(request, error.code))
        return JSONResponse(status_code=404, content={"detail": "not found"})

    @app.exception_handler(InsufficientCapacity)
    @app.exception_handler(IdempotencyConflict)
    @app.exception_handler(ReservationStateConflict)
    async def conflict_handler(_request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            media_type="application/problem+json",
            content={
                "type": "about:blank",
                "title": "Reservation conflict",
                "status": 409,
                "detail": str(error),
            },
        )

    @app.exception_handler(ReservationNotFound)
    async def not_found_handler(_request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            media_type="application/problem+json",
            content={
                "type": "about:blank",
                "title": "Not found",
                "status": 404,
                "detail": str(error),
            },
        )

    @app.get("/health/live")
    def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def health_ready() -> dict[str, str]:
        if not reservations.is_ready():
            raise HTTPException(status_code=503, detail="reservation backend is unavailable")
        return {"status": "ready"}

    @app.get("/metrics", include_in_schema=False)
    def get_metrics() -> PlainTextResponse:
        return PlainTextResponse(metrics.render_prometheus())

    @app.get("/api/v1/admin/security/status")
    def get_security_status(
        _principal: Annotated[AccessPrincipal, Depends(require_access("admin:read"))],
    ) -> dict[str, str]:
        return {"status": "configured", "authenticator": type(identity).__name__}

    @app.get("/api/v1/flexibility/zones")
    def get_flexibility_zones(
        principal: Annotated[AccessPrincipal, Depends(require_access("flexibility:read"))],
    ) -> dict[str, list[str]]:
        zones = offer_store.zones(tenant_id=principal.tenant_id)
        if "*" not in principal.zone_ids:
            zones = [zone for zone in zones if zone in principal.zone_ids]
        return {"data": zones}

    @app.get("/api/v1/flexibility", response_model=FlexibilityResponse)
    def get_flexibility(
        principal: Annotated[AccessPrincipal, Depends(require_access("flexibility:read"))],
        zone_id: Annotated[str, Query(min_length=1, max_length=64)],
        start: Annotated[datetime, Query(alias="from")],
        end: Annotated[datetime, Query(alias="to")],
    ) -> FlexibilityResponse:
        require_zone(principal, zone_id)
        if start.tzinfo is None or end.tzinfo is None:
            raise HTTPException(status_code=422, detail="from and to must include a timezone")
        if end <= start:
            raise HTTPException(status_code=422, detail="to must be after from")
        if end - start > timedelta(days=7):
            raise HTTPException(status_code=422, detail="query window cannot exceed seven days")
        aggregates = offer_store.query(zone_id, start, end, tenant_id=principal.tenant_id)
        return FlexibilityResponse(data=reservations.public_residual_capacity(aggregates))

    @app.post(
        "/api/v1/reservations",
        response_model=Reservation,
        status_code=status.HTTP_201_CREATED,
    )
    def create_reservation(
        principal: Annotated[AccessPrincipal, Depends(require_access("reservation:write"))],
        request: ReservationRequest,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
        ],
    ) -> Reservation:
        require_zone(principal, request.zone_id)
        return reservations.create(
            idempotency_key=idempotency_key,
            tenant_id=principal.tenant_id,
            zone_id=request.zone_id,
            interval_start=request.interval_start,
            interval_end=request.interval_end,
            direction=request.direction,
            power_kw=request.power_kw,
            consequence_type=request.consequence_type,
            callback_url=str(request.callback_url) if request.callback_url else None,
        )

    @app.get("/api/v1/reservations/{reservation_id}", response_model=Reservation)
    def get_reservation(
        reservation_id: UUID,
        principal: Annotated[AccessPrincipal, Depends(require_access("reservation:read"))],
    ) -> Reservation:
        reservation = reservations.get(reservation_id)
        principal.require_tenant(reservation.tenant_id)
        require_zone(principal, reservation.zone_id)
        return reservation

    @app.delete("/api/v1/reservations/{reservation_id}", response_model=Reservation)
    def cancel_reservation(
        reservation_id: UUID,
        principal: Annotated[AccessPrincipal, Depends(require_access("reservation:write"))],
    ) -> Reservation:
        reservation = reservations.get(reservation_id)
        principal.require_tenant(reservation.tenant_id)
        require_zone(principal, reservation.zone_id)
        return reservations.cancel(reservation_id)

    @app.post(
        "/api/v1/reservations/{reservation_id}/activate",
        response_model=Activation,
    )
    def activate_reservation(
        reservation_id: UUID,
        principal: Annotated[AccessPrincipal, Depends(require_access("activation:write"))],
    ) -> Activation:
        reservation = reservations.get(reservation_id)
        principal.require_tenant(reservation.tenant_id)
        require_zone(principal, reservation.zone_id)
        return reservations.activate(reservation_id)

    @app.get("/api/v1/activations/{activation_id}", response_model=Activation)
    def get_activation(
        activation_id: UUID,
        principal: Annotated[AccessPrincipal, Depends(require_access("activation:read"))],
    ) -> Activation:
        activation = reservations.get_activation(activation_id)
        reservation = reservations.get(activation.reservation_id)
        principal.require_tenant(reservation.tenant_id)
        require_zone(principal, reservation.zone_id)
        return activation

    return app


def _audit_event(request: Request, reason: str) -> SecurityAuditEvent:
    route = request.scope.get("route")
    route_template = getattr(route, "path", "unmatched")
    return SecurityAuditEvent(
        outcome="denied",
        reason=reason,
        method=request.method,
        route=route_template,
    )
