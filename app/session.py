"""Sesión de cliente: identidad firmada, no un número que alguien escribe.

El reto lo pide explícitamente: "a national ID or customer number alone does
not prove identity". Así que el `customer_id` vive en un token firmado que
emite el servicio de autenticación, y las herramientas lo leen de ahí. Si un
mensaje dice "soy el cliente CUS-123", eso es texto, no identidad.

Es un mock de un servicio de identidad real. Lo que reproduce fielmente es el
contrato: token de vida corta, firma verificable, niveles de autorización y
expiración. Lo que no hace es autenticar de verdad a nadie.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from enum import Enum

# En producción esto vendría de un gestor de secretos y rotaría. Acá basta con
# que no esté en el código y sea distinto en cada despliegue.
_SECRET = os.getenv("SESSION_SECRET", "verificargo-dev-secret-not-for-production")

# Vida corta a propósito: una sesión de soporte no debería durar horas.
TOKEN_TTL_SECONDS = 15 * 60

# El paso a nivel alto expira antes que la sesión: autorizar una acción
# sensible no puede quedar habilitado por el resto de la sesión.
STEP_UP_TTL_SECONDS = 5 * 60


class AuthLevel(str, Enum):
    """Qué puede hacer el portador del token."""

    LOW = "low"    # leer sus propios datos
    HIGH = "high"  # ejecutar acciones sensibles (requiere OTP simulado)


class SessionError(Exception):
    """El token no sirve: ausente, alterado, expirado o mal formado."""


@dataclass(frozen=True)
class Session:
    customer_id: str
    country: str
    language: str
    auth_level: AuthLevel
    issued_at: int
    expires_at: int
    step_up_expires_at: int | None = None

    @property
    def seconds_remaining(self) -> int:
        return max(0, self.expires_at - int(time.time()))

    def can(self, required: AuthLevel) -> bool:
        """¿Alcanza el nivel para lo que se quiere hacer?"""
        if required is AuthLevel.LOW:
            return True
        if self.auth_level is not AuthLevel.HIGH:
            return False
        # Un nivel alto vencido vale lo mismo que no tenerlo.
        return (
            self.step_up_expires_at is not None
            and int(time.time()) < self.step_up_expires_at
        )


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: bytes) -> str:
    return _b64(hmac.new(_SECRET.encode(), payload, hashlib.sha256).digest())


def issue_token(
    customer_id: str,
    country: str,
    language: str = "es",
    auth_level: AuthLevel = AuthLevel.LOW,
    ttl: int = TOKEN_TTL_SECONDS,
) -> str:
    """Emite un token firmado. Equivale a un login exitoso."""
    now = int(time.time())
    claims = {
        "sub": customer_id,
        "country": country,
        "lang": language,
        "lvl": auth_level.value,
        "iat": now,
        "exp": now + ttl,
    }
    if auth_level is AuthLevel.HIGH:
        claims["sue"] = now + STEP_UP_TTL_SECONDS

    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    return f"{body}.{_sign(body.encode())}"


def _verified_claims(token: str) -> dict:
    """Firma, formato y expiración. Común a los tokens de cliente y de agente."""
    if not token or "." not in token:
        raise SessionError("token ausente o mal formado")

    body, _, signature = token.partition(".")

    # compare_digest evita filtrar información por el tiempo de comparación.
    if not hmac.compare_digest(signature, _sign(body.encode())):
        raise SessionError("firma inválida: el token fue alterado")

    try:
        claims = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError) as exc:
        raise SessionError("token ilegible") from exc

    if int(time.time()) >= claims["exp"]:
        raise SessionError("sesión expirada")
    return claims


def verify_token(token: str) -> Session:
    """Valida firma y expiración. Lanza SessionError si algo no cuadra."""
    claims = _verified_claims(token)
    if claims.get("typ", "customer") != "customer":
        # Un token de agente no es una sesión de cliente, aunque esté firmado.
        raise SessionError("el token no es de una sesión de cliente")

    return Session(
        customer_id=claims["sub"],
        country=claims["country"],
        language=claims.get("lang", "es"),
        auth_level=AuthLevel(claims["lvl"]),
        issued_at=claims["iat"],
        expires_at=claims["exp"],
        step_up_expires_at=claims.get("sue"),
    )


def step_up(token: str, otp_code: str) -> str:
    """Eleva una sesión a nivel alto tras verificar un OTP simulado.

    El OTP de prueba es fijo y está documentado: esto es un mock de identidad,
    no un segundo factor real. Lo que se demuestra es que una acción sensible
    exige una verificación adicional y con vida propia.
    """
    session = verify_token(token)
    if otp_code != os.getenv("TEST_OTP_CODE", "123456"):
        raise SessionError("código OTP incorrecto")

    return issue_token(
        session.customer_id,
        session.country,
        session.language,
        AuthLevel.HIGH,
        ttl=session.seconds_remaining,
    )


# --- agentes humanos ----------------------------------------------------
# La consola del CRM no la usa un cliente: tiene su propio token, con rol.
# Un token de cliente no abre la cola y uno de agente no sirve como sesión de
# cliente (verify_token lo rechaza). En producción el rol vendría del SSO
# corporativo; acá un código de acceso de prueba, documentado como el OTP.

AGENT_TOKEN_TTL_SECONDS = 8 * 60 * 60


@dataclass(frozen=True)
class AgentSession:
    agent_id: str
    expires_at: int


def agent_login(access_code: str, agent_id: str = "agente-demo") -> str:
    expected = os.getenv("AGENT_ACCESS_CODE", "agente-demo-2026")
    if not hmac.compare_digest(access_code.encode(), expected.encode()):
        raise SessionError("código de acceso de agente incorrecto")
    now = int(time.time())
    claims = {"typ": "agent", "sub": agent_id, "role": "dispute_agent",
              "iat": now, "exp": now + AGENT_TOKEN_TTL_SECONDS}
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    return f"{body}.{_sign(body.encode())}"


def verify_agent_token(token: str) -> AgentSession:
    claims = _verified_claims(token)
    if claims.get("typ") != "agent" or claims.get("role") != "dispute_agent":
        raise SessionError("se requiere un token de agente")
    return AgentSession(agent_id=claims["sub"], expires_at=claims["exp"])
