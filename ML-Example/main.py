"""
HTTP Cloud Function that serves Iris species predictions from a logistic regression model.
The model is trained once when an instance starts and kept in memory for every request.
"""

import math

import functions_framework
from flask import jsonify
from sklearn.datasets import load_iris
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

N_FEATURES = 4
MAX_BATCH_SIZE = 100
MAX_ITER = 200
RANDOM_STATE = 42


def train_model():
    """
    Train a scaler and logistic regression pipeline on the Iris dataset.

    Returns
    -------
    model : sklearn.pipeline.Pipeline
        Fitted StandardScaler followed by LogisticRegression.
    class_name_list : list of str
        Species names indexed by class number.
    """
    iris = load_iris()
    model = make_pipeline(
        StandardScaler(), LogisticRegression(max_iter=MAX_ITER, random_state=RANDOM_STATE)
    )
    model.fit(iris.data, iris.target)
    return model, [str(name) for name in iris.target_names]


# trained at cold start; Iris trains in milliseconds, so no file storage is needed
MODEL, CLASS_NAME_LIST = train_model()


def validate_features(features):
    """
    Check that features is a list of exactly N_FEATURES finite numbers.

    Parameters
    ----------
    features : object
        One measurement row from the request body.

    Returns
    -------
    error : str or None
        Description of the problem, or None if the row is valid.
    """
    if not isinstance(features, list) or len(features) != N_FEATURES:
        return f"each instance must be a list of {N_FEATURES} numbers"
    for value in features:
        # bool is a subclass of int, and strings such as "5.1" are not numbers
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"feature values must be numbers, got {value!r}"
        if not math.isfinite(value):
            return f"feature values must be finite, got {value!r}"
    return None


def predict_rows(row_list):
    """
    Predict species for validated measurement rows.

    Parameters
    ----------
    row_list : list of list of float
        Each row holds sepal length, sepal width, petal length, and petal width in cm.

    Returns
    -------
    prediction_list : list of dict
        Per row: predicted_class, predicted_label, and confidences keyed by species name.
    """
    probability_matrix = MODEL.predict_proba(row_list)
    prediction_list = []
    for probability_row in probability_matrix:
        predicted_class = int(probability_row.argmax())
        prediction_list.append({
            "predicted_class": predicted_class,
            "predicted_label": CLASS_NAME_LIST[predicted_class],
            "confidences": {
                name: round(float(p), 4) for name, p in zip(CLASS_NAME_LIST, probability_row)
            },
        })
    return prediction_list


def error_response(message, status):
    """Return a JSON error body with the given HTTP status."""
    return jsonify({"error": message}), status


@functions_framework.http
def predict(request):
    """
    Predict the Iris species for one or more flowers.

    Request body (POST, application/json), either:
        {"features": [5.1, 3.5, 1.4, 0.2]}
        {"instances": [[5.1, 3.5, 1.4, 0.2], [6.7, 3.0, 5.2, 2.3]]}

    Parameters
    ----------
    request : flask.Request
        Incoming HTTP request.

    Returns
    -------
    response : tuple of (flask.Response, int)
        One prediction object for "features", or {"predictions": [...]} for "instances".
    """
    if request.method != "POST":
        return error_response("use POST", 405)
    if not request.is_json:
        return error_response("Content-Type must be application/json", 400)

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error_response("request body must be a JSON object", 400)

    o_batch = "instances" in body
    if not o_batch and "features" not in body:
        return error_response("body must contain 'features' or 'instances'", 400)

    row_list = body["instances"] if o_batch else [body["features"]]
    if not isinstance(row_list, list) or not row_list:
        return error_response("'instances' must be a non-empty list", 400)
    if len(row_list) > MAX_BATCH_SIZE:
        return error_response(f"at most {MAX_BATCH_SIZE} instances per request", 400)

    for index, row in enumerate(row_list):
        error = validate_features(row)
        if error:
            label = f"instances[{index}]" if o_batch else "features"
            return error_response(f"{label}: {error}", 400)

    prediction_list = predict_rows(row_list)
    if o_batch:
        return jsonify({"predictions": prediction_list}), 200
    return jsonify(prediction_list[0]), 200
