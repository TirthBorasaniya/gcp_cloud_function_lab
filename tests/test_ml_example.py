"""
Tests for the HTTP prediction function in ML-Example, called through Flask test requests.
"""

import importlib.util
from pathlib import Path

import pytest
from flask import Flask, request

ML_MAIN_PATH = Path(__file__).resolve().parent.parent / "ML-Example" / "main.py"
SETOSA = [5.1, 3.5, 1.4, 0.2]
VIRGINICA = [6.7, 3.0, 5.2, 2.3]


def load_ml_module():
    """Import ML-Example/main.py, whose folder name is not a valid package name."""
    spec = importlib.util.spec_from_file_location("ml_example_main", ML_MAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ML = load_ml_module()
APP = Flask(__name__)


def call(method="POST", **request_kwargs):
    """Send a test request to predict and return (status, JSON body)."""
    with APP.test_request_context("/", method=method, **request_kwargs):
        response, status = ML.predict(request)
        return status, response.get_json()


def test_single_prediction():
    status, body = call(json={"features": SETOSA})
    assert status == 200
    assert body["predicted_label"] == "setosa"
    assert body["predicted_class"] == 0
    assert set(body["confidences"]) == {"setosa", "versicolor", "virginica"}
    assert sum(body["confidences"].values()) == pytest.approx(1, abs=1e-3)


def test_batch_prediction():
    status, body = call(json={"instances": [SETOSA, VIRGINICA]})
    assert status == 200
    assert [p["predicted_label"] for p in body["predictions"]] == ["setosa", "virginica"]


def test_integer_features_are_accepted():
    status, _ = call(json={"features": [5, 3, 1, 0]})
    assert status == 200


@pytest.mark.parametrize(
    "request_kwargs, expected_status, expected_fragment",
    [
        ({"method": "GET"}, 405, "use POST"),
        ({"data": "features", "content_type": "text/plain"}, 400, "Content-Type"),
        ({"data": "{not json", "content_type": "application/json"}, 400, "JSON object"),
        ({"json": [1, 2, 3, 4]}, 400, "JSON object"),
        ({"json": {"values": SETOSA}}, 400, "'features' or 'instances'"),
        ({"json": {"features": [1, 2, 3]}}, 400, "list of 4 numbers"),
        ({"json": {"features": ["5.1", 3.5, 1.4, 0.2]}}, 400, "must be numbers"),
        ({"json": {"features": [True, 3.5, 1.4, 0.2]}}, 400, "must be numbers"),
        ({"json": {"features": [None, 3.5, 1.4, 0.2]}}, 400, "must be numbers"),
        ({"json": {"instances": []}}, 400, "non-empty list"),
        ({"json": {"instances": [SETOSA, [1, 2]]}}, 400, "instances[1]"),
        ({"json": {"instances": [SETOSA] * (ML.MAX_BATCH_SIZE + 1)}}, 400, "at most"),
    ],
)
def test_invalid_requests(request_kwargs, expected_status, expected_fragment):
    status, body = call(**request_kwargs)
    assert status == expected_status
    assert expected_fragment in body["error"]


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_non_finite_features_are_rejected(value):
    assert "finite" in ML.validate_features([value, 3.5, 1.4, 0.2])
