import os
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import jwt

os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-that-is-at-least-32-bytes")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.example")
os.environ.setdefault("ADMIN_PASSWORD", "TestPassword-Only-For-Tests")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.database import Base, get_db
from app.security import hash_password


test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
main.engine = test_engine
main.SessionLocal = TestingSession


def override_db():
    with TestingSession() as session:
        yield session


main.app.dependency_overrides[get_db] = override_db


def setup_function():
    main.password_reset_rate_limiter.clear()
    main.registration_rate_limiter.clear()
    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)
    with TestingSession() as session:
        session.add(
            main.User(
                email="admin@test.example",
                full_name="Admin Test",
                password_hash=hash_password("TestPassword-Only-For-Tests"),
                role="admin",
            )
        )
        session.add(
            main.User(
                email="worker@test.example",
                full_name="Employee Test",
                password_hash=hash_password("TestEmployee-Password-123"),
                role="employee",
            )
        )
        session.commit()


def test_health_and_authentication():
    with TestClient(main.app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/api/openapi.json").status_code == 200
        assert client.get("/metrics").status_code == 200
        assert client.get("/api/customers").status_code == 401
        public_registration = client.post(
            "/api/auth/register",
            json={
                "email": "self-made-admin@test.example",
                "full_name": "Self Made Admin",
                "password": "StrongPassword-123",
                "confirm_password": "StrongPassword-123",
                "role": "admin",
            },
        )
        assert public_registration.status_code == 201
        assert public_registration.json()["role"] == "employee"
        assert client.post(
            "/api/auth/login",
            json={"email": "self-made-admin@test.example", "password": "StrongPassword-123"},
        ).json()["user"]["role"] == "employee"
        response = client.post(
            "/api/auth/login",
            json={"email": "admin@test.example", "password": "TestPassword-Only-For-Tests"},
        )
        assert response.status_code == 200
        assert response.json()["user"]["role"] == "admin"


def test_remember_me_uses_configured_longer_token_lifetime():
    with TestClient(main.app) as client:
        credentials = {
            "email": "admin@test.example",
            "password": "TestPassword-Only-For-Tests",
        }
        session_login = client.post("/api/auth/login", json=credentials)
        remembered_login = client.post(
            "/api/auth/login", json={**credentials, "remember_me": True}
        )

        assert session_login.status_code == 200
        assert remembered_login.status_code == 200
        secret = main.get_settings().JWT_SECRET
        session_claims = jwt.decode(
            session_login.json()["access_token"], secret, algorithms=["HS256"]
        )
        remembered_claims = jwt.decode(
            remembered_login.json()["access_token"], secret, algorithms=["HS256"]
        )
        now = datetime.now(timezone.utc).timestamp()

        assert 60 * 60 - 2 <= session_claims["exp"] - now <= 60 * 60
        assert 30 * 24 * 60 * 60 - 2 <= remembered_claims["exp"] - now <= 30 * 24 * 60 * 60


def test_customer_crud_and_dashboard():
    with TestClient(main.app) as client:
        login = client.post(
            "/api/auth/login",
            json={"email": "admin@test.example", "password": "TestPassword-Only-For-Tests"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created = client.post("/api/customers", json={"name": "Khách thử"}, headers=headers)
        assert created.status_code == 201
        customer_id = created.json()["id"]
        assert client.get("/api/customers", headers=headers).json()[0]["name"] == "Khách thử"
        assert client.get("/api/dashboard", headers=headers).json()["customers"] == 1
        changed = client.put(
            f"/api/customers/{customer_id}", json={"name": "Khách đã sửa"}, headers=headers
        )
        assert changed.status_code == 200
        assert changed.json()["name"] == "Khách đã sửa"
        assert client.delete(f"/api/customers/{customer_id}", headers=headers).status_code == 204


def test_three_interaction_examples_for_customer():
    with TestClient(main.app) as client:
        login = client.post(
            "/api/auth/login",
            json={"email": "worker@test.example", "password": "TestEmployee-Password-123"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        customer = client.post(
            "/api/customers", json={"name": "Khách kiểm thử tương tác"}, headers=headers
        )
        assert customer.status_code == 201
        customer_id = customer.json()["id"]
        examples = (
            ("call", "Gọi điện xác nhận nhu cầu"),
            ("email", "Gửi thông tin sản phẩm qua email"),
            ("meeting", "Gặp khách hàng trao đổi kế hoạch"),
        )

        created = []
        for kind, notes in examples:
            response = client.post(
                "/api/interactions",
                json={
                    "customer_id": customer_id,
                    "kind": kind,
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "notes": notes,
                },
                headers=headers,
            )
            assert response.status_code == 201
            created.append(response.json())

        assert {record["kind"] for record in created} == {"call", "email", "meeting"}
        assert all(record["customer_id"] == customer_id for record in created)
        assert {record["notes"] for record in created} == {notes for _, notes in examples}
        assert len(client.get("/api/interactions", headers=headers).json()) == 3


def test_employee_scope_and_lead_conversion():
    with TestClient(main.app) as client:
        employee_login = client.post(
            "/api/auth/login",
            json={"email": "worker@test.example", "password": "TestEmployee-Password-123"},
        )
        employee_headers = {"Authorization": f"Bearer {employee_login.json()['access_token']}"}
        assert client.get("/api/users", headers=employee_headers).status_code == 403
        assert client.get("/api/audit-logs", headers=employee_headers).status_code == 403
        assert client.post(
            "/api/users",
            json={
                "email": "new@test.example",
                "full_name": "New User",
                "password": "StrongPassword-123",
                "role": "admin",
            },
            headers=employee_headers,
        ).status_code == 403
        assert client.patch(
            "/api/users/1/active?is_active=false", headers=employee_headers
        ).status_code == 403
        assert client.patch("/api/auth/me", json={"role": "admin"}, headers=employee_headers).status_code == 405
        lead = client.post(
            "/api/leads", json={"name": "Lead thử"}, headers=employee_headers
        )
        assert lead.status_code == 201
        converted = client.post(
            f"/api/leads/{lead.json()['id']}/convert", headers=employee_headers
        )
        assert converted.status_code == 201
        assert converted.json()["status"] == "converted"
        assert client.get("/api/leads", headers=employee_headers).json()[0]["status"] == "converted"
        customer_id = converted.json()["customer_id"]
        client.post(
            "/api/tasks",
            json={"title": "Việc quá hạn", "customer_id": customer_id, "due_date": "2000-01-01"},
            headers=employee_headers,
        )
        now = datetime.now(timezone.utc)
        appointment = client.post(
            "/api/appointments",
            json={
                "title": "Lịch cần nhắc",
                "customer_id": customer_id,
                "starts_at": (now + timedelta(hours=1)).isoformat(),
                "reminder_at": (now - timedelta(minutes=1)).isoformat(),
            },
            headers=employee_headers,
        )
        assert appointment.status_code == 201
        due = client.get("/api/reminders", headers=employee_headers).json()
        assert due["tasks"][0]["title"] == "Việc quá hạn"
        assert due["appointments"][0]["title"] == "Lịch cần nhắc"

        admin_login = client.post(
            "/api/auth/login",
            json={"email": "admin@test.example", "password": "TestPassword-Only-For-Tests"},
        )
        admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
        other_customer = client.post(
            "/api/customers", json={"name": "Riêng của admin"}, headers=admin_headers
        )
        assert other_customer.status_code == 201
        assert all(
            record["id"] != other_customer.json()["id"]
            for record in client.get("/api/customers", headers=employee_headers).json()
        )
        assert any(
            record["id"] == other_customer.json()["id"]
            for record in client.get("/api/customers", headers=admin_headers).json()
        )
        assert client.put(
            f"/api/customers/{other_customer.json()['id']}",
            json={"name": "Sửa trái phép"},
            headers=employee_headers,
        ).status_code == 404
        assert client.delete(
            f"/api/customers/{other_customer.json()['id']}", headers=employee_headers
        ).status_code == 403
        assert client.delete(f"/api/leads/{lead.json()['id']}", headers=employee_headers).status_code == 403


def test_admin_can_manage_accounts_and_delete_crm_records():
    with TestClient(main.app) as client:
        login = client.post(
            "/api/auth/login",
            json={"email": "admin@test.example", "password": "TestPassword-Only-For-Tests"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert client.get("/api/users", headers=headers).status_code == 200
        created = client.post(
            "/api/users",
            json={
                "email": "managed@test.example",
                "full_name": "Managed User",
                "password": "StrongPassword-123",
                "role": "employee",
            },
            headers=headers,
        )
        assert created.status_code == 201
        assert client.post(
            "/api/users",
            json={
                "email": "managed@test.example",
                "full_name": "Managed User",
                "password": "StrongPassword-123",
                "role": "employee",
            },
            headers=headers,
        ).status_code == 409
        user_id = created.json()["id"]
        assert client.patch(
            f"/api/users/{user_id}/active?is_active=false", headers=headers
        ).json()["is_active"] is False
        assert client.patch(
            f"/api/users/{user_id}/active?is_active=true", headers=headers
        ).json()["is_active"] is True
        customer = client.post("/api/customers", json={"name": "Delete me"}, headers=headers)
        assert customer.status_code == 201
        assert client.delete(
            f"/api/customers/{customer.json()['id']}", headers=headers
        ).status_code == 204


def test_public_registration_validates_duplicates_and_password_strength():
    with TestClient(main.app) as client:
        payload = {
            "email": "new-user@test.example",
            "full_name": "New User",
            "password": "StrongPassword-123",
        }
        created = client.post("/api/auth/register", json=payload)
        assert created.status_code == 201
        assert created.json()["role"] == "employee"
        assert client.post("/api/auth/register", json=payload).status_code == 409
        assert client.post(
            "/api/auth/register", json={**payload, "email": "invalid-email"}
        ).status_code == 422
        assert client.post(
            "/api/auth/register", json={**payload, "email": "weak@test.example", "password": "weakpassword"}
        ).status_code == 422
        assert client.post(
            "/api/auth/register", json={**payload, "email": "missing-name@test.example", "full_name": ""}
        ).status_code == 422


def test_password_reset_token_is_hashed_expiring_and_single_use(monkeypatch):
    settings = main.get_settings().model_copy(update={
        "SMTP_HOST": "mailpit",
        "SMTP_FROM_EMAIL": "crm@test.example",
        "PUBLIC_APP_URL": "http://crm.test",
        "PASSWORD_RESET_TTL_MINUTES": 15,
    })
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    sent_messages = []
    monkeypatch.setattr(
        main,
        "send_password_reset_email",
        lambda current_settings, recipient, reset_url: sent_messages.append(
            (recipient, reset_url, current_settings.PASSWORD_RESET_TTL_MINUTES)
        ),
    )

    with TestClient(main.app) as client:
        unknown = client.post(
            "/api/auth/forgot-password", json={"email": "missing@test.example"}
        )
        valid = client.post(
            "/api/auth/forgot-password", json={"email": "worker@test.example"}
        )
        assert unknown.status_code == valid.status_code == 202
        assert unknown.json() == valid.json()
        assert len(sent_messages) == 1
        recipient, reset_url, ttl_minutes = sent_messages[0]
        raw_token = parse_qs(urlparse(reset_url).fragment)["reset_token"][0]
        assert recipient == "worker@test.example"
        assert ttl_minutes == 15
        with TestingSession() as session:
            stored = session.query(main.PasswordResetToken).one()
            assert stored.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()
            assert stored.token_hash != raw_token
            assert stored.expires_at > datetime.now(timezone.utc).replace(tzinfo=None)

        invalid = client.post(
            "/api/auth/reset-password",
            json={"token": "x" * 43, "password": "NewStrongPassword-456"},
        )
        assert invalid.status_code == 400

        changed = client.post(
            "/api/auth/reset-password",
            json={"token": raw_token, "password": "NewStrongPassword-456"},
        )
        assert changed.status_code == 200
        assert client.post(
            "/api/auth/login",
            json={"email": "worker@test.example", "password": "TestEmployee-Password-123"},
        ).status_code == 401
        assert client.post(
            "/api/auth/login",
            json={"email": "worker@test.example", "password": "NewStrongPassword-456"},
        ).status_code == 200
        reused = client.post(
            "/api/auth/reset-password",
            json={"token": raw_token, "password": "AnotherStrongPassword-789"},
        )
        assert reused.status_code == 400


def test_expired_password_reset_token_is_rejected():
    expired_value = "expired-token-value-that-is-long-enough-12345"
    with TestingSession() as session:
        worker = session.query(main.User).filter_by(email="worker@test.example").one()
        session.add(
            main.PasswordResetToken(
                user_id=worker.id,
                token_hash=hashlib.sha256(expired_value.encode()).hexdigest(),
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1),
            )
        )
        session.commit()

    with TestClient(main.app) as client:
        response = client.post(
            "/api/auth/reset-password",
            json={"token": expired_value, "password": "NewStrongPassword-456"},
        )
        assert response.status_code == 400


def test_password_reset_requires_smtp_configuration(monkeypatch):
    settings = main.get_settings().model_copy(update={"SMTP_HOST": None, "SMTP_FROM_EMAIL": None})
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/auth/forgot-password", json={"email": "worker@test.example"}
        )
        assert response.status_code == 503
        assert "chưa được cấu hình" in response.json()["detail"]


def test_smtp_delivery_failure_does_not_reveal_account_existence(monkeypatch):
    settings = main.get_settings().model_copy(update={
        "SMTP_HOST": "mailpit",
        "SMTP_FROM_EMAIL": "crm@test.example",
    })
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(
        main, "send_password_reset_email", lambda *_: (_ for _ in ()).throw(OSError("SMTP unavailable"))
    )
    with TestClient(main.app) as client:
        known = client.post(
            "/api/auth/forgot-password", json={"email": "worker@test.example"}
        )
        missing = client.post(
            "/api/auth/forgot-password", json={"email": "missing@test.example"}
        )
        assert known.status_code == missing.status_code == 202
        assert known.json() == missing.json()
        with TestingSession() as session:
            reset_token = session.query(main.PasswordResetToken).one()
            assert reset_token.used_at is not None


def test_password_reset_requests_are_rate_limited(monkeypatch):
    settings = main.get_settings().model_copy(update={
        "SMTP_HOST": "mailpit",
        "SMTP_FROM_EMAIL": "crm@test.example",
    })
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "send_password_reset_email", lambda *_: None)
    with TestClient(main.app) as client:
        statuses = [
            client.post(
                "/api/auth/forgot-password", json={"email": "worker@test.example"}
            ).status_code
            for _ in range(4)
        ]
        assert statuses == [202, 202, 202, 429]
