"""V2 workbench privacy controls and same-origin visitor interaction regressions.

HTTP is mocked explicitly. These checks do not claim a live model result.
"""

from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

import httpx
from streamlit.testing.v1 import AppTest

from demo.support_admin import SupportAPI, SupportAPIError


ROOT = Path(__file__).resolve().parents[1]


class TestWorkbenchV2(unittest.TestCase):
    def setUp(self):
        self.conversation_status = "human"
        self.pending = False
        self.deleted = False
        self.calls = []
        self.fail_preview = False
        self.patch = patch("demo.support_admin.httpx.request", side_effect=self.backend)
        self.patch.start()
        self.env = patch.dict("os.environ", {"SUPPORT_API_URL": "http://service"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.patch.stop()

    def backend(self, method, url, **kwargs):
        path = httpx.URL(url).path
        self.calls.append((method, path, kwargs))
        code = 200
        if path == "/admin/conversations":
            data = [] if self.deleted else [{"id": "session-1", "status": self.conversation_status}]
        elif path == "/admin/conversations/session-1":
            if method == "DELETE":
                self.deleted = True
                data = {"deleted": True}
            else:
                data = {"id": "session-1", "status": self.conversation_status, "messages": []}
        elif path == "/admin/conversations/session-1/close":
            self.conversation_status = "closed"
            data = {"status": "closed"}
        elif path == "/admin/conversations/session-1/resume":
            self.conversation_status = "ai"
            data = {"status": "ai"}
        elif path == "/admin/handoffs":
            data = [{"id": "handoff-1", "conversation_id": "session-1", "status": "pending"}] if self.pending else []
        elif path == "/admin/model/status":
            data = {"provider": "ollama", "status": "DISABLED", "available": False,
                    "local_provider": {"provider": "ollama", "status": "DISABLED", "available": False}}
        elif path == "/admin/model/usage":
            data = {"estimated_cost": None, "unknown_cost_calls": 1}
        elif path == "/admin/limits":
            data = {"retention_days": 30, "rate_limits": {"create_per_hour": 12, "message_per_minute": 8},
                    "model_budget": {"daily_call_limit": 50, "daily_token_limit": 50000, "calls": 3,
                                     "tokens": 12000, "remaining_calls": 47, "remaining_tokens": 38000,
                                     "cost_status": "unknown"}}
        elif path == "/admin/privacy/retention/preview":
            code = 503 if self.fail_preview else 200
            data = {"dry_run": True, "cutoff": "2026-01-01T00:00:00Z", "eligible_count": 2,
                    "counts": {"conversations": 2, "messages": 5, "handoffs": 0, "leads": 1, "model_usage": 0}}
        elif path == "/admin/privacy/retention/purge":
            data = {"dry_run": False, "deleted_count": 2}
        elif path == "/admin/audit":
            data = [{"id": "audit-1", "event": "conversation_closed", "actor": "admin",
                     "object_id": "hashed-reference", "created_at": "2026-01-01", "secret": "never-display-private"}]
        elif path == "/support/conversations":
            data = {"id": "visitor-1", "session_token": "visitor-secret"}
        elif path == "/support/conversations/visitor-1":
            data = {"id": "visitor-1", "status": "closed", "messages": []}
        else:
            data = {"deleted": True} if method == "DELETE" else []
        return httpx.Response(code, json=data, request=httpx.Request(method, url))

    @staticmethod
    def label(elements, label):
        return next(element for element in elements if element.label == label)

    def app(self):
        app = AppTest.from_string("from demo.support_admin import render_support_admin\nrender_support_admin('admin-private')").run()
        self.assertFalse(app.exception, app.exception)
        return app

    def mutations(self, suffix):
        return [call for call in self.calls if call[0] in {"POST", "DELETE"} and call[1].endswith(suffix)]

    @staticmethod
    def visible(app):
        return "\n".join(str(item.value) for group in (app.info, app.warning, app.error, app.caption, app.success)
                         for item in group)

    def test_six_pages_disabled_model_unknown_cost_and_budget(self):
        app = self.app()
        self.assertEqual([tab.label for tab in app.tabs], ["产品与知识库", "AI 客服测试", "客户会话", "人工接管队列", "模型状态和调用成本", "隐私与审计"])
        self.assertIn("DISABLED：管理员已主动暂停模型", self.visible(app))
        self.assertIn("调用成本：未知", self.visible(app))
        self.assertIn("保守预留", self.visible(app))
        self.assertFalse(any(call[0] == "POST" for call in self.calls))
        budget_frames = [frame.value for frame in app.dataframe if "daily_call_limit" in frame.value.columns]
        self.assertEqual(budget_frames[0].iloc[0]["daily_call_limit"], 50)

    def test_delete_requires_explicit_confirmation_and_admin_header(self):
        app = self.app()
        self.assertFalse(self.label(app.checkbox, "我确认永久删除所选会话及关联数据，且无法恢复").value)
        self.assertTrue(self.label(app.button, "永久删除所选会话").disabled)
        self.assertFalse(self.mutations("/session-1"))
        self.label(app.checkbox, "我确认永久删除所选会话及关联数据，且无法恢复").check().run()
        self.label(app.button, "永久删除所选会话").click().run()
        method, _, kwargs = self.mutations("/session-1")[0]
        self.assertEqual(method, "DELETE")
        self.assertEqual(kwargs["json"], {"confirm": True})
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer admin-private"})
        self.assertNotIn("admin-private", self.visible(app))

    def test_lead_withdrawal_requires_confirmation(self):
        app = self.app()
        self.assertTrue(self.label(app.button, "撤回所选联系线索").disabled)
        self.label(app.checkbox, "确认撤回所选会话的联系线索").check().run()
        self.label(app.button, "撤回所选联系线索").click().run()
        method, _, kwargs = self.mutations("/lead")[0]
        self.assertEqual(method, "DELETE")
        self.assertEqual(kwargs["json"], {"confirm": True})

    def test_resume_is_disabled_until_pending_handoffs_resolved(self):
        self.pending = True
        app = self.app()
        self.assertTrue(self.label(app.button, "恢复所选会话的 AI").disabled)
        self.assertIn("完成全部待办", self.visible(app))
        self.pending = False
        app.run()
        self.assertFalse(self.label(app.button, "恢复所选会话的 AI").disabled)
        self.label(app.button, "恢复所选会话的 AI").click().run()
        self.assertEqual(self.mutations("/resume")[0][2]["json"], {})

    def test_closed_conversation_cannot_resume_or_close_again(self):
        self.conversation_status = "closed"
        app = self.app()
        self.assertTrue(self.label(app.button, "关闭所选会话").disabled)
        self.assertTrue(self.label(app.button, "恢复所选会话的 AI").disabled)
        self.assertTrue(self.label(app.button, "永久删除所选会话").disabled)

    def test_close_uses_selected_conversation_only(self):
        app = self.app()
        self.label(app.button, "关闭所选会话").click().run()
        self.assertEqual(self.mutations("/close")[0][1], "/admin/conversations/session-1/close")
        app.run()
        self.assertTrue(self.label(app.button, "关闭所选会话").disabled)

    def test_purge_needs_preview_then_confirmation(self):
        app = self.app()
        self.assertTrue(self.label(app.button, "永久清理到期数据").disabled)
        self.assertTrue(self.label(app.checkbox, "我已核对当前预览，确认永久清理到期会话及关联数据，且无法恢复").disabled)
        self.label(app.button, "预览到期数据").click().run()
        self.assertTrue(self.label(app.button, "永久清理到期数据").disabled)
        self.assertFalse(self.mutations("/purge"))
        self.label(app.checkbox, "我已核对当前预览，确认永久清理到期会话及关联数据，且无法恢复").check().run()
        self.label(app.button, "永久清理到期数据").click().run()
        self.assertEqual(self.mutations("/purge")[0][2]["json"], {"days": 30, "confirm": True})
        app.run()
        self.assertTrue(self.label(app.button, "永久清理到期数据").disabled)

    def test_changing_retention_days_invalidates_preview_and_consent(self):
        app = self.app()
        self.label(app.button, "预览到期数据").click().run()
        self.label(app.checkbox, "我已核对当前预览，确认永久清理到期会话及关联数据，且无法恢复").check().run()
        self.label(app.number_input, "会话保留天数").set_value(60).run()
        self.assertTrue(self.label(app.button, "永久清理到期数据").disabled)
        self.assertFalse(self.label(app.checkbox, "我已核对当前预览，确认永久清理到期会话及关联数据，且无法恢复").value)
        self.label(app.number_input, "会话保留天数").set_value(30).run()
        self.assertTrue(self.label(app.button, "永久清理到期数据").disabled)
        self.assertFalse(self.mutations("/purge"))

    def test_failed_preview_cannot_enable_purge(self):
        self.fail_preview = True
        app = self.app()
        self.label(app.button, "预览到期数据").click().run()
        self.assertTrue(self.label(app.button, "永久清理到期数据").disabled)
        self.assertIn("BLOCKED", self.visible(app))
        self.assertFalse(self.mutations("/purge"))

    def test_audit_only_displays_allowlisted_metadata(self):
        app = self.app()
        audit_frame = next(frame.value for frame in app.dataframe if "event" in frame.value.columns)
        self.assertEqual(set(audit_frame.columns), {"id", "event", "actor", "object_id", "created_at"})
        self.assertNotIn("never-display-private", audit_frame.to_json())

    def test_closed_test_chat_disables_mutations(self):
        app = self.app()
        self.label(app.button, "创建新的测试会话").click().run()
        for label in ("发送客服消息", "保存客户线索", "转接人工客服"):
            self.assertTrue(self.label(app.button, label).disabled)
        self.assertNotIn("visitor-secret", self.visible(app))

    def test_admin_error_response_does_not_display_private_content(self):
        for code in (401, 403, 409, 429, 503):
            with self.subTest(code=code):
                response = httpx.Response(code, json={"detail": "credential-and-customer-secret"},
                                          request=httpx.Request("DELETE", "http://service/test"))
                with patch("demo.support_admin.httpx.request", return_value=response), self.assertRaises(SupportAPIError) as caught:
                    SupportAPI("http://service", "secret").request("DELETE", "/test", body={"confirm": True}, admin=True)
                self.assertNotIn("credential-and-customer-secret", str(caught.exception))


# Execute the shipped JavaScript in a small DOM with mocked fetch; no npm packages,
# network calls or model process are required for these interaction contracts.
JS_HARNESS = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const ids = ["welcome","workspace","status","notice","start","new-session","chat-form","message","handoff","refresh",
 "lead-form","lead-name","contact","need","consent","messages","close-session","withdraw-confirm","withdraw-lead","delete-confirm","delete-session"];
const elements = Object.fromEntries(ids.map(id => [id, {id, checked: false, disabled: false, value: "", textContent: "", hidden: false,
 handlers: {}, children: [], addEventListener(event, action) { this.handlers[event] = action; },
 replaceChildren() { this.children = []; }, appendChild(child) { this.children.push(child); },
 reset() { for (const id of ["lead-name","contact","need"]) elements[id].value = ""; elements.consent.checked = false; }}]));
const send = {disabled: false}; const leadSubmit = {disabled: false};
const buttons = ["start","new-session","handoff","refresh","close-session","withdraw-lead","delete-session"].map(id => elements[id]).concat([send, leadSubmit]);
globalThis.document = {getElementById: id => elements[id], createElement: () => ({textContent: ""}),
 querySelectorAll: query => query === "button" ? buttons : [elements.message, send, elements["lead-name"], elements.contact, elements.need, elements.consent, leadSubmit]};
const stored = new Map();
globalThis.sessionStorage = {getItem: key => stored.get(key) || null, setItem: (key, val) => stored.set(key, val), removeItem: key => stored.delete(key)};
let serverState = "ai"; let failures = false; const fetchCalls = [];
globalThis.fetch = async (path, options) => {
 fetchCalls.push({path, ...options});
 if (failures) return {ok: false, status: 503, json: async () => ({detail: {message: "private-key-and-customer-data"}})};
 let data;
 if (path === "/support/conversations") data = {id: "own-session", session_token: "private-session-token"};
 else if (path.endsWith("/close")) { serverState = "closed"; data = {status: "closed"}; }
 else if (options.method === "DELETE") data = {deleted: true};
 else data = {id: "own-session", status: serverState, messages: [{role: "assistant", content: "<script>untrusted</script>"}]};
 return {ok: true, status: 200, json: async () => data};
};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"), {filename: "customer.js"});
async function trigger(id, event = "click") {
 await elements[id].handlers[event]({preventDefault() {}});
 await new Promise(resolve => setImmediate(resolve));
}
(async () => {
 elements.notice.checked = true; await trigger("start");
 assert.equal(elements.workspace.hidden, false);
 assert.equal(elements.messages.children[0].textContent, "客服：\n<script>untrusted</script>");
 assert.equal(elements["delete-session"].disabled, true);
 assert.equal(elements["withdraw-lead"].disabled, true);
 const scenario = process.argv[2];
 if (scenario === "closed") {
   await trigger("close-session");
   assert.equal(elements.message.disabled, true); assert.equal(send.disabled, true);
   assert.equal(leadSubmit.disabled, true); assert.equal(elements.handoff.disabled, true);
   assert.equal(elements.refresh.disabled, false); assert.equal(elements["close-session"].disabled, true);
   assert.match(elements.status.textContent, /会话已关闭/);
 } else if (scenario === "withdraw") {
   await trigger("withdraw-lead"); assert.equal(fetchCalls.filter(x => x.method === "DELETE").length, 0);
   elements["withdraw-confirm"].checked = true; await trigger("withdraw-confirm", "change");
   await trigger("withdraw-lead");
   const call = fetchCalls.find(x => x.method === "DELETE");
   assert.equal(call.path, "/support/conversations/own-session/leads");
   assert.deepEqual(JSON.parse(call.body), {confirm: true});
   assert.equal(call.headers["X-Session-Token"], "private-session-token");
   assert.equal(elements["withdraw-confirm"].checked, false);
 } else if (scenario === "delete") {
   await trigger("delete-session"); assert.equal(fetchCalls.filter(x => x.method === "DELETE").length, 0);
   elements["delete-confirm"].checked = true; await trigger("delete-confirm", "change");
   await trigger("delete-session");
   const call = fetchCalls.find(x => x.method === "DELETE");
   assert.equal(call.path, "/support/conversations/own-session");
   assert.deepEqual(JSON.parse(call.body), {confirm: true});
   assert.equal(call.headers["X-Session-Token"], "private-session-token");
   assert.equal(stored.has("sales-support-session"), false);
   assert.equal(elements.workspace.hidden, true); assert.equal(elements.notice.checked, false);
   assert.equal(elements["delete-confirm"].checked, false);
 } else if (scenario === "safe_error") {
   failures = true; await trigger("refresh");
   assert.match(elements.status.textContent, /暂不可用/);
   assert.ok(!elements.status.textContent.includes("private-key-and-customer-data"));
 } else if (scenario === "reset") {
   elements["delete-confirm"].checked = true; elements["withdraw-confirm"].checked = true;
   elements.consent.checked = true; await trigger("new-session");
   assert.equal(elements["delete-confirm"].checked, false); assert.equal(elements["withdraw-confirm"].checked, false);
   assert.equal(elements.consent.checked, false); assert.equal(stored.size, 0);
   assert.equal(fetchCalls.filter(x => x.method === "DELETE").length, 0);
 }
 assert.ok(!elements.status.textContent.includes("private-session-token"));
 assert.ok(fetchCalls.every(x => !x.headers.Authorization));
 assert.ok(fetchCalls.every(x => x.credentials === "same-origin" && x.redirect === "error"));
})().catch(error => { console.error(error.message); process.exitCode = 1; });
'''


@unittest.skipUnless(shutil.which("node"), "Node.js is required for optional JavaScript interaction tests")
class TestVisitorPrivacyV2(unittest.TestCase):
    def run_scenario(self, name):
        result = subprocess.run(["node", "-e", JS_HARNESS, str(ROOT / "web/customer.js"), name],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_closed_session_remains_readable_but_mutations_disabled(self):
        self.run_scenario("closed")

    def test_withdrawal_requires_explicit_confirmation_and_session_header(self):
        self.run_scenario("withdraw")

    def test_deletion_requires_confirmation_and_clears_session_storage(self):
        self.run_scenario("delete")

    def test_error_never_displays_server_detail(self):
        self.run_scenario("safe_error")

    def test_new_session_resets_privacy_consent_without_server_deletion(self):
        self.run_scenario("reset")


if __name__ == "__main__":
    unittest.main()
