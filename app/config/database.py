"""Database URL helpers and shared runtime connection settings.

No database connection is opened at import time.
"""

from __future__ import annotations

from app.config.settings import Settings

# Every live psycopg connection the app opens uses these. ``connect_timeout``
# keeps a dead/unreachable server from freezing the UI for the OS default (~20s
# on Windows) — instead the attempt fails in 5s with a clear message.
# ``application_name`` tags CRM sessions so they are identifiable on the server
# in ``pg_stat_activity``.
CONNECT_TIMEOUT_SECONDS = 5
APPLICATION_NAME = "CRM"


def build_database_url(settings: Settings) -> str:
    return (
        f"postgresql+psycopg://{settings.db_user}:{settings.db_password}"
        f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
    )


def runtime_connect_kwargs() -> dict[str, object]:
    """Keyword args merged into every runtime ``psycopg.connect`` call.

    Passed as libpq parameters (not baked into the URL), so the saved
    connection-profile format is unchanged.
    """
    return {
        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
        "application_name": APPLICATION_NAME,
    }


# Markers (lowercased) that identify a *client-side* failure to reach the server
# — the connection never got far enough to talk to PostgreSQL.
_UNREACHABLE_MARKERS = (
    "timeout expired",
    "connection timeout",
    "timed out",
    "could not connect",
    "connection refused",
    "could not translate host name",
    "name or service not known",
    "no route to host",
    "network is unreachable",
    "host is unreachable",
    "could not receive data from server",
    "server closed the connection unexpectedly",
)


def describe_db_error(
    exc: BaseException,
    *,
    host: str = "",
    port: int | str = "",
    db_name: str = "",
    password: str = "",
    fallback_prefix: str = "تعذّر الاتصال بقاعدة البيانات",
) -> str:
    """Turn a raw psycopg/DB exception into a clear, actionable Arabic message.

    Distinguishes the four cases the user must act on differently:

    * server unreachable  -> check IP / firewall / service / port
    * wrong user/password -> fix the credentials
    * wrong database name -> fix the database name
    * anything else (e.g. the connection worked but a later step failed) -> the
      exact error is shown, prefixed with ``fallback_prefix`` so it is never
      hidden.

    The DB password (if provided) is scrubbed from the returned text.
    """
    sqlstate = getattr(exc, "sqlstate", None)
    text = str(exc)
    if password:
        text = text.replace(password, "******")
    low = text.lower()

    # 1) Authentication failure — wrong username or password.
    #    28P01 invalid_password, 28000 invalid_authorization_specification.
    if sqlstate in {"28P01", "28000"} or "password authentication failed" in low \
            or "authentication failed" in low or "no password supplied" in low:
        return "بيانات دخول قاعدة البيانات غير صحيحة: اسم المستخدم أو كلمة المرور خاطئة."

    # 2) Database does not exist — wrong database name. 3D000 invalid_catalog_name.
    if sqlstate == "3D000" or ("database" in low and "does not exist" in low):
        name = str(db_name) or "؟"
        return (
            f"قاعدة البيانات «{name}» غير موجودة على الخادم.\n"
            "تأكد من اسم قاعدة البيانات في إعدادات الاتصال."
        )

    # 3) Server unreachable — never reached PostgreSQL (client-side network error).
    if sqlstate is None and any(m in low for m in _UNREACHABLE_MARKERS):
        where = f" ({host}:{port})" if host else ""
        shown_port = port or 5432
        return (
            f"تعذّر الوصول إلى خادم قاعدة بيانات PostgreSQL{where}.\n"
            "تحقّق من الآتي:\n"
            f"• عنوان IP الخاص بالخادم في إعدادات الاتصال (الحالي: {host or 'غير محدد'}).\n"
            "• أن خدمة PostgreSQL قيد التشغيل على الخادم.\n"
            "• أن جدار الحماية (Firewall) يسمح بالاتصال.\n"
            f"• أن المنفذ {shown_port} مفتوح وأن جهاز الخادم يعمل ومتصل بالشبكة."
        )

    # 4) Anything else — do NOT hide it; show the exact error.
    return f"{fallback_prefix}:\n{text}"
