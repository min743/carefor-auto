"""
Windows 자격증명 관리자(DPAPI) 기반 비밀 저장.
keyring 라이브러리가 Windows에서는 자동으로 Credential Manager 사용.
"""
from __future__ import annotations

import os

import keyring

SERVICE_NAME = "carefor-auto"

KEY_SLACK_WEBHOOK   = "slack_webhook_url"
KEY_SLACK_BOT_TOKEN = "slack_bot_token"
KEY_PORTAL_ID       = "portal_id"
KEY_PORTAL_PASSWORD = "portal_password"
KEY_SHEET_WEBHOOK   = "sheet_webhook_url"
KEY_ERP_ID          = "erp_id"
KEY_ERP_PASSWORD    = "erp_password"


def get(key: str) -> str | None:
    return keyring.get_password(SERVICE_NAME, key)


def set_(key: str, value: str) -> None:
    keyring.set_password(SERVICE_NAME, key, value)


def delete(key: str) -> None:
    try:
        keyring.delete_password(SERVICE_NAME, key)
    except keyring.errors.PasswordDeleteError:
        pass


def get_slack_webhook() -> str | None:
    return get(KEY_SLACK_WEBHOOK)


def set_slack_webhook(url: str) -> None:
    set_(KEY_SLACK_WEBHOOK, url)


def get_slack_bot_token() -> str | None:
    return get(KEY_SLACK_BOT_TOKEN)


def set_slack_bot_token(token: str) -> None:
    set_(KEY_SLACK_BOT_TOKEN, token)


def get_portal_credentials() -> tuple[str, str] | None:
    """케어포 자동로그인 portal HTTP Basic 인증 정보."""
    pid = get(KEY_PORTAL_ID)
    pw = get(KEY_PORTAL_PASSWORD)
    if pid and pw:
        return pid, pw
    return None


def set_portal_credentials(portal_id: str, portal_password: str) -> None:
    set_(KEY_PORTAL_ID, portal_id)
    set_(KEY_PORTAL_PASSWORD, portal_password)


def get_erp_credentials() -> tuple[str, str] | None:
    """케어링 ERP(erp-api.caring.co.kr) 로그인 정보 — 롱텀 자동로그인용.

    우선순위: 환경변수(CARING_ERP_ID/CARING_ERP_PW) → keyring.
    CI에서는 환경변수만 있으면 되고, 로컬에서는 자격증명 관리자를 쓴다.
    """
    eid = os.environ.get("CARING_ERP_ID") or get(KEY_ERP_ID)
    epw = os.environ.get("CARING_ERP_PW") or get(KEY_ERP_PASSWORD)
    if eid and epw:
        return eid, epw
    return None


def set_erp_credentials(erp_id: str, erp_password: str) -> None:
    set_(KEY_ERP_ID, erp_id)
    set_(KEY_ERP_PASSWORD, erp_password)


def get_sheet_webhook() -> str | None:
    return get(KEY_SHEET_WEBHOOK)


def set_sheet_webhook(url: str) -> None:
    set_(KEY_SHEET_WEBHOOK, url)


# ---- 지점점검 전용 webhook (출석보고와 분리) ----
KEY_AUDIT_WEBHOOK = "audit_webhook_url"


def get_audit_webhook() -> str | None:
    return get(KEY_AUDIT_WEBHOOK)


def set_audit_webhook(url: str) -> None:
    set_(KEY_AUDIT_WEBHOOK, url)


# ---- CI 아티팩트 복호화 열쇠 (audit.sync_from_ci 전용) ----
# GitHub Secrets 의 AUDIT_ARTIFACT_KEY 와 '같은 값'을 로컬 keyring 에도 둔다.
# CI 는 지점 결과(수급자 실명 포함)를 이 열쇠로 대칭 암호화해 아티팩트로 올리고,
# merge job 이 받아 푼다. sync_from_ci 는 로컬에서 같은 일을 해 audit_results/ 를
# 되살린다(2시간 재스캔 없이). 열쇠가 없으면 아티팩트는 열리지 않는다.
KEY_AUDIT_ARTIFACT = "audit_artifact_key"


def get_audit_artifact_key() -> str | None:
    return get(KEY_AUDIT_ARTIFACT)


def set_audit_artifact_key(key: str) -> None:
    set_(KEY_AUDIT_ARTIFACT, key)
