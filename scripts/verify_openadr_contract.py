"""Validate DER Flex fixtures against an OpenADR 3.1.0 OpenAPI document."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

import yaml
from openapi_schema_validator import OAS30Validator

ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec", type=Path, help="path to OpenADR 3.1.0 openadr3.yaml")
    args = parser.parse_args()
    spec = cast(dict[str, Any], yaml.safe_load(args.spec.read_text(encoding="utf-8")))
    fixtures = (
        ("event", ROOT / "tests/fixtures/openadr/dispatch-event-3.1.0.json"),
        ("reportRequest", ROOT / "tests/fixtures/openadr/activation-report-3.1.0.json"),
    )
    for schema_name, fixture_path in fixtures:
        root_schema = {
            "$ref": f"#/components/schemas/{schema_name}",
            "components": spec["components"],
        }
        OAS30Validator(root_schema).validate(load_json(fixture_path))
        print(f"OpenADR 3.1.0 {schema_name}: valid")


if __name__ == "__main__":
    main()
