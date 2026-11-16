"""JWT authentication and the three roles (admin, data_engineer, analyst)."""
import jwt

from tests.conftest import PASSWORD, token_for


def test_health_is_public(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["components"]["database"]["status"] == "up"
    assert body["components"]["object_storage"]["status"] == "up"


def test_login_returns_jwt_with_role(client):
    r = client.post("/api/v1/auth/login", json={"email": "Engineer@orbit.local", "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    claims = jwt.decode(body["access_token"], options={"verify_signature": False})
    assert claims["role"] == "data_engineer" and "exp" in claims
    assert body["me"]["role_label"] == "Data Engineer"
    assert "imports.write" in body["me"]["permissions"]


def test_wrong_password_and_unknown_user_are_rejected_the_same_way(client):
    a = client.post("/api/v1/auth/login", json={"email": "admin@orbit.local", "password": "nope"})
    b = client.post("/api/v1/auth/login", json={"email": "ghost@orbit.local", "password": "nope"})
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["message"] == b.json()["error"]["message"]


def test_login_is_throttled_after_repeated_failures(client):
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": "admin@orbit.local", "password": "bad"})
    r = client.post("/api/v1/auth/login", json={"email": "admin@orbit.local", "password": PASSWORD})
    assert r.status_code == 429


def test_protected_routes_need_a_valid_token(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/datasets", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401
    forged = jwt.encode({"sub": "x", "role": "admin", "exp": 9999999999}, "wrong-secret", algorithm="HS256")
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_me(client, as_analyst):
    r = client.get("/api/v1/auth/me", headers=as_analyst)
    assert r.status_code == 200
    me = r.json()
    assert me["user"]["role"] == "analyst"
    assert "imports.write" not in me["permissions"] and "datasets.read" in me["permissions"]


def test_permission_matrix(client, as_admin, as_engineer, as_analyst):
    csv = ("a.csv", b"id,name\n1,x\n", "text/csv")
    # upload: admin + data engineer only
    assert client.post("/api/v1/imports", headers=as_analyst, files={"file": csv}).status_code == 403
    assert client.post("/api/v1/imports", headers=as_engineer, files={"file": csv}).status_code == 201
    # catalogue: every role can read
    for h in (as_admin, as_engineer, as_analyst):
        assert client.get("/api/v1/datasets", headers=h).status_code == 200
        assert client.get("/api/v1/imports", headers=h).status_code == 200
    # user management and audit: admin only
    for h in (as_engineer, as_analyst):
        assert client.get("/api/v1/users", headers=h).status_code == 403
        assert client.get("/api/v1/audit", headers=h).status_code == 403
    assert client.get("/api/v1/users", headers=as_admin).status_code == 200
    assert client.get("/api/v1/audit", headers=as_admin).status_code == 200


def test_admin_manages_users_and_roles(client, as_admin):
    r = client.post("/api/v1/users", headers=as_admin, json={
        "email": "new@orbit.local", "full_name": "New Person", "role": "analyst", "password": "a-long-password"})
    assert r.status_code == 201
    uid = r.json()["id"]
    # only the three roles exist
    bad = client.post("/api/v1/users", headers=as_admin, json={
        "email": "x@orbit.local", "full_name": "X", "role": "viewer", "password": "a-long-password"})
    assert bad.status_code == 422
    dup = client.post("/api/v1/users", headers=as_admin, json={
        "email": "NEW@orbit.local", "full_name": "Dup", "role": "analyst", "password": "a-long-password"})
    assert dup.status_code == 409

    # promote to data engineer → can now upload
    assert client.patch(f"/api/v1/users/{uid}", headers=as_admin, json={"role": "data_engineer"}).status_code == 200
    h = {"Authorization": f"Bearer {token_for(client, 'new@orbit.local', 'a-long-password')}"}
    assert client.post("/api/v1/imports", headers=h, files={"file": ("a.csv", b"id\n1\n", "text/csv")}).status_code == 201

    # disable → existing token stops working, login refused
    assert client.patch(f"/api/v1/users/{uid}", headers=as_admin, json={"is_active": False}).status_code == 200
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "new@orbit.local", "password": "a-long-password"}).status_code == 401

    actions = [e["action"] for e in client.get("/api/v1/audit", headers=as_admin).json()["items"]]
    assert "user.create" in actions and "user.update" in actions and "auth.login" in actions


def test_admin_cannot_lock_themselves_out(client, as_admin):
    me = client.get("/api/v1/auth/me", headers=as_admin).json()["user"]
    r = client.patch(f"/api/v1/users/{me['id']}", headers=as_admin, json={"role": "analyst"})
    assert r.status_code == 422
    r = client.patch(f"/api/v1/users/{me['id']}", headers=as_admin, json={"is_active": False})
    assert r.status_code == 422


def test_role_change_takes_effect_without_new_login(client, as_admin, as_engineer):
    users = client.get("/api/v1/users", headers=as_admin).json()
    eng = next(u for u in users if u["email"] == "engineer@orbit.local")
    client.patch(f"/api/v1/users/{eng['id']}", headers=as_admin, json={"role": "analyst"})
    r = client.post("/api/v1/imports", headers=as_engineer, files={"file": ("a.csv", b"id\n1\n", "text/csv")})
    assert r.status_code == 403  # permissions are read from the database role, not from the token
