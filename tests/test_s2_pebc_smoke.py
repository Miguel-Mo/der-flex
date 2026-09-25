import asyncio
import json
from pathlib import Path

from s2python.s2_parser import S2Parser

from scripts.s2_pebc_smoke import FIXTURE_PATH, run_session


def test_every_fixture_message_is_accepted_by_s2_parser() -> None:
    fixture = json.loads(Path(FIXTURE_PATH).read_text(encoding="utf-8"))
    parser = S2Parser()

    parsed_types = {
        parser.parse_as_any_message(json.dumps(message)).message_type
        for message in fixture.values()
    }

    assert parsed_types == {
        "Handshake",
        "HandshakeResponse",
        "ResourceManagerDetails",
        "SelectControlType",
        "PEBC.PowerConstraints",
        "PowerForecast",
        "PEBC.Instruction",
    }


def test_complete_session_exchanges_sixteen_validated_wire_messages() -> None:
    transcript = asyncio.run(run_session())

    # Each wire message is observed once at send and once at receive.
    assert len(transcript) == 32
    assert "CEM <- PEBC.PowerConstraints" in transcript
    assert "RM <- PEBC.Instruction" in transcript
