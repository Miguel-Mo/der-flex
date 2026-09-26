from copy import deepcopy

import pytest

from scripts.verify_traceability import collected_tests, load_matrix, validate_matrix


def test_traceability_matrix_is_complete_and_resolvable() -> None:
    report = validate_matrix(load_matrix(), collected_tests())

    assert report["passed"] is True
    assert report["claim_count"] == 21
    assert report["original_audit_claims_complete"] is True


def test_traceability_rejects_duplicate_claim() -> None:
    matrix = deepcopy(load_matrix())
    matrix["claims"].append(deepcopy(matrix["claims"][0]))

    with pytest.raises(RuntimeError, match="duplicate or empty claim"):
        validate_matrix(matrix, collected_tests())


def test_traceability_rejects_uncollected_test_node() -> None:
    matrix = deepcopy(load_matrix())
    matrix["claims"][0]["test_nodes"] = ["tests/missing.py::test_missing"]

    with pytest.raises(RuntimeError, match="test node was not collected"):
        validate_matrix(matrix, collected_tests())
