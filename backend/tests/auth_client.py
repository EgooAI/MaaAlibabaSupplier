"""Explicit login for business API tests; production authentication stays enabled."""

import os


def authenticate(client):
    response = client.post("/api/auth/login", json={"secret_sha256": os.environ["MAA_AUTH_SECRET_SHA256"]})
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['data']['token']}"
    return client
