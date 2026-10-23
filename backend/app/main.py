from contextlib import asynccontextmanager
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import hashlib
import logging
import secrets
import smtplib
import ssl
from threading import Lock
import time
from typing import Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import Base, SessionLocal, engine, get_db
from app.models import (
    Appointment,
    AuditLog,
    CRMTask,
    Customer,
    Interaction,
    Lead,
    PasswordResetToken,
    User,
)
from app.schemas import (
    AppointmentInput,
    CustomerInput,
    ForgotPasswordInput,
    InteractionInput,
    LeadInput,
    LoginInput,
    LoginOutput,
    ResetPasswordInput,
    TaskInput,
    UserCreate,
    UserRegister,
)
from app.security import (
    create_access_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    settings = get_settings()
    with SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)) is None:
            db.add(
                User(
                    email=settings.ADMIN_EMAIL.lower(),
                    full_name="Quản trị viên",
                    password_hash=hash_password(settings.ADMIN_PASSWORD),
                    role="admin",
                )
            )
            db.commit()
    yield


app = FastAPI(
    title="CRM Chăm sóc khách hàng API",
    version="1.0.0",
    docs_url=None,
    openapi_url="/api/openapi.json",
    redoc_url=None,
    lifespan=lifespan,
)
origins = [item.strip() for item in get_settings().CORS_ORIGINS.split(",") if item.strip()]
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

ENTITY_ROUTES: dict[str, tuple[type, type]] = {
    "customers": (Customer, CustomerInput),
    "leads": (Lead, LeadInput),
    "interactions": (Interaction, InteractionInput),
    "tasks": (CRMTask, TaskInput),
    "appointments": (Appointment, AppointmentInput),
}


def write_audit(db: Session, user: User, action: str, entity: str, entity_id: int | None) -> None:
    db.add(AuditLog(user_id=user.id, action=action, entity=entity, entity_id=entity_id))


def save_changes(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Dữ liệu liên quan không hợp lệ hoặc đang được sử dụng",
        ) from exc


def flush_changes(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Dữ liệu liên quan không hợp lệ hoặc đã tồn tại",
        ) from exc


def check_customer_access(db: Session, user: User, values: dict[str, Any]) -> None:
    customer_id = values.get("customer_id") or values.get("converted_customer_id")
    if customer_id is None:
        return
    query = select(Customer.id).where(Customer.id == customer_id)
    if user.role != "admin":
        query = query.where(Customer.owner_id == user.id)
    if db.scalar(query) is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy khách hàng được liên kết")


def owned_query(model: type, user: User):
    query = select(model)
    if user.role != "admin":
        query = query.where(model.owner_id == user.id)
    return query


class PasswordResetRateLimiter:
    def __init__(self) -> None:
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, client_ip: str, email: str) -> bool:
        now = time.monotonic()
        cutoff = now - 900
        email_key = hashlib.sha256(email.lower().encode()).hexdigest()
        limits = ((f"ip:{client_ip}", 20), (f"email:{email_key}", 3))
        with self._lock:
            for key, attempts in list(self._attempts.items()):
                while attempts and attempts[0] <= cutoff:
                    attempts.popleft()
                if not attempts:
                    self._attempts.pop(key, None)
            if any(len(self._attempts[key]) >= limit for key, limit in limits):
                return False
            for key, _ in limits:
                self._attempts[key].append(now)
            return True

    def clear(self) -> None:
        with self._lock:
            self._attempts.clear()


password_reset_rate_limiter = PasswordResetRateLimiter()
registration_rate_limiter = PasswordResetRateLimiter()
PASSWORD_RESET_REQUEST_MESSAGE = (
    "Yêu cầu đã được tiếp nhận. Nếu email đã đăng ký, hãy kiểm tra hộp thư; "
    "nếu không nhận được thư, vui lòng liên hệ quản trị viên."
)


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def send_password_reset_email(settings: Any, recipient: str, reset_url: str) -> None:
    message = EmailMessage()
    message["Subject"] = "Đặt lại mật khẩu CRM"
    message["From"] = settings.SMTP_FROM_EMAIL
    message["To"] = recipient
    message.set_content(
        "Bạn đã yêu cầu đặt lại mật khẩu CRM. Mở liên kết sau trong vòng "
        f"{settings.PASSWORD_RESET_TTL_MINUTES} phút:\n\n{reset_url}\n\n"
        "Nếu bạn không yêu cầu thao tác này, hãy bỏ qua email."
    )
    if settings.SMTP_USE_SSL:
        with smtplib.SMTP_SSL(
            settings.SMTP_HOST, settings.SMTP_PORT, timeout=10,
            context=ssl.create_default_context(),
        ) as smtp:
            if settings.SMTP_USERNAME:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD or "")
            smtp.send_message(message)
        return

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
        if settings.SMTP_STARTTLS:
            smtp.starttls(context=ssl.create_default_context())
        if settings.SMTP_USERNAME:
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD or "")
        smtp.send_message(message)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/login", response_model=LoginOutput)
def login(payload: LoginInput, db: Session = Depends(get_db)) -> dict[str, Any]:
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email hoặc mật khẩu không đúng")
    write_audit(db, user, "login", "user", user.id)
    save_changes(db)
    return {
        "access_token": create_access_token(user),
        "user": {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role},
    }


@app.post("/api/auth/register", status_code=201)
def register_account(
    payload: UserRegister,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    client_ip = request.client.host if request.client else "unknown"
    if not registration_rate_limiter.allow(client_ip, str(payload.email)):
        raise HTTPException(status_code=429, detail="Bạn đã gửi quá nhiều yêu cầu tạo tài khoản. Vui lòng thử lại sau.")

    email = str(payload.email).lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=409, detail="Email đã được sử dụng")
    user = User(
        email=email,
        full_name=payload.full_name.strip(),
        password_hash=hash_password(payload.password),
        role="employee",
    )
    db.add(user)
    flush_changes(db)
    write_audit(db, user, "register", "user", user.id)
    save_changes(db)
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role}


@app.get("/api/auth/me")
def current_user(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role}


@app.post("/api/auth/forgot-password", status_code=202)
def forgot_password(
    payload: ForgotPasswordInput,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    client_ip = request.client.host if request.client else "unknown"
    if not password_reset_rate_limiter.allow(client_ip, str(payload.email)):
        raise HTTPException(status_code=429, detail="Bạn đã gửi quá nhiều yêu cầu. Vui lòng thử lại sau.")

    settings = get_settings()
    if not settings.SMTP_HOST or not settings.SMTP_FROM_EMAIL:
        raise HTTPException(
            status_code=503,
            detail="Email chưa được cấu hình. Vui lòng liên hệ quản trị viên.",
        )

    user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if user is not None and user.is_active:
        now = utc_now_naive()
        for previous in db.scalars(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used_at.is_(None),
            )
        ).all():
            previous.used_at = now

        raw_token = secrets.token_urlsafe(32)
        reset_token = PasswordResetToken(
            user_id=user.id,
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            expires_at=now + timedelta(minutes=settings.PASSWORD_RESET_TTL_MINUTES),
        )
        db.add(reset_token)
        save_changes(db)

        reset_url = f"{settings.PUBLIC_APP_URL.rstrip('/')}/reset-password#reset_token={quote(raw_token)}"
        try:
            send_password_reset_email(settings, user.email, reset_url)
        except (OSError, smtplib.SMTPException, TimeoutError) as exc:
            reset_token.used_at = utc_now_naive()
            save_changes(db)
            logging.getLogger(__name__).warning("Password reset email delivery failed")

    return {"message": PASSWORD_RESET_REQUEST_MESSAGE}


@app.post("/api/auth/reset-password")
def reset_password(
    payload: ResetPasswordInput,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    token_hash = hashlib.sha256(payload.token.encode()).hexdigest()
    reset_token = db.scalar(
        select(PasswordResetToken)
        .where(PasswordResetToken.token_hash == token_hash)
        .with_for_update()
    )
    now = utc_now_naive()
    if reset_token is None or reset_token.used_at is not None or reset_token.expires_at <= now:
        raise HTTPException(status_code=400, detail="Liên kết đặt lại mật khẩu không hợp lệ hoặc đã hết hạn.")

    user = db.get(User, reset_token.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail="Liên kết đặt lại mật khẩu không hợp lệ hoặc đã hết hạn.")

    user.password_hash = hash_password(payload.password)
    reset_token.used_at = now
    for previous in db.scalars(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.id != reset_token.id,
            PasswordResetToken.used_at.is_(None),
        )
    ).all():
        previous.used_at = now
    write_audit(db, user, "password_reset", "user", user.id)
    save_changes(db)
    return {"message": "Đặt lại mật khẩu thành công. Bạn có thể đăng nhập bằng mật khẩu mới."}


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, model in (
        ("customers", Customer),
        ("leads", Lead),
        ("tasks", CRMTask),
        ("appointments", Appointment),
    ):
        query = select(func.count()).select_from(model)
        if user.role != "admin":
            query = query.where(model.owner_id == user.id)
        result[key] = db.scalar(query) or 0
    result["open_tasks"] = db.scalar(
        select(func.count()).select_from(CRMTask).where(
            CRMTask.status.in_(("open", "in_progress")),
            *([] if user.role == "admin" else [CRMTask.owner_id == user.id]),
        )
    ) or 0
    return result


@app.get("/api/reminders")
def reminders(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, list[dict[str, Any]]]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    task_query = select(CRMTask).where(
        CRMTask.status.in_(("open", "in_progress")),
        CRMTask.due_date <= now.date(),
    ).order_by(CRMTask.due_date).limit(10)
    appointment_query = select(Appointment).where(
        Appointment.status == "scheduled",
        Appointment.reminder_at.is_not(None),
        Appointment.reminder_at <= now,
        Appointment.starts_at >= now,
    ).order_by(Appointment.starts_at).limit(10)
    if user.role != "admin":
        task_query = task_query.where(CRMTask.owner_id == user.id)
        appointment_query = appointment_query.where(Appointment.owner_id == user.id)
    return {
        "tasks": [
            {"id": task.id, "title": task.title, "due_date": task.due_date}
            for task in db.scalars(task_query).all()
        ],
        "appointments": [
            {
                "id": appointment.id,
                "title": appointment.title,
                "starts_at": appointment.starts_at,
                "reminder_at": appointment.reminder_at,
            }
            for appointment in db.scalars(appointment_query).all()
        ],
    }


@app.get("/api/users")
def list_users(
    db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> list[dict[str, Any]]:
    users = db.scalars(select(User).order_by(User.id)).all()
    return [
        {"id": item.id, "email": item.email, "full_name": item.full_name, "role": item.role,
         "is_active": item.is_active}
        for item in users
    ]


@app.post("/api/users", status_code=201)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict[str, Any]:
    email = payload.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=409, detail="Email đã được sử dụng")
    user = User(
        email=email, full_name=payload.full_name, role=payload.role,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    flush_changes(db)
    write_audit(db, admin, "create", "user", user.id)
    save_changes(db)
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role}


@app.patch("/api/users/{user_id}/active")
def set_user_active(
    user_id: int,
    is_active: bool,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict[str, Any]:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")
    if user.id == admin.id and not is_active:
        raise HTTPException(status_code=400, detail="Không thể tự khóa tài khoản đang đăng nhập")
    if user.role == "admin" and not is_active:
        active_admins = db.scalar(
            select(func.count()).select_from(User).where(
                User.role == "admin", User.is_active.is_(True)
            )
        ) or 0
        if active_admins <= 1:
            raise HTTPException(status_code=400, detail="Không thể khóa quản trị viên hoạt động cuối cùng")
    user.is_active = is_active
    write_audit(db, admin, "activate" if is_active else "deactivate", "user", user.id)
    save_changes(db)
    return {"id": user.id, "is_active": user.is_active}


@app.post("/api/leads/{lead_id}/convert", status_code=201)
def convert_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    lead = db.scalar(owned_query(Lead, user).where(Lead.id == lead_id))
    if lead is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy khách hàng tiềm năng")
    if lead.status == "converted":
        raise HTTPException(status_code=409, detail="Khách hàng tiềm năng đã được chuyển đổi")
    customer = Customer(
        owner_id=lead.owner_id,
        name=lead.name,
        email=lead.email,
        phone=lead.phone,
        notes=lead.notes,
    )
    db.add(customer)
    flush_changes(db)
    lead.status = "converted"
    lead.converted_customer_id = customer.id
    write_audit(db, user, "convert", "lead", lead.id)
    save_changes(db)
    return {"lead_id": lead.id, "customer_id": customer.id, "status": lead.status}


def register_entity_routes(path: str, model: type, schema: type) -> None:
    @app.get(f"/api/{path}", name=f"list_{path}")
    def list_records(
        q: str | None = Query(default=None, max_length=100),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user),
    ) -> list[dict[str, Any]]:
        query = owned_query(model, user).order_by(model.id.desc()).limit(500)
        if q:
            search_columns = [
                getattr(model, column)
                for column in ("name", "email", "title")
                if hasattr(model, column)
            ]
            if search_columns:
                query = query.where(
                    or_(*(column.ilike(f"%{q}%") for column in search_columns))
                )
        return [
            {column.name: getattr(record, column.name) for column in model.__table__.columns}
            for record in db.scalars(query).all()
        ]

    @app.post(f"/api/{path}", status_code=201, name=f"create_{path}")
    def create_record(
        payload: schema,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        values = payload.model_dump(exclude_unset=True)
        check_customer_access(db, user, values)
        record = model(**values, owner_id=user.id)
        db.add(record)
        flush_changes(db)
        write_audit(db, user, "create", path, record.id)
        save_changes(db)
        db.refresh(record)
        return {column.name: getattr(record, column.name) for column in model.__table__.columns}

    @app.put(f"/api/{path}/{{record_id}}", name=f"update_{path}")
    def update_record(
        record_id: int,
        payload: schema,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        record = db.scalar(owned_query(model, user).where(model.id == record_id))
        if record is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy dữ liệu")
        values = payload.model_dump(exclude_unset=True)
        check_customer_access(db, user, values)
        for key, value in values.items():
            setattr(record, key, value)
        write_audit(db, user, "update", path, record_id)
        save_changes(db)
        db.refresh(record)
        return {column.name: getattr(record, column.name) for column in model.__table__.columns}

    @app.delete(f"/api/{path}/{{record_id}}", status_code=204, name=f"delete_{path}")
    def delete_record(
        record_id: int,
        db: Session = Depends(get_db),
        user: User = Depends(require_admin),
    ) -> Response:
        record = db.scalar(owned_query(model, user).where(model.id == record_id))
        if record is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy dữ liệu")
        write_audit(db, user, "delete", path, record_id)
        db.delete(record)
        save_changes(db)
        return Response(status_code=204)


for route_name, (route_model, route_schema) in ENTITY_ROUTES.items():
    register_entity_routes(route_name, route_model, route_schema)


@app.get("/api/audit-logs")
def audit_logs(
    db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> list[dict[str, Any]]:
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(200)).all()
    return [
        {"id": row.id, "user_id": row.user_id, "action": row.action, "entity": row.entity,
         "entity_id": row.entity_id, "created_at": row.created_at}
        for row in rows
    ]
