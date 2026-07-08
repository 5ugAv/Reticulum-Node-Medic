import os


def test_ci_workflow_present_and_runs_pytest():
    path = os.path.join(os.path.dirname(__file__), "..", ".github",
                        "workflows", "ci.yml")
    assert os.path.isfile(path), "CI workflow missing"
    body = open(path).read()
    assert "pytest" in body
    assert "setup-python" in body
