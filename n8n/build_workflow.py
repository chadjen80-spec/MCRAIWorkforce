"""Generates n8n/workflows/mcr-ai-workforce.json (kept as code so the node graph stays reviewable).

    python n8n/build_workflow.py

The workflow never executes AI work itself. It drives the FastAPI job API, which is the source of
truth, and all heavy work runs on Celery queues: n8n branches only *observe* per-agent progress.
No secret is embedded: nodes reference two n8n credentials by name (see docs/N8N.md).
"""
from __future__ import annotations

import json
from pathlib import Path

API = "={{ $env.MCR_API_URL }}"
JOB = "{{ $('Submit Job').first().json.id }}"
API_CRED = {"httpHeaderAuth": {"id": "REPLACE_AFTER_IMPORT", "name": "MCR API Key"}}
WEBHOOK_CRED = {"httpHeaderAuth": {"id": "REPLACE_AFTER_IMPORT", "name": "MCR Webhook Auth"}}

nodes: list[dict] = []
connections: dict[str, dict] = {}


def node(name: str, type_: str, version: float, pos: tuple[int, int], params: dict, **extra) -> str:
    nodes.append({"id": name.lower().replace(" ", "-").replace("?", ""), "name": name, "type": f"n8n-nodes-base.{type_}",
                  "typeVersion": version, "position": list(pos), "parameters": params, **extra})
    return name


def link(src: str, dst: str, output: int = 0, input_: int = 0) -> None:
    outs = connections.setdefault(src, {"main": []})["main"]
    while len(outs) <= output:
        outs.append([])
    outs[output].append({"node": dst, "type": "main", "index": input_})


def http(name: str, pos: tuple[int, int], method: str, path: str, body: str | None = None, headers: dict | None = None,
         timeout_ms: int = 15000) -> str:
    """Authenticated call to the MCR API with a timeout, bounded retries and an explicit error path."""
    params: dict = {
        "method": method,
        "url": f"{API}{path}",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "options": {"timeout": timeout_ms, "response": {"response": {"neverError": False}}},
    }
    if headers:
        params["sendHeaders"] = True
        params["headerParameters"] = {"parameters": [{"name": k, "value": v} for k, v in headers.items()]}
    if body is not None:
        params.update(sendBody=True, specifyBody="json", jsonBody=body)
    return node(name, "httpRequest", 4.2, pos, params, credentials=API_CRED, retryOnFail=True, maxTries=3,
                waitBetweenTries=2000, onError="continueErrorOutput")


def code(name: str, pos: tuple[int, int], js: str) -> str:
    return node(name, "code", 2, pos, {"jsCode": js})


def if_(name: str, pos: tuple[int, int], expression: str) -> str:
    return node(name, "if", 2.2, pos, {
        "conditions": {
            "options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
            "combinator": "and",
            "conditions": [{"id": "c1", "leftValue": expression, "rightValue": "",
                            "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
        },
        "options": {},
    })


def agent_js(agent: str) -> str:
    return (
        "// Observes the Celery-backed tasks of one agent. n8n does not run the agent: the queue does.\n"
        "const terminal = ['succeeded','failed','skipped','cancelled'];\n"
        f"const tasks = $('Get Tasks').first().json.items.filter(t => t.agent === '{agent}');\n"
        "const count = s => tasks.filter(t => t.status === s).length;\n"
        f"return [{{ json: {{ agent: '{agent}', total: tasks.length, succeeded: count('succeeded'), failed: count('failed'),\n"
        "  running: count('running'), queued: count('queued') + count('pending'),\n"
        "  done: tasks.every(t => terminal.includes(t.status)),\n"
        "  providers: [...new Set(tasks.map(t => t.provider).filter(Boolean))] } }];"
    )


# 1. authenticated webhook ---------------------------------------------------------------------
webhook = node("Webhook", "webhook", 2, (0, 300), {
    "httpMethod": "POST", "path": "mcr-ai-workforce", "authentication": "headerAuth",
    "responseMode": "responseNode", "options": {},
}, credentials=WEBHOOK_CRED, webhookId="mcr-ai-workforce")

# 2. input validation ----------------------------------------------------------------------------
validate = code("Validate Input", (220, 300), """const b = $input.first().json.body || {};
const errors = [];
const command = typeof b.command === 'string' ? b.command.trim() : (typeof b.prompt === 'string' ? b.prompt.trim() : '');
if (command.length < 5 || command.length > 4000) errors.push('command must be 5-4000 characters');
const allowed = ['content', 'image', 'document', 'video'];
const agents = Array.isArray(b.agents) && b.agents.length ? [...new Set(b.agents)] : allowed;
if (agents.some(a => !allowed.includes(a))) errors.push('agents must be a subset of ' + allowed.join(', '));
const budget = b.budget_limit_usd === undefined ? null : Number(b.budget_limit_usd);
if (budget !== null && !(budget > 0 && budget <= 1000)) errors.push('budget_limit_usd must be between 0 and 1000');
const language = b.language === 'en' ? 'en' : 'th';
const aspect_ratio = b.aspect_ratio === '9:16' ? '9:16' : '16:9';
const requestId = typeof b.request_id === 'string' && b.request_id.length <= 100 ? b.request_id : $execution.id;
const payload = { command, agents, language, aspect_ratio, auto_start: false,
  require_approval: b.require_approval !== false,
  data_classification: ['public', 'internal', 'restricted'].includes(b.data_classification) ? b.data_classification : 'internal' };
if (budget !== null) payload.budget_limit_usd = budget;
return [{ json: { valid: errors.length === 0, errors, payload, idempotency_key: 'n8n-' + requestId } }];""")
valid = if_("Input Valid?", (440, 300), "={{ $json.valid }}")
reject = node("Respond 400", "respondToWebhook", 1.1, (660, 480), {
    "respondWith": "json", "responseBody": "={{ JSON.stringify({ error: { code: 'VALIDATION_ERROR', message: $json.errors.join('; ') } }) }}",
    "options": {"responseCode": 400}})

# 3. job submission (idempotent; FastAPI/PostgreSQL own the job from here on) -----------------------
submit = http("Submit Job", (660, 280), "POST", "/v1/jobs", body="={{ JSON.stringify($json.payload) }}",
              headers={"Idempotency-Key": "={{ $json.idempotency_key }}"})
accepted = node("Respond 202", "respondToWebhook", 1.1, (880, 200), {
    "respondWith": "json",
    "responseBody": "={{ JSON.stringify({ job_id: $json.id, status: $json.status, status_url: '/v1/jobs/' + $json.id }) }}",
    "options": {"responseCode": 202}})
submit_failed = node("Respond 502", "respondToWebhook", 1.1, (880, 480), {
    "respondWith": "json",
    "responseBody": "={{ JSON.stringify({ error: { code: 'JOB_SUBMISSION_FAILED', message: 'The job could not be submitted to the MCR API' } }) }}",
    "options": {"responseCode": 502}})

# 4. planner invocation (async: enqueue, then poll) ---------------------------------------------------
plan = http("Invoke Planner", (1100, 200), "POST", f"=/v1/jobs/{JOB}/plan".replace("=/", "/"))
wait_plan = node("Wait For Plan", "wait", 1.1, (1320, 200), {"amount": 3, "unit": "seconds"}, webhookId="mcr-wait-plan")
get_plan = http("Get Plan Status", (1540, 200), "GET", f"/v1/jobs/{JOB}")
plan_state = code("Plan State", (1760, 200), """const job = $input.first().json;
const polls = $runIndex + 1;
return [{ json: { job_id: job.id, status: job.status, planned: job.status === 'planned' || job.status === 'running',
  give_up: ['failed', 'cancelled'].includes(job.status) || polls >= 100,
  error_code: job.error_code, error_message: job.error_message, polls } }];""")
planned = if_("Planned?", (1980, 200), "={{ $json.planned }}")
plan_failed = if_("Planning Failed?", (2200, 380), "={{ $json.give_up }}")

# 5. parallel task dispatch: one call puts every ready task on its Celery queue ------------------------
dispatch = http("Dispatch Tasks", (2200, 180), "POST", f"/v1/jobs/{JOB}/dispatch")

# 6-9. per-agent observation of queue-backed work -----------------------------------------------------------
wait_tasks = node("Wait For Agents", "wait", 1.1, (2420, 180), {"amount": 10, "unit": "seconds"}, webhookId="mcr-wait-agents")
get_tasks = http("Get Tasks", (2640, 180), "GET", f"/v1/jobs/{JOB}/tasks")
agents = [code(f"{a.title()} Agent", (2880, 20 + i * 150), agent_js(a)) for i, a in enumerate(["content", "image", "document", "video"])]
merge = node("Merge Agent Status", "merge", 3, (3120, 240), {"mode": "append", "numberInputs": 4})
get_job = http("Get Job", (3340, 240), "GET", f"/v1/jobs/{JOB}")
progress = code("Job Progress", (3560, 240), """const job = $input.first().json;
const agents = $('Merge Agent Status').all().map(i => i.json);
const finished = ['awaiting_approval', 'completed', 'partial', 'failed', 'cancelled', 'rejected'].includes(job.status);
const polls = $runIndex + 1;
return [{ json: { job_id: job.id, status: job.status, progress: job.progress, finished, timed_out: !finished && polls >= 360,
  stop: finished || polls >= 360, demo_mode: job.demo_mode, error_code: job.error_code, agents, polls } }];""")
done = if_("Job Finished?", (3780, 240), "={{ $json.stop }}")

# 10. QA coordination ------------------------------------------------------------------------------------------
qa = http("QA Coordination", (4000, 220), "GET", f"/v1/jobs/{JOB}/qa")

# 11. job status update (written to the job's audit trail in PostgreSQL) -----------------------------------------
summary = code("Build Summary", (4220, 220), """const p = $('Job Progress').last().json;
const qa = $input.first().json;
const warnings = (qa.checks || []).filter(c => c.status !== 'pass').map(c => c.name + ': ' + c.detail);
const text = p.timed_out
  ? `MCR AI Workforce: job ${p.job_id} is still ${p.status} after ${p.polls} checks. It keeps running; see the dashboard.`
  : `MCR AI Workforce: job ${p.job_id} finished with status ${p.status}` + (p.demo_mode ? ' (DEMO output, not AI-generated)' : '')
    + `. QA: ${qa.verdict || 'n/a'}.` + (p.status === 'awaiting_approval' ? ' Approval is required in the dashboard.' : '');
return [{ json: { job_id: p.job_id, status: p.status, timed_out: p.timed_out, demo_mode: p.demo_mode, qa_verdict: qa.verdict,
  qa_summary: qa.summary, warnings, agents: p.agents, text } }];""")
status_update = http("Job Status Update", (4440, 220), "POST", f"/v1/jobs/{JOB}/events", body=(
    "={{ JSON.stringify({ action: $('Build Summary').first().json.timed_out ? 'n8n.monitor_timeout' : 'n8n.workflow_completed', "
    "detail: { status: $('Build Summary').first().json.status, qa_verdict: $('Build Summary').first().json.qa_verdict, "
    "execution_id: $execution.id } }) }}"))

# 12. completion notification -----------------------------------------------------------------------------------------
has_notify = if_("Notify Configured?", (4660, 220), "={{ !!$env.MCR_NOTIFY_WEBHOOK_URL }}")
notify = node("Completion Notification", "httpRequest", 4.2, (4880, 140), {
    "method": "POST", "url": "={{ $env.MCR_NOTIFY_WEBHOOK_URL }}", "sendBody": True, "specifyBody": "json",
    "jsonBody": "={{ JSON.stringify({ text: $('Build Summary').first().json.text, job: $('Build Summary').first().json }) }}",
    "options": {"timeout": 10000},
}, retryOnFail=True, maxTries=3, waitBetweenTries=3000, onError="continueRegularOutput")
finished = node("Done", "noOp", 1, (5100, 220), {})

# failure reporting: planning failed, or an API call exhausted its retries -------------------------------------------------
failure = code("Build Failure Report", (2420, 520), """const j = $input.first().json || {};
return [{ json: { job_id: j.job_id || $('Submit Job').first().json.id, status: j.status || 'unknown',
  text: 'MCR AI Workforce: workflow stopped for job ' + (j.job_id || $('Submit Job').first().json.id)
    + ' (' + (j.error_code || j.status || 'API call failed after retries') + '). The job state in the dashboard is authoritative.' } }];""")
failure_notify = node("Failure Notification", "httpRequest", 4.2, (2660, 520), {
    "method": "POST", "url": "={{ $env.MCR_NOTIFY_WEBHOOK_URL || $env.MCR_API_URL + '/health/live' }}",
    "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify({ text: $json.text, job: $json }) }}",
    "options": {"timeout": 10000},
}, onError="continueRegularOutput")

# wiring ---------------------------------------------------------------------------------------------------------------------
link(webhook, validate)
link(validate, valid)
link(valid, submit, 0)
link(valid, reject, 1)
link(submit, accepted, 0)
link(submit, submit_failed, 1)
link(accepted, plan)
link(plan, wait_plan, 0)
link(plan, failure, 1)
link(wait_plan, get_plan)
link(get_plan, plan_state, 0)
link(get_plan, failure, 1)
link(plan_state, planned)
link(planned, dispatch, 0)
link(planned, plan_failed, 1)
link(plan_failed, failure, 0)
link(plan_failed, wait_plan, 1)
link(dispatch, wait_tasks, 0)
link(dispatch, failure, 1)
link(wait_tasks, get_tasks)
for i, a in enumerate(agents):
    link(get_tasks, a, 0)
    link(a, merge, 0, i)
link(get_tasks, failure, 1)
link(merge, get_job)
link(get_job, progress, 0)
link(get_job, failure, 1)
link(progress, done)
link(done, qa, 0)
link(done, wait_tasks, 1)
link(qa, summary, 0)
link(qa, failure, 1)
link(summary, status_update)
link(status_update, has_notify, 0)
link(status_update, has_notify, 1)
link(has_notify, notify, 0)
link(has_notify, finished, 1)
link(notify, finished)
link(failure, failure_notify)

workflow = {
    "name": "MCR AI Workforce",
    "nodes": nodes,
    "connections": connections,
    "settings": {"executionOrder": "v1", "saveExecutionProgress": True, "executionTimeout": 7200},
    "active": False,
    "pinData": {},
    "meta": {"templateCredsSetupCompleted": False},
}
out = Path(__file__).parent / "workflows" / "mcr-ai-workforce.json"
out.parent.mkdir(parents=True, exist_ok=True)
text = json.dumps(workflow, ensure_ascii=False, indent=2)
assert "mcr_" not in text and "sk-" not in text, "no secrets may be embedded"
names = {n["name"] for n in nodes}
for src, conn in connections.items():
    assert src in names, src
    for outs in conn["main"]:
        for target in outs:
            assert target["node"] in names, target
out.write_text(text + "\n", encoding="utf-8")
print(f"wrote {out} ({len(nodes)} nodes)")
