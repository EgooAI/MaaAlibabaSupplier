from __future__ import annotations

import json
import re
from base64 import b64encode
from typing import Any

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field
from typing import Literal
from sqlmodel import Session, select

from backend.app.api.envelope import AppError, api_error, ok
from backend.app.shared.agent.inputs import build_analysis_input
from backend.app.shared.agent.runner import run_chat_tool_agent
from backend.app.shared.agent.suggestions import generate_reply_suggestions
from backend.app.shared.agent.system_agents import CHAT_CUSTOMER_STAGE_AGENT_APID
from backend.app.shared.backend.maafw_runner import chat_input, chat_send, goto_contact
from backend.app.shared.chat_format import (
    business_card_from_message,
    conversation_transcript,
)
from backend.app.shared.crm import (
    REASON_DATA_DIR_NOT_CONFIGURED,
    REASON_SELF_IDENTITY_NOT_SELECTED,
    get_conversation_detail as crm_get_conversation_detail,
    get_user_info as crm_get_user_info,
    list_conversations as crm_list_conversations,
    refresh_chat_data,
)
from backend.app.shared.crm.identities import PLATFORM_PID
from backend.app.shared.crm.sdk import AccountMapping
from backend.app.shared.crm.sync import CRMAdapter
from backend.app.shared.crm.views import (
    CrmConversation,
    CrmResolver,
    format_created_at,
    message_display_text,
    normalize_message_type,
    resolve_role,
)
from backend.app.shared.export_zip import build_export_zip
from backend.app.task_queue import TaskStatus, get_task_queue

router = APIRouter()

_STAGE_TASK = "分析客户当前所处成交阶段，并给出下一步推进建议。"
_TONE_CYCLE = ("formal", "friendly", "urgent")
_SCORE_RE = re.compile(r"(\d+(?:\.\d+)?)")


class GotoContactInput(BaseModel):
    login_id: str = Field(min_length=1)


class SendMessageInput(BaseModel):
    content: str = Field(min_length=1)
    action: Literal["send", "test"] = "send"


class ExportConversationsInput(BaseModel):
    conversationIds: list[int] = Field(min_length=1, max_length=200)


def _snap_to_dict(s) -> dict:
    return {
        "task_id": s.task_id,
        "description": s.description,
        "status": str(s.status),
        "message": s.message,
        "result": list(s.result) if s.result else None,
        "created_at": s.created_at,
        "started_at": s.started_at,
        "completed_at": s.completed_at,
    }


def _ready() -> str:
    state = refresh_chat_data(wait=False)
    if not state.ready or not state.self_ali_id:
        if state.reason == REASON_DATA_DIR_NOT_CONFIGURED:
            raise AppError("阿里客户端数据目录尚未配置，请前往“设置”页配置后重试", status_code=503)
        if state.reason == REASON_SELF_IDENTITY_NOT_SELECTED:
            raise AppError("尚未选择阿里账号身份，请前往“设置”页选择后重试", status_code=503)
        raise AppError(f"聊天数据未就绪({state.reason or 'unknown'})，请确认阿里客户端已启动且 AES Key 有效", status_code=503)
    return state.self_ali_id


def _dump(model: Any) -> dict:
    if model is None:
        return {}
    data = model.model_dump(mode="json")
    return data if isinstance(data, dict) else {}


def _build_aggregate(adapter: CRMAdapter, self_ali_id: str, conv: CrmConversation) -> dict:
    resolver = CrmResolver(self_ali_id)
    participants = list(conv.participants or [])
    # Written by sync as [self_aid, contact_aid]; role itself is resolved
    # authoritatively from sender_id strings (see resolve_role).
    self_aid = participants[0] if participants else 0
    contact_aid = participants[1] if len(participants) > 1 else self_aid

    message_dtos: list[dict] = []
    cards: list[dict] = []
    seen_cards: set[str] = set()
    for message in conv.messages:
        role = resolve_role(message, resolver)
        content = message_display_text(message)
        card = business_card_from_message(message)
        if card is not None and card["id"] not in seen_cards:
            seen_cards.add(card["id"])
            cards.append(card)
        sender_aid = self_aid if role in ("seller", "system") else contact_aid
        if role == "system" and resolver.is_self(message.sender_id):
            sender_aid = self_aid
        created = format_created_at(message.created_at) or None
        message_dtos.append({
            "message": {
                "external_mid": f"{message.table_name}:{message.mid}",
                "sid": conv.sid,
                "sender": sender_aid,
                "read": None,
                "content": {"card_id": card["id"], "label": content} if card is not None else content,
                "type": normalize_message_type(message),
            },
            "created_at": created,
            "role": role,
        })

    accounts = [_dump(adapter.accounts.get_account(aid)) for aid in participants]
    accounts = [account for account in accounts if account]
    customers: list[dict] = []
    for account in accounts:
        customer = adapter.customers.get_customer(account.get("cid", 0))
        if customer is not None:
            customers.append(_dump(customer))
    platform = adapter.platforms.get_platform(PLATFORM_PID)
    platforms = [_dump(platform)] if platform is not None else [{"pid": PLATFORM_PID, "name": "Alibaba"}]
    mappings: list[dict] = []
    if participants:
        with Session(adapter.engine) as session:
            rows = session.exec(
                select(AccountMapping).where(AccountMapping.aid.in_(participants))
            ).all()
            mappings = [_dump(row) for row in rows]

    last = message_dtos[-1] if message_dtos else None
    dialogue_count = sum(1 for message in conv.messages if not message.is_system)
    return {
        "sid": conv.sid,
        "name": conv.key,
        "participants": participants,
        "messages": message_dtos,
        "accounts": accounts,
        "customers": customers,
        "platforms": platforms,
        "account_mappings": mappings,
        "customer_view": _build_customer_view(conv.contact_ali_id, accounts, customers),
        "latest": {
            "content": (last["message"]["content"] if isinstance(last["message"]["content"], str) else last["message"]["content"].get("label")) if last else None,
            "updated_at": last["created_at"] if last else None,
        },
        "unread_count": 0,
        "status": "following",
        "priority": "medium",
        "dialogue_count": dialogue_count,
        "business_cards": cards,
    }


def _build_customer_view(contact_ali_id: str, accounts: list[dict], customers: list[dict]) -> dict | None:
    user = crm_get_user_info(contact_ali_id)
    account = next((a for a in accounts if not (a.get("extra") or {}).get("is_self")), None)
    if account is None and accounts:
        account = accounts[0]
    customer = None
    if account is not None:
        customer = next((c for c in customers if c.get("cid") == account.get("cid")), None)
    if customer is None and customers:
        customer = customers[0]
    if user is None and account is None:
        return None
    extra = (account.get("extra") or {}) if account else {}
    name = ""
    first_name = ""
    last_name = ""
    company = ""
    country = ""
    email = ""
    mobile = ""
    phone = ""
    d90: dict = {}
    quality_tag = ""
    growth_level: list[str] = []
    industries: list[str] = []
    joining_years = None
    potential_score = None
    recent_contact = None
    email_validated = None
    available = ""
    register_date = None
    if user is not None:
        first_name = user.first_name
        last_name = user.last_name
        name = f"{first_name} {last_name}".strip() or user.company_name or user.login_id or contact_ali_id
        company = user.company_name
        country = user.country_code
        email = user.email
        mobile = user.mobile_number
        phone = user.phone_number
        register_date = user.register_date or None
        quality_tag = user.high_quality_level_tag
        growth_level = [user.growth_level] if user.growth_level else []
        industries = list(user.preferred_industries or [])
        joining_years = user.joining_years or None
        potential_score = user.potential_score or None
        recent_contact = bool(user.recent_contact)
        email_validated = bool(user.email_validated)
        available = "可用" if user.available else "不可用"
        d90 = {
            "product_views": user.product_view_count,
            "valid_inquiries": user.valid_inquiry_count,
            "replied_inquiries": user.replied_inquiry_count,
            "valid_rfqs": user.valid_rfq_count,
            "login_days": user.login_days,
            "spam_inquiries": user.spam_inquiry_count,
            "blacklisted": user.blacklisted_count,
        }
    else:
        name = str(extra.get("account") or account.get("nickname") or contact_ali_id)
    return {
        "id": str((customer or {}).get("cid") or (account or {}).get("cid") or contact_ali_id),
        "ali_id": (user.ali_id if user else None) or str(extra.get("ali_id") or "") or None,
        "login_id": (user.login_id if user else None) or str(extra.get("login_id") or "") or None,
        "encrypt_account_id": (user.encrypt_account_id if user else "") or None,
        "member_id": (user.ali_member_id if user else "") or None,
        "name": name,
        "first_name": first_name or None,
        "last_name": last_name or None,
        "company": company,
        "country": country or str((customer or {}).get("region") or ""),
        "register_date": format_created_at(register_date) if register_date else None,
        "email": email,
        "mobile": mobile or None,
        "phone": phone,
        "stage": "unknown",
        "tags": [tag for tag in [quality_tag, *growth_level] if tag],
        "quality_tag": quality_tag or None,
        "growth_level": growth_level[0] if growth_level else None,
        "industries": industries,
        "availability": available,
        "joining_years": joining_years,
        "potential_score": potential_score,
        "recent_contact": recent_contact,
        "email_validated": email_validated,
        "behavior": [],
        "d90": d90,
    }


def _map_stage(text: str) -> str:
    value = str(text or "")
    if any(keyword in value for keyword in ("成交", "签约", "成单")):
        return "done"
    if any(keyword in value for keyword in ("风险", "流失", "沉睡")):
        return "risk"
    if any(keyword in value for keyword in ("谈判", "议价", "报价", "比价")):
        return "negotiating"
    if any(keyword in value for keyword in ("意向", "兴趣", "考虑")):
        return "interested"
    if any(keyword in value for keyword in ("新", "初步", "线索")):
        return "new"
    return "unknown"


def _map_score(value: Any) -> int:
    match = _SCORE_RE.search(str(value or ""))
    if not match:
        return 0
    try:
        number = float(match.group(1))
    except ValueError:
        return 0
    if number <= 1:
        number *= 100
    return max(0, min(100, int(number)))


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value:
        return [str(value).strip()]
    return []


@router.get("/api/conversations")
def list_conversations() -> dict:
    self_ali_id = _ready()
    adapter = CRMAdapter()
    try:
        convs = crm_list_conversations(self_ali_id)
        return ok([_build_aggregate(adapter, self_ali_id, conv) for conv in convs])
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)


@router.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: int) -> dict:
    self_ali_id = _ready()
    try:
        conv = crm_get_conversation_detail(self_ali_id, conversation_id)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    if conv is None:
        raise AppError("会话不存在", status_code=404)
    try:
        return ok(_build_aggregate(CRMAdapter(), self_ali_id, conv))
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)


@router.post("/api/conversations/{conversation_id}/messages")
def send_message(conversation_id: int, body: SendMessageInput) -> dict:
    content = body.content.strip()
    if not content:
        raise AppError("content is required", status_code=422)
    action = body.action
    self_ali_id = _ready()
    conv = crm_get_conversation_detail(self_ali_id, conversation_id)
    if conv is None:
        raise AppError("会话不存在", status_code=404)
    user = crm_get_user_info(conv.contact_ali_id)
    login_id = (user.login_id if user and user.login_id else conv.contact_ali_id).strip()

    def _run() -> tuple[bool, str]:
        reached, message = goto_contact(login_id)
        if not reached:
            return False, message
        if action == "test":
            return chat_input(content)
        return chat_send(content)

    snap = get_task_queue().enqueue(_run, description=f"send({action}) to {login_id}")
    current = get_task_queue().get(snap.task_id)
    try:
        aggregate = _build_aggregate(CRMAdapter(), self_ali_id, conv)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    queued = current is None or str(current.status) == "pending"
    return ok({
        "message": None,
        "conversation": aggregate,
        "execution": {
            "success": not queued and bool(current and current.result and current.result[0]),
            "message": current.message if current else "任务已提交，等待执行",
            "task_snapshot": _snap_to_dict(current) if current else None,
        },
    })


@router.get("/api/conversations/{conversation_id}/suggestions")
def suggestions(conversation_id: int) -> dict:
    self_ali_id = _ready()
    conv = crm_get_conversation_detail(self_ali_id, conversation_id)
    if conv is None:
        raise AppError("会话不存在", status_code=404)
    try:
        rows = conversation_transcript(conv.messages, CrmResolver(self_ali_id))
        result = generate_reply_suggestions(rows)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    items = [
        {
            "id": f"sug-{index}",
            "title": item.zh[:16] if len(item.zh) > 16 else item.zh,
            "content": item.reply,
            "tone": _TONE_CYCLE[index % len(_TONE_CYCLE)],
            "zh": item.zh,
        }
        for index, item in enumerate(result.items[:3])
    ]
    return ok(items)


@router.get("/api/conversations/{conversation_id}/analysis")
def analysis(conversation_id: int) -> dict:
    self_ali_id = _ready()
    conv = crm_get_conversation_detail(self_ali_id, conversation_id)
    if conv is None:
        raise AppError("会话不存在", status_code=404)
    try:
        rows = conversation_transcript(conv.messages, CrmResolver(self_ali_id))
        raw = run_chat_tool_agent(CHAT_CUSTOMER_STAGE_AGENT_APID, build_analysis_input(task=_STAGE_TASK, conversation=rows))
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("分析结果格式错误")
        raise AppError("分析结果格式错误", status_code=502)
    if not isinstance(payload, dict):
        raise AppError("分析结果格式错误", status_code=502)
    stage = _map_stage(payload.get("stage"))
    evidence = _as_list(payload.get("evidence"))
    concerns = _as_list(payload.get("concerns"))
    next_actions = _as_list(payload.get("next_actions"))
    summary = "；".join(evidence) if evidence else str(payload.get("summary") or payload.get("intent") or "")
    return ok({
        "intent": str(payload.get("intent") or ""),
        "stage": stage,
        "score": _map_score(payload.get("confidence")),
        "risks": concerns,
        "next_actions": next_actions,
        "summary": summary,
        "raw_text": raw,
        "json_payload": payload,
        # 兼容旧前端 camel 键，新代码请用 snake。
        "nextActions": next_actions,
        "rawText": raw,
        "jsonPayload": payload,
    })


@router.post("/api/conversations/export")
def export_conversations(body: ExportConversationsInput) -> dict:
    sids = body.conversationIds
    self_ali_id = _ready()
    convs: list[CrmConversation] = []
    missing: list[int] = []
    for sid in sids:
        try:
            conv = crm_get_conversation_detail(self_ali_id, sid)
        except Exception:
            logger.exception("request failed")
            return api_error("服务器内部错误", status_code=500)
        if conv is None:
            missing.append(sid)
            continue
        convs.append(conv)
    if not convs:
        raise AppError(f"会话不存在: {missing[0] if missing else ''}", status_code=404)
    try:
        payload, archive_name = build_export_zip(convs, CrmResolver(self_ali_id))
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    data: dict = {
        "archive_name": archive_name,
        "file_name": archive_name,
        "content": b64encode(payload).decode("ascii"),
        # 兼容旧前端 camel 键。
        "archiveName": archive_name,
        "fileName": archive_name,
    }
    if missing:
        data["missing"] = missing
    return ok(data)


@router.post("/api/conversations/{conversation_id}/goto-contact")
def goto_contact_api(conversation_id: int, body: GotoContactInput) -> dict:
    login_id = body.login_id.strip()
    snap = get_task_queue().enqueue(
        lambda: goto_contact(login_id), description=f"goto-contact {login_id}"
    )
    current = get_task_queue().get(snap.task_id)
    status = str(current.status) if current else str(TaskStatus.PENDING)
    return ok({"task_snapshot": _snap_to_dict(current) if current else None, "status": status})
