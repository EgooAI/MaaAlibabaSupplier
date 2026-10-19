"""Explicit login for business API tests; production authentication stays enabled."""

import os


def authenticate(client):
    response = client.post("/api/auth/login", json={"secret_sha256": os.environ["MAA_AUTH_SECRET_SHA256"]})
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['data']['token']}"
    from backend.app.shared.backend.account_context import get_account_context

    client.headers["X-Account-Epoch"] = get_account_context().epoch
    return client
