"""Run a deterministic, local S2 PEBC session over WebSocket.

This is a protocol spike. Discovery, pairing and authentication from S2 Connect are
deliberately outside this smoke test; the exchanged S2 JSON messages are validated by
the schema-derived s2-python parser at both endpoints.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

from s2python.s2_parser import S2Parser
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "s2" / "pebc_session.json"
)
PARSER = S2Parser()


def load_fixture() -> dict[str, dict[str, Any]]:
    return cast(
        dict[str, dict[str, Any]],
        json.loads(FIXTURE_PATH.read_text(encoding="utf-8")),
    )


def encode_validated(message: dict[str, Any]) -> str:
    encoded = json.dumps(message, separators=(",", ":"), sort_keys=True)
    PARSER.parse_as_any_message(encoded)
    return encoded


def decode_validated(encoded: str) -> Any:
    return PARSER.parse_as_any_message(encoded)


def reception_status(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "message_type": "ReceptionStatus",
        "subject_message_id": message["message_id"],
        "status": "OK",
    }


async def send_message(
    socket: Any, actor: str, message: dict[str, Any], transcript: list[str]
) -> None:
    encoded = encode_validated(message)
    await socket.send(encoded)
    transcript.append(f"{actor} -> {message['message_type']}")


async def receive_type(socket: Any, expected_type: str, actor: str, transcript: list[str]) -> Any:
    parsed = decode_validated(await socket.recv())
    if parsed.message_type != expected_type:
        raise AssertionError(f"{actor} expected {expected_type}, received {parsed.message_type}")
    transcript.append(f"{actor} <- {expected_type}")
    return parsed


async def acknowledge(
    socket: Any, actor: str, message: dict[str, Any], transcript: list[str]
) -> None:
    await send_message(socket, actor, reception_status(message), transcript)


async def expect_ack(
    socket: Any, message: dict[str, Any], actor: str, transcript: list[str]
) -> None:
    status = await receive_type(socket, "ReceptionStatus", actor, transcript)
    if str(status.subject_message_id) != message["message_id"] or status.status.value != "OK":
        raise AssertionError(f"Invalid reception status for {message['message_type']}")


async def cem_session(
    socket: Any, fixture: dict[str, dict[str, Any]], transcript: list[str]
) -> None:
    await receive_type(socket, "Handshake", "CEM", transcript)
    await acknowledge(socket, "CEM", fixture["rm_handshake"], transcript)

    await send_message(socket, "CEM", fixture["cem_handshake"], transcript)
    await expect_ack(socket, fixture["cem_handshake"], "CEM", transcript)

    await send_message(socket, "CEM", fixture["handshake_response"], transcript)
    await expect_ack(socket, fixture["handshake_response"], "CEM", transcript)

    await receive_type(socket, "ResourceManagerDetails", "CEM", transcript)
    await acknowledge(socket, "CEM", fixture["resource_manager_details"], transcript)

    await send_message(socket, "CEM", fixture["select_control_type"], transcript)
    await expect_ack(socket, fixture["select_control_type"], "CEM", transcript)

    await receive_type(socket, "PEBC.PowerConstraints", "CEM", transcript)
    await acknowledge(socket, "CEM", fixture["power_constraints"], transcript)

    await receive_type(socket, "PowerForecast", "CEM", transcript)
    await acknowledge(socket, "CEM", fixture["power_forecast"], transcript)

    await send_message(socket, "CEM", fixture["instruction"], transcript)
    await expect_ack(socket, fixture["instruction"], "CEM", transcript)


async def rm_session(uri: str, fixture: dict[str, dict[str, Any]], transcript: list[str]) -> None:
    async with connect(uri) as socket:
        await send_message(socket, "RM", fixture["rm_handshake"], transcript)
        await expect_ack(socket, fixture["rm_handshake"], "RM", transcript)

        await receive_type(socket, "Handshake", "RM", transcript)
        await acknowledge(socket, "RM", fixture["cem_handshake"], transcript)

        await receive_type(socket, "HandshakeResponse", "RM", transcript)
        await acknowledge(socket, "RM", fixture["handshake_response"], transcript)

        await send_message(socket, "RM", fixture["resource_manager_details"], transcript)
        await expect_ack(socket, fixture["resource_manager_details"], "RM", transcript)

        await receive_type(socket, "SelectControlType", "RM", transcript)
        await acknowledge(socket, "RM", fixture["select_control_type"], transcript)

        await send_message(socket, "RM", fixture["power_constraints"], transcript)
        await expect_ack(socket, fixture["power_constraints"], "RM", transcript)

        await send_message(socket, "RM", fixture["power_forecast"], transcript)
        await expect_ack(socket, fixture["power_forecast"], "RM", transcript)

        await receive_type(socket, "PEBC.Instruction", "RM", transcript)
        await acknowledge(socket, "RM", fixture["instruction"], transcript)


async def run_session() -> list[str]:
    fixture = load_fixture()
    transcript: list[str] = []

    async with serve(
        lambda socket: cem_session(socket, fixture, transcript), "127.0.0.1", 0
    ) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        await rm_session(f"ws://127.0.0.1:{port}", fixture, transcript)

    return transcript


def main() -> None:
    transcript = asyncio.run(run_session())
    for line in transcript:
        print(line)
    print(f"S2 PEBC smoke session completed: {len(transcript) // 2} validated wire messages")


if __name__ == "__main__":
    main()
