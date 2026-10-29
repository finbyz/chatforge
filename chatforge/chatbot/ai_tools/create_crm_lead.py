# AI Tool: Create CRM Lead
# Module: Chatbot
#
# Turns a chatbot conversation into a real CRM Lead.
#
# `create_chatbot_lead` writes to Chatbot Lead, which is chatforge's own doctype
# and nobody in sales opens. This writes the ERPNext Lead instead, tagged with the
# "Chatbot" Lead Source, so chat enquiries report alongside every other channel
# and pick up whatever Lead workflow the team already runs. Both tools can be
# linked on the same agent — Chatbot Lead stays the chat-side record, Lead is the
# one sales works.
#
# Two things land on the Lead, and the split is deliberate:
#
#   Notes    one short line — the summary the agent passes in, or, when it passes
#            none, a condensed version of what the visitor actually typed. This is
#            what a salesperson reads before picking up the phone.
#   Comment  the whole conversation, both sides, on the Lead's timeline for when
#            they want the detail. A wall of chat in the Notes list is noise; on
#            the timeline it sits beside the rest of the follow-up.
#
# No second AI Agent is involved — the chatbot agent is already holding the whole
# conversation when it calls this, so asking it for one sentence costs nothing,
# and the fallback covers the case where it does not bother.
#
# Link it by creating an AI Tool named exactly "Create CRM Lead" with module
# Chatbot and From Package blank, then picking it in the agent's Tools table.
# AI Tool.get_tool_path() derives the dotted path from the module and the record
# name, so both the record name and this filename have to stay as they are.

import json
import re

import frappe
from langchain.tools import tool

LEAD_SOURCE = "Chatbot"

# A note, not a transcript. Long enough for a real requirement, short enough that
# a salesperson reads it before calling.
MAX_SUMMARY_CHARS = 1000

# Only the opening exchanges matter for the fallback; by message twelve the
# visitor is usually handing over their phone number.
MAX_FALLBACK_MESSAGES = 12

# The transcript Comment is the whole exchange, so it needs a far wider ceiling
# than the summary — but still a ceiling, since a Comment renders inline on the
# Lead and a runaway chat should not bury the rest of the timeline.
MAX_TRANSCRIPT_MESSAGES = 100
MAX_TRANSCRIPT_LINE_CHARS = 2000

EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


# ── conversation context ──────────────────────────────────────────────────────


def _conversation():
    """The Conversation this tool call belongs to.

    chatforge's call_ai_agent parks it on frappe.local for the duration of the
    request and deletes it afterwards, so it is only ever set while a tool is
    actually running inside a chat.
    """
    return getattr(frappe.local, "chatbot_conversation", None)


def _page_url(conversation):
    """The page the visitor was on when they opened the chat, if it was captured."""
    if not conversation:
        return ""
    raw = frappe.db.get_value("Conversation", conversation, "metadata")
    if not raw:
        return ""
    try:
        return (json.loads(raw) or {}).get("page_url") or ""
    except (TypeError, ValueError):
        return ""


def _transcript_rows(conversation, limit=None):
    """Every message in the chat, oldest first."""
    if not conversation:
        return []
    return frappe.get_all(
        "Conversation Message",
        filters={"parent": conversation, "parenttype": "Conversation"},
        fields=["sender", "content", "timestamp"],
        order_by="idx asc",
        limit=limit,
    )


def _transcript_html(conversation, page_url):
    """The whole conversation, both sides, as one Comment on the Lead.

    The Notes row carries the one-line summary a salesperson reads before
    calling; this is the full exchange for when they want the detail. It goes in
    a Comment rather than a second note so it stays out of the Notes list but
    is still on the Lead's own timeline, where the rest of the follow-up lands.
    """
    rows = _transcript_rows(conversation, limit=MAX_TRANSCRIPT_MESSAGES)
    if not rows:
        return ""

    lines = [f"<b>Chatbot conversation</b> — {frappe.utils.escape_html(conversation)}"]
    if page_url:
        lines.append(f"<small>Started on {frappe.utils.escape_html(page_url)}</small>")
    lines.append("<hr>")

    for row in rows:
        who = "Visitor" if (row.sender or "") == "User" else "Bot"
        text = " ".join((row.content or "").split())
        if not text:
            continue
        if len(text) > MAX_TRANSCRIPT_LINE_CHARS:
            text = text[:MAX_TRANSCRIPT_LINE_CHARS].rstrip() + "…"
        lines.append(f"<p><b>{who}:</b> {frappe.utils.escape_html(text)}</p>")

    if len(rows) == MAX_TRANSCRIPT_MESSAGES:
        lines.append(
            f"<p><small>Showing the first {MAX_TRANSCRIPT_MESSAGES} messages — "
            "open the Conversation for the rest.</small></p>"
        )

    return "".join(lines)


def _add_transcript_comment(lead, conversation, page_url):
    """Best-effort: the Lead is what matters, the transcript is a nicety."""
    if not conversation:
        return
    try:
        html = _transcript_html(conversation, page_url)
        if html:
            lead.add_comment("Comment", html)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Chatbot - Lead transcript comment")


def _visitor_summary(conversation):
    """What the visitor said, condensed — used when the agent passes no summary.

    Bot turns are dropped on purpose: they are the knowledge base talking, and
    repeating them back tells a salesperson nothing about the enquiry.
    """
    if not conversation:
        return ""

    rows = frappe.get_all(
        "Conversation Message",
        filters={"parent": conversation, "parenttype": "Conversation", "sender": "User"},
        fields=["content"],
        order_by="idx asc",
        limit=MAX_FALLBACK_MESSAGES,
    )

    said = [" ".join((row.content or "").split()) for row in rows]
    said = [line for line in said if line]
    if not said:
        return ""

    return ("Visitor asked: " + " | ".join(said))[:MAX_SUMMARY_CHARS].rstrip(" |")


def _note_html(summary, conversation, page_url):
    """The Notes row. Text Editor field, so this has to be HTML and escaped."""
    parts = [
        f"<p><b>Chatbot enquiry</b> — {frappe.utils.format_datetime(frappe.utils.now(), 'medium')}</p>",
        f"<p>{frappe.utils.escape_html(summary)}</p>",
    ]

    trail = []
    if conversation:
        trail.append(f"Conversation: {frappe.utils.escape_html(conversation)}")
    if page_url:
        trail.append(f"Page: {frappe.utils.escape_html(page_url)}")
    if trail:
        parts.append(f"<p><small>{' &middot; '.join(trail)}</small></p>")

    return "".join(parts)


def _append_note(doc, note):
    if not doc.meta.has_field("notes"):
        return False
    doc.append(
        "notes",
        {"note": note, "added_by": frappe.session.user, "added_on": frappe.utils.now_datetime()},
    )
    return True


def _close_conversation(conversation):
    """Mark the chat as converted.

    Best-effort: the Lead is already committed by the time this runs, so a failure
    here must not turn a captured lead into an error message for the visitor.
    """
    if not conversation:
        return
    try:
        frappe.db.set_value("Conversation", conversation, "status", "Lead Converted", update_modified=False)
        frappe.db.commit()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Chatbot - Conversation status update")


# ── setup ─────────────────────────────────────────────────────────────────────


def lead_source_field():
    """Which field carries the enquiry's origin, and what it links to.

    ERPNext v15 had Lead.source → Lead Source. v16 dropped both and attributes
    leads through Lead.utm_source → UTM Source instead. The old `tabLead Source`
    table survives an upgrade as an orphan with no DocType behind it, so testing
    the field on Lead is the only reliable way to tell the two apart — checking
    the link doctype would quietly pick the dead one.

    Returns (fieldname, link_doctype, name_field) or (None, None, None) when
    neither exists.
    """
    meta = frappe.get_meta("Lead")
    if meta.has_field("utm_source"):
        return "utm_source", "UTM Source", "name"
    if meta.has_field("source"):
        return "source", "Lead Source", "source_name"
    return None, None, None


def ensure_lead_source():
    """The "Chatbot" source record, created on first use.

    Created here rather than left as a setup step, so a site that has not had the
    source added by hand still captures the enquiry instead of throwing a
    link-validation error at a visitor mid-conversation.
    """
    fieldname, link_doctype, name_field = lead_source_field()
    if not fieldname:
        return None, None

    if not frappe.db.exists(link_doctype, LEAD_SOURCE):
        doc = frappe.get_doc(
            {
                "doctype": link_doctype,
                name_field: LEAD_SOURCE,
                "description" if link_doctype == "UTM Source" else "details":
                    "Enquiries captured by the website chatbot.",
            }
        )
        # UTM Source is autoname: prompt, so the name has to be set explicitly.
        if link_doctype == "UTM Source":
            doc.name = LEAD_SOURCE
        doc.insert(ignore_permissions=True)

    return fieldname, LEAD_SOURCE


def _default_company():
    return (
        frappe.defaults.get_user_default("company")
        or frappe.db.get_single_value("Global Defaults", "default_company")
        or frappe.db.get_value("Company", {}, "name")
    )


# ── the tool ──────────────────────────────────────────────────────────────────


@tool("create_crm_lead", return_direct=False)
def create_crm_lead_tool(
    full_name: str,
    email: str,
    company_name: str = "",
    phone: str = "",
    summary: str = "",
) -> str:
    """
    Create a CRM Lead from this chatbot conversation.

    Call this only once you have the visitor's name and email. It creates a Lead
    with source "Chatbot" and stores a short note of what the visitor is after, so
    the sales team can follow up without reading the chat. If a Lead already exists
    for that email, the note is added to it instead of creating a duplicate.

    Args:
        full_name: Full name of the visitor (required)
        email: Email address of the visitor (required, must be a valid address)
        company_name: Organization the visitor works for (optional)
        phone: Phone or mobile number (optional)
        summary: One or two sentences on what the visitor actually asked for, in
            their terms. Summarise only what they typed — do not invent a
            requirement from a menu button they clicked, and do not list services
            they never mentioned. Leave empty if they stated no requirement.

    Returns:
        JSON string with the outcome and a message to show the visitor.
    """
    try:
        # chatforge installs without ERPNext; say so rather than throwing a
        # DoesNotExistError at a visitor mid-conversation.
        if not frappe.db.exists("DocType", "Lead"):
            return json.dumps({
                "success": False,
                "error": "Lead doctype not available on this site",
                "message": "I'm having trouble saving your details. Please email us directly.",
            })

        if not full_name or not full_name.strip():
            return json.dumps({
                "success": False,
                "error": "Full name is required",
                "message": "I need your full name to proceed. Could you please share it?",
            })

        if not email or not email.strip():
            return json.dumps({
                "success": False,
                "error": "Email is required",
                "message": "I need your email address so our team can follow up. Could you share it?",
            })

        email = email.strip().lower()
        if not EMAIL_PATTERN.match(email):
            return json.dumps({
                "success": False,
                "error": "Invalid email format",
                "message": f"The email '{email}' looks a bit off. Could you double-check it for me?",
            })

        full_name = full_name.strip()
        company_name = (company_name or "").strip()
        # Keep the number as the visitor gave it, minus the spacing they typed —
        # enquiries come from every country, so guessing a dialling code would
        # corrupt more numbers than it fixes.
        phone = re.sub(r"[\s\-()]", "", phone or "")

        conversation = _conversation()
        page_url = _page_url(conversation)

        summary = (summary or "").strip()[:MAX_SUMMARY_CHARS]
        if not summary:
            summary = _visitor_summary(conversation)
        if not summary:
            summary = "No specific requirement stated — visitor left their contact details in chat."

        note = _note_html(summary, conversation, page_url)

        # ── an existing Lead wins over a duplicate ────────────────────────────
        existing = frappe.db.get_value("Lead", {"email_id": email}, "name", order_by="creation desc")
        if existing:
            lead = frappe.get_doc("Lead", existing)
            _append_note(lead, note)
            if phone and not lead.mobile_no:
                lead.mobile_no = phone
            if company_name and not lead.company_name:
                lead.company_name = company_name
            lead.save(ignore_permissions=True)
            _add_transcript_comment(lead, conversation, page_url)
            frappe.db.commit()

            _close_conversation(conversation)
            frappe.logger().info(f"Chatbot: note added to existing Lead {lead.name} ({email})")

            return json.dumps({
                "success": True,
                "lead": lead.name,
                "existing": True,
                "email": email,
                "conversation": conversation,
                "link": f"/app/lead/{lead.name}",
                "message": (
                    f"Thanks {full_name.split()[0]}! ✅ We already have your details on file and "
                    "I've passed this conversation on — our team will be in touch shortly."
                ),
            })

        # ── new Lead ──────────────────────────────────────────────────────────
        name_parts = full_name.split()
        lead_data = {
            "doctype": "Lead",
            "lead_name": full_name,
            "first_name": name_parts[0],
            "last_name": " ".join(name_parts[1:]),
            "email_id": email,
            "company_name": company_name,
            "status": "Lead",
        }

        source_field, source_value = ensure_lead_source()
        if source_field:
            lead_data[source_field] = source_value

        if phone:
            lead_data["mobile_no"] = phone

        company = _default_company()
        if company:
            lead_data["company"] = company

        lead = frappe.get_doc(lead_data)

        # Both are custom fields that may or may not be on this site.
        if page_url and lead.meta.has_field("source_web_page"):
            lead.source_web_page = page_url
        if conversation and lead.meta.has_field("custom_conversation"):
            lead.custom_conversation = conversation

        _append_note(lead, note)
        lead.insert(ignore_permissions=True)
        _add_transcript_comment(lead, conversation, page_url)
        frappe.db.commit()

        _close_conversation(conversation)
        frappe.logger().info(f"Chatbot: Lead created - {lead.name} for {email} (Conversation: {conversation})")

        return json.dumps({
            "success": True,
            "lead": lead.name,
            "existing": False,
            "full_name": full_name,
            "email": email,
            "phone": phone,
            "company_name": company_name,
            "summary": summary,
            "conversation": conversation,
            "link": f"/app/lead/{lead.name}",
            "message": (
                f"Thank you, {full_name.split()[0]}! ✅ I've passed your details to our team — "
                f"they'll reach out to you at {email} shortly."
            ),
        })

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Chatbot - Create CRM Lead Tool")
        return json.dumps({
            "success": False,
            "error": str(e),
            "message": "I hit a problem saving your details. Please try again, or email us directly.",
        })
