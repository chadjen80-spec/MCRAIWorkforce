"""Generates the six Quote-to-Cash workflows in n8n/workflows/qtc-*.json.

    python n8n/build_qtc_workflows.py

Five workflows are driven by **signed outbound events** from the MCR API (no polling): the API POSTs each event to
`{QTC_EVENTS_URL}-{slug}` with an HMAC-SHA256 signature; the workflow's first step checks the signature and the
timestamp and rejects anything else with 401. The sixth (new customer inquiry) receives inquiries from a website
form through a Header Auth webhook and calls the read-only Sales Intake endpoint with a key that has only the
`qtc:read` and `qtc:intake` scopes.

No credential or secret is embedded:
- the event secret is read from n8n's environment (`MCR_EVENTS_SECRET`),
- API calls use the n8n credential named "MCR QTC Key", the inquiry webhook the credential "MCR Webhook Auth",
- the team notification goes to `MCR_NOTIFY_WEBHOOK_URL` (an internal channel) when it is set.

These workflows notify the **team**. They never message customers: the API has no sending of quotations or
reminders, and `/v1/qtc/reminders/{id}/send` refuses.
"""

from __future__ import annotations

import json
from pathlib import Path

API = "={{ $env.MCR_API_URL }}"
QTC_CRED = {"httpHeaderAuth": {"id": "REPLACE_AFTER_IMPORT", "name": "MCR QTC Key"}}
WEBHOOK_CRED = {"httpHeaderAuth": {"id": "REPLACE_AFTER_IMPORT", "name": "MCR Webhook Auth"}}
OUT = Path(__file__).parent / "workflows"

VERIFY_JS = r"""// Accept only events signed by the MCR API: HMAC-SHA256(MCR_EVENTS_SECRET, timestamp + "." + raw body),
// sent within the last 5 minutes. The secret comes from n8n's environment, never from this workflow.
const crypto = require('crypto');
const secret = $env.MCR_EVENTS_SECRET || '';
const item = $input.first();
const h = item.json.headers || {};
let raw = '';
if (item.binary && item.binary.data) {
  raw = (await this.helpers.getBinaryDataBuffer(0, 'data')).toString('utf8');
} else {
  raw = JSON.stringify(item.json.body);
}
const ts = Number(h['x-mcr-timestamp']);
const sig = String(h['x-mcr-signature'] || '');
const expected = 'sha256=' + crypto.createHmac('sha256', secret).update(ts + '.' + raw).digest('hex');
const fresh = Number.isFinite(ts) && Math.abs(Date.now() / 1000 - ts) <= 300;
const same = sig.length === expected.length && crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected));
const valid = secret.length >= 32 && fresh && same;
let event = null;
if (valid) event = JSON.parse(raw);
const type = event && event.type;
return [{ json: { valid: valid && type === EXPECTED_TYPE, event,
  reason: !secret ? 'MCR_EVENTS_SECRET is not set' : !fresh ? 'stale or missing timestamp' : !same ? 'bad signature' : 'unexpected event type' } }];"""

MONEY_JS = """const money = v => Number(v || 0).toLocaleString('th-TH', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' บาท';
const app = ($env.MCR_APP_URL || 'http://localhost:3000').replace(/\\/$/, '');
"""


class Flow:
    def __init__(self, name: str):
        self.name = name
        self.nodes: list[dict] = []
        self.connections: dict[str, dict] = {}

    def node(self, name: str, type_: str, version: float, pos: tuple[int, int], params: dict, **extra) -> str:
        self.nodes.append({"id": name.lower().replace(" ", "-").replace("?", ""), "name": name, "type": f"n8n-nodes-base.{type_}",
                           "typeVersion": version, "position": list(pos), "parameters": params, **extra})
        return name

    def link(self, src: str, dst: str, output: int = 0) -> None:
        outs = self.connections.setdefault(src, {"main": []})["main"]
        while len(outs) <= output:
            outs.append([])
        outs[output].append({"node": dst, "type": "main", "index": 0})

    def code(self, name: str, pos: tuple[int, int], js: str) -> str:
        return self.node(name, "code", 2, pos, {"jsCode": js})

    def if_(self, name: str, pos: tuple[int, int], expression: str) -> str:
        return self.node(name, "if", 2.2, pos, {
            "conditions": {
                "options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
                "combinator": "and",
                "conditions": [{"id": "c1", "leftValue": expression, "rightValue": "",
                                "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
            },
            "options": {},
        })

    def respond(self, name: str, pos: tuple[int, int], code: int, body: str) -> str:
        return self.node(name, "respondToWebhook", 1.1, pos, {"respondWith": "json", "responseBody": body, "options": {"responseCode": code}})

    def notify(self, pos: tuple[int, int]) -> str:
        """Post the composed message to the team's internal channel, if one is configured."""
        has = self.if_("Notify Configured?", pos, "={{ !!$env.MCR_NOTIFY_WEBHOOK_URL }}")
        send = self.node("Notify Team", "httpRequest", 4.2, (pos[0] + 220, pos[1] - 20), {
            "method": "POST",
            "url": "={{ $env.MCR_NOTIFY_WEBHOOK_URL }}",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ text: $json.text, event: $json.event_type, link: $json.link }) }}",
            "options": {"timeout": 10000},
        }, retryOnFail=True, maxTries=3, waitBetweenTries=2000, onError="continueRegularOutput")
        self.link(has, send)
        return has

    def write(self, filename: str) -> Path:
        workflow = {
            "name": self.name,
            "nodes": self.nodes,
            "connections": self.connections,
            "settings": {"executionOrder": "v1", "saveExecutionProgress": True, "executionTimeout": 300},
            "active": False,
            "pinData": {},
            "meta": {"templateCredsSetupCompleted": False},
        }
        text = json.dumps(workflow, ensure_ascii=False, indent=2)
        assert "mcr_" not in text and "sk-" not in text, "no secrets may be embedded"
        names = {n["name"] for n in self.nodes}
        assert len(names) == len(self.nodes), "node names must be unique"
        for src, conn in self.connections.items():
            assert src in names, src
            for outs in conn["main"]:
                for target in outs:
                    assert target["node"] in names, target
        path = OUT / filename
        path.write_text(text + "\n", encoding="utf-8")
        return path


def event_flow(title: str, event_type: str, compose_js: str) -> Flow:
    slug = event_type.replace(".", "-").replace("_", "-")
    f = Flow(f"MCR QTC: {title}")
    hook = f.node("Event Webhook", "webhook", 2, (0, 300), {
        "httpMethod": "POST", "path": f"mcr-qtc-{slug}", "responseMode": "responseNode", "options": {"rawBody": True},
    }, webhookId=f"mcr-qtc-{slug}")
    verify = f.code("Verify Signature", (220, 300), VERIFY_JS.replace("EXPECTED_TYPE", json.dumps(event_type)))
    valid = f.if_("Signature Valid?", (440, 300), "={{ $json.valid }}")
    ok = f.respond("Respond 202", (660, 200), 202, "={{ JSON.stringify({ received: $json.event.id }) }}")
    deny = f.respond("Respond 401", (660, 440), 401, "={{ JSON.stringify({ error: { code: 'INVALID_SIGNATURE', message: $json.reason } }) }}")
    compose = f.code("Compose Message", (880, 200), MONEY_JS + f"const e = $('Signature Valid?').first().json.event;\nconst d = e.data;\n{compose_js}")
    notify = f.notify((1100, 200))
    f.link(hook, verify)
    f.link(verify, valid)
    f.link(valid, ok, 0)
    f.link(valid, deny, 1)
    f.link(ok, compose)
    f.link(compose, notify)
    return f


def ret(text_expr: str) -> str:
    return f"return [{{ json: {{ event_type: e.type, text: {text_expr}, link: app + (d.link || '/qtc') }} }}];"


FLOWS = {
    "qtc-quotation-approval.json": event_flow(
        "Quotation approval notification", "quotation.submitted",
        ret("`ใบเสนอราคา ${d.number} (ฉบับที่ ${d.revision}) ของ ${d.customer} ยอด ${money(d.grand_total)} รออนุมัติ\\n"
            "ส่งโดย ${d.submitted_by}\\nอนุมัติได้ที่ ${app}${d.link}`"),
    ),
    "qtc-quotation-decision.json": event_flow(
        "Quotation approval decision", "quotation.decided",
        "const label = { approved: 'อนุมัติแล้ว', rejected: 'ไม่อนุมัติ', changes_requested: 'ขอให้แก้ไข' }[d.decision] || d.decision;\n"
        + ret("`ใบเสนอราคา ${d.number} ของ ${d.customer}: ${label} โดย ${d.decided_by}` + (d.comment ? `\\nความเห็น: ${d.comment}` : '')"),
    ),
    "qtc-quotation-accepted.json": event_flow(
        "Quotation acceptance", "quotation.accepted",
        ret("`ลูกค้า ${d.customer} ตอบรับใบเสนอราคา ${d.number} ยอด ${money(d.grand_total)} (ผู้ตอบรับ: ${d.accepted_by_name})\\n"
            "ขั้นต่อไป: สร้างใบสั่งขายที่ ${app}${d.link}`"),
    ),
    "qtc-invoice-due-reminder.json": event_flow(
        "Invoice due-date reminder", "invoice.due_soon",
        ret("`ใบแจ้งหนี้ ${d.number} ของ ${d.customer} ครบกำหนด ${d.due_date} ยอดคงค้าง ${money(d.balance)}\\n"
            "(แจ้งทีมงานเท่านั้น ระบบไม่ส่งข้อความถึงลูกค้า)`"),
    ),
    "qtc-overdue-payment-alert.json": event_flow(
        "Overdue payment alert", "invoice.overdue",
        ret("`ใบแจ้งหนี้ ${d.number} ของ ${d.customer} เกินกำหนด ${d.days_overdue} วัน ยอดคงค้าง ${money(d.balance)}\\n"
            "เตรียมร่างข้อความติดตามได้ที่ Mira: \"เตรียมข้อความติดตามเงินสำหรับ Invoice ที่เกินกำหนด\"`"),
    ),
    "qtc-weekly-receivables.json": event_flow(
        "Weekly receivables summary", "receivables.weekly_summary",
        "const d2 = { ...d, link: '/qtc' };\n"
        "return [{ json: { event_type: e.type, link: app + '/qtc', text: [\n"
        "  `สรุปลูกหนี้ประจำสัปดาห์ ${d2.week}`,\n"
        "  `ลูกหนี้คงค้าง ${money(d.outstanding.amount)} (${d.outstanding.count} ใบ)`,\n"
        "  `เกินกำหนด ${money(d.overdue.amount)} (${d.overdue.count} ใบ)`,\n"
        "  `รับชำระเดือนนี้ ${money(d.cash_received.amount)}`,\n"
        "  `ใบเสนอราคาที่เปิดอยู่ ${money(d.pipeline.amount)} · รออนุมัติ ${d.awaiting_approval.count} ฉบับ`,\n"
        "].join('\\n') } }];",
    ),
}


def inquiry_flow() -> Flow:
    f = Flow("MCR QTC: New customer inquiry")
    hook = f.node("Inquiry Webhook", "webhook", 2, (0, 300), {
        "httpMethod": "POST", "path": "mcr-qtc-inquiry", "authentication": "headerAuth", "responseMode": "responseNode", "options": {},
    }, credentials=WEBHOOK_CRED, webhookId="mcr-qtc-inquiry")
    validate = f.code("Validate Inquiry", (220, 300), """const b = $input.first().json.body || {};
const message = String(b.message || b.text || '').trim();
const parts = [message];
for (const [k, label] of [['company', 'บริษัท'], ['name', 'ผู้ติดต่อ'], ['email', 'อีเมล'], ['phone', 'โทร']]) {
  if (typeof b[k] === 'string' && b[k].trim()) parts.push(`${label}: ${b[k].trim().slice(0, 200)}`);
}
const text = parts.join('\\n').slice(0, 4000);
return [{ json: { valid: message.length >= 5, text, errors: message.length >= 5 ? [] : ['message must be at least 5 characters'] } }];""")
    valid = f.if_("Inquiry Valid?", (440, 300), "={{ $json.valid }}")
    reject = f.respond("Respond 400", (660, 460), 400,
                       "={{ JSON.stringify({ error: { code: 'VALIDATION_ERROR', message: $json.errors.join('; ') } }) }}")
    intake = f.node("Sales Intake Agent", "httpRequest", 4.2, (660, 280), {
        "method": "POST",
        "url": f"{API}/v1/qtc/agents/sales-intake",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ text: $json.text }) }}",
        "options": {"timeout": 15000},
    }, credentials=QTC_CRED, retryOnFail=True, maxTries=3, waitBetweenTries=2000, onError="continueErrorOutput")
    ok = f.respond("Respond 202", (880, 200), 202, "={{ JSON.stringify({ received: true }) }}")
    failed = f.respond("Respond 502", (880, 440), 502,
                       "={{ JSON.stringify({ error: { code: 'INTAKE_FAILED', message: 'The inquiry could not be processed' } }) }}")
    compose = f.code("Compose Message", (1100, 200), MONEY_JS + """const r = $('Sales Intake Agent').first().json;
const who = r.customer ? `ลูกค้าเดิม ${r.customer.name} (${r.customer.code})`
  : r.customer_candidates.length ? `อาจเป็น: ${r.customer_candidates.map(c => c.name).join(', ')}`
  : `ลูกค้าใหม่ ${r.new_customer.name || ''} ${r.new_customer.email || ''} ${r.new_customer.phone || ''}`.trim();
const services = r.services.map(s => s.name).join(', ') || 'ยังไม่ตรงกับบริการในแคตตาล็อก';
const extra = r.unlisted_items.length ? `\\nรายการนอกแคตตาล็อก (ต้องกำหนดราคาเอง): ${r.unlisted_items.join(', ')}` : '';
const link = r.customer ? '/qtc/customers/' + r.customer.id : '/qtc/customers';
return [{ json: { event_type: 'customer.inquiry', link: app + link,
  text: `มีคำขอใบเสนอราคาใหม่\\n${who}\\nบริการ: ${services}${extra}\\nเปิดดู: ${app}${link}` } }];""")
    notify = f.notify((1320, 200))
    f.link(hook, validate)
    f.link(validate, valid)
    f.link(valid, intake, 0)
    f.link(valid, reject, 1)
    f.link(intake, ok, 0)
    f.link(intake, failed, 1)
    f.link(ok, compose)
    f.link(compose, notify)
    return f


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    written = [inquiry_flow().write("qtc-new-customer-inquiry.json")]
    written += [flow.write(name) for name, flow in FLOWS.items()]
    for path in written:
        print(f"wrote {path}")
