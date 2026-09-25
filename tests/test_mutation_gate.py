from pathlib import Path

import pytest

from scripts.mutation_gate import MUTATIONS, Mutation, apply_mutation


def test_mutation_catalog_has_unique_ids_and_targets() -> None:
    identifiers = {mutation.identifier for mutation in MUTATIONS}

    assert len(identifiers) == len(MUTATIONS)
    assert all(mutation.tests for mutation in MUTATIONS)
    assert all((Path(__file__).parents[1] / mutation.path).is_file() for mutation in MUTATIONS)


def test_mutation_requires_exactly_one_site(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    target = source_root / "der_flex" / "example.py"
    target.parent.mkdir(parents=True)
    target.write_text("safe = True\n", encoding="utf-8")
    missing = Mutation(
        identifier="missing",
        path="src/der_flex/example.py",
        original="safe = False",
        replacement="safe = True",
        tests=("tests/test_example.py",),
    )

    with pytest.raises(RuntimeError, match="expected one mutation site"):
        apply_mutation(source_root, missing)
