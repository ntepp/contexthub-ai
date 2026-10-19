from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.audit import service as audit
from src.auth.oidc import OIDCIdentity
from src.auth.security import hash_password, validate_password_strength
from src.config import get_settings
from src.db.models import ROLE_ADMIN, ROLE_MEMBER, User, utcnow
from src.utils.logging_config import get_logger

log = get_logger()


def normalise_email(email: str) -> str:
    return email.strip().lower()


def find_by_email(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(User.email == normalise_email(email))).scalar_one_or_none()


def create_local_user(
    db: Session, email: str, password: str, *, full_name: str | None = None, role: str = ROLE_MEMBER
) -> User:
    problem = validate_password_strength(password)
    if problem:
        raise ValueError(problem)
    user = User(
        email=normalise_email(email),
        full_name=full_name,
        password_hash=hash_password(password),
        role=role,
        auth_provider="local",
    )
    db.add(user)
    db.flush()
    return user


def ensure_bootstrap_admin(db: Session) -> None:
    """Create ADMIN_EMAIL/ADMIN_PASSWORD as administrator if no admin exists yet."""
    settings = get_settings()
    has_admin = db.execute(select(func.count()).select_from(User).where(User.role == ROLE_ADMIN)).scalar_one()
    if has_admin:
        return
    if not (settings.admin_email and settings.admin_password):
        log.warning(
            "Aucun administrateur : définissez ADMIN_EMAIL et ADMIN_PASSWORD, "
            "ou lancez `python -m src.cli create-admin`."
        )
        return
    existing = find_by_email(db, settings.admin_email)
    if existing:
        existing.role = ROLE_ADMIN
    else:
        create_local_user(db, settings.admin_email, settings.admin_password, role=ROLE_ADMIN, full_name="Administrateur")
    db.commit()
    audit.record("user.bootstrap_admin", details={"email": normalise_email(settings.admin_email)})
    log.info("Administrateur initial créé : %s", settings.admin_email)


def upsert_oidc_user(db: Session, identity: OIDCIdentity) -> User:
    settings = get_settings()
    user = db.execute(select(User).where(User.oidc_subject == identity.subject)).scalar_one_or_none()
    if user is None:
        user = find_by_email(db, identity.email)
        if user is not None:
            # First SSO login of a pre-provisioned account: bind the subject.
            user.oidc_subject = identity.subject
        else:
            user = User(email=identity.email, oidc_subject=identity.subject, auth_provider="oidc", role=ROLE_MEMBER)
            db.add(user)
    if identity.name and not user.full_name:
        user.full_name = identity.name
    admin_groups = set(settings.split_csv(settings.oidc_admin_groups))
    if admin_groups:
        user.role = ROLE_ADMIN if admin_groups & set(identity.groups) else (
            ROLE_MEMBER if user.auth_provider == "oidc" else user.role
        )
    user.last_login_at = utcnow()
    db.commit()
    return user
