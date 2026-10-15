"""UI regression tests with isolated SQLite and explicit mocked HTTP calls."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from streamlit.testing.v1 import AppTest

from core import memory
from core.skill_engine import SkillEngine
from core.scorer import ScoreResult
from demo.support_admin import SupportAPI, SupportAPIError


APP = Path(__file__).resolve().parents[1] / "demo" / "app.py"


class TestSupportAPIClient(unittest.TestCase):
    def response(self, status=200, payload=None):
        return httpx.Response(status, json=payload if payload is not None else {},
                              request=httpx.Request("GET", "http://service/test"))

    def test_admin_auth_stays_in_header(self):
        with patch("demo.support_admin.httpx.request", return_value=self.response(payload=[])) as request:
            SupportAPI("http://service", "admin-secret").request("GET", "/admin/products", admin=True)
        self.assertEqual(request.call_args.kwargs["headers"], {"Authorization": "Bearer admin-secret"})
        self.assertFalse(request.call_args.kwargs["follow_redirects"])
        self.assertNotIn("admin-secret", request.call_args.args[1])

    def test_session_auth_does_not_send_admin_token(self):
        with patch("demo.support_admin.httpx.request", return_value=self.response() ) as request:
            SupportAPI("http://service", "admin-secret").request(
                "POST", "/support/conversations/id/messages", body={"message": "你好"}, session_token="session-secret")
        self.assertEqual(request.call_args.kwargs["headers"], {"X-Session-Token": "session-secret"})
        self.assertEqual(request.call_args.kwargs["json"], {"message": "你好"})

    def test_missing_admin_token_blocks_before_request(self):
        with patch("demo.support_admin.httpx.request") as request:
            with self.assertRaisesRegex(SupportAPIError, "BLOCKED"):
                SupportAPI("http://service").request("GET", "/admin/products", admin=True)
        request.assert_not_called()

    def test_safe_errors_for_authorization_unavailable_and_validation(self):
        for status in (401, 403, 404, 422, 503, 500, 302):
            with self.subTest(status=status):
                with patch("demo.support_admin.httpx.request", return_value=self.response(status, {"detail": "private upstream data"})):
                    with self.assertRaises(SupportAPIError) as caught:
                        SupportAPI("http://service").request("GET", "/test")
                self.assertNotIn("private upstream data", str(caught.exception))
                if status in (401, 403, 503, 500, 302):
                    self.assertIn("BLOCKED", str(caught.exception))

    def test_network_timeout_and_invalid_json_are_blocked(self):
        for error in (httpx.ConnectError("private upstream data"), httpx.ReadTimeout("private upstream data")):
            with self.subTest(error=type(error).__name__):
                with patch("demo.support_admin.httpx.request", side_effect=error):
                    with self.assertRaisesRegex(SupportAPIError, "BLOCKED") as caught:
                        SupportAPI("http://service").request("GET", "/test")
                self.assertNotIn("private upstream data", str(caught.exception))
        response = httpx.Response(200, text="not json", request=httpx.Request("GET", "http://service/test"))
        with patch("demo.support_admin.httpx.request", return_value=response):
            with self.assertRaisesRegex(SupportAPIError, "BLOCKED"):
                SupportAPI("http://service").request("GET", "/test")

    def test_invalid_or_embedded_credential_base_url_is_rejected(self):
        for url in ("file:///tmp/data", "http://user:secret@service", "service"):
            with self.subTest(url=url), self.assertRaises(SupportAPIError):
                SupportAPI(url)


class TestStreamlitRegressions(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(memory, "DB_PATH", str(Path(self.tempdir.name) / "ui.db"))
        self.db_patch.start()
        self.env_patch = patch.dict(os.environ, {"ADMIN_API_TOKEN": "admin-private", "SUPPORT_API_URL": "http://service"})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.db_patch.stop()
        self.tempdir.cleanup()

    @staticmethod
    def by_label(elements, label):
        return next(item for item in elements if item.label == label)

    @staticmethod
    def visible_text(app):
        return "\n".join(str(element.value) for group in
                         (app.markdown, app.caption, app.info, app.warning, app.error, app.success)
                         for element in group)

    def app(self):
        result = AppTest.from_file(str(APP), default_timeout=20).run()
        self.assertFalse(result.exception, result.exception)
        self.by_label(result.sidebar.text_input, "管理员访问令牌").set_value("admin-private").run()
        self.assertFalse(result.exception, result.exception)
        return result

    def test_filter_all_three_scenarios_to_single_hard(self):
        app = self.app()
        self.assertEqual(len(self.by_label(app.selectbox, "选择场景").options), 3)
        self.by_label(app.selectbox, "难度筛选").select("hard").run()
        self.assertFalse(app.exception, app.exception)
        self.assertEqual(len(self.by_label(app.selectbox, "选择场景").options), 1)
        self.assertEqual(self.by_label(app.selectbox, "选择场景").options, ["异议处理·高价产品价值转化"])

    def test_empty_filter_has_no_start_button_and_no_exception(self):
        app = self.app()
        with patch.object(SkillEngine, "list_available_scenarios", return_value=[]) as scenarios:
            self.by_label(app.selectbox, "难度筛选").select("hard").run()
        scenarios.assert_called_once_with("hard")
        self.assertFalse(app.exception, app.exception)
        self.assertIn("当前难度暂无可用场景", self.visible_text(app))
        self.assertFalse(any(button.label == "开始对话" for button in app.button))

    def test_training_start_failure_is_blocked_without_raw_error(self):
        app = self.app()
        with patch.object(SkillEngine, "start_roleplay", side_effect=RuntimeError("private-model-secret")):
            self.by_label(app.button, "开始对话").click().run()
        self.assertFalse(app.exception, app.exception)
        self.assertIn("BLOCKED", self.visible_text(app))
        self.assertNotIn("private-model-secret", self.visible_text(app))
        self.assertFalse(app.session_state["in_session"])

    def test_quiz_model_error_is_contained(self):
        app = self.app()
        with patch.object(SkillEngine, "generate_quiz", side_effect=RuntimeError("private-model-secret")):
            self.by_label(app.button, "AI 出题测验").click().run()
        self.assertFalse(app.exception, app.exception)
        self.assertIn("BLOCKED", self.visible_text(app))
        self.assertNotIn("private-model-secret", self.visible_text(app))

    def test_server_admin_env_does_not_authorize_browser(self):
        with patch("demo.support_admin.httpx.request") as request, patch.object(SkillEngine, "__init__", return_value=None) as init:
            app = AppTest.from_file(str(APP), default_timeout=20).run()
        self.assertFalse(app.exception, app.exception)
        self.assertIn("请输入管理员访问令牌", self.visible_text(app))
        self.assertFalse(app.sidebar.radio)
        self.assertFalse(app.tabs)
        self.assertFalse(any(item.label == "选择学员" for item in app.selectbox))
        self.assertNotIn("admin-private", self.visible_text(app))
        request.assert_not_called()
        init.assert_not_called()

    def test_invalid_browser_token_cannot_access_training_or_admin(self):
        with patch("demo.support_admin.httpx.request") as request, patch.object(SkillEngine, "__init__", return_value=None) as init:
            app = AppTest.from_file(str(APP), default_timeout=20).run()
            self.by_label(app.sidebar.text_input, "管理员访问令牌").set_value("wrong-private").run()
        self.assertFalse(app.exception, app.exception)
        self.assertIn("BLOCKED", self.visible_text(app))
        self.assertFalse(app.sidebar.radio)
        self.assertFalse(app.tabs)
        request.assert_not_called()
        init.assert_not_called()

    def test_unconfigured_server_admin_token_is_blocked(self):
        with patch.dict(os.environ, {"ADMIN_API_TOKEN": ""}), patch("demo.support_admin.httpx.request") as request:
            app = AppTest.from_file(str(APP), default_timeout=20).run()
        self.assertFalse(app.exception, app.exception)
        self.assertIn("BLOCKED", self.visible_text(app))
        self.assertFalse(app.sidebar.radio)
        request.assert_not_called()

    def mock_backend(self, method, url, **kwargs):
        path = httpx.URL(url).path
        if path == "/support/conversations" and method == "POST":
            payload = {"id": "conversation-1", "session_token": "session-private", "status": "active", "privacy_notice": "客服消息会保存。"}
        elif path == "/support/conversations/conversation-1":
            payload = {"id": "conversation-1", "status": "active", "messages": []}
        elif path == "/admin/model/status":
            payload = {"provider": "deepseek", "model": "configured-model", "status": "BLOCKED"}
        elif path == "/admin/model/usage":
            payload = {"total_calls": 0, "estimated_cost": None}
        elif method == "POST":
            payload = {"id": "saved"}
        else:
            payload = []
        return httpx.Response(200, json=payload, request=httpx.Request(method, url))

    def test_admin_workbench_has_unknown_cost_and_hides_auth(self):
        app = self.app()
        with patch.dict(os.environ, {"ADMIN_API_TOKEN": "admin-private"}), patch("demo.support_admin.httpx.request", side_effect=self.mock_backend):
            self.by_label(app.sidebar.radio, "工作区").set_value("客服工作台").run()
        self.assertFalse(app.exception, app.exception)
        text = self.visible_text(app)
        self.assertIn("调用成本：未知", text)
        self.assertNotIn("admin-private", text)


    def test_ollama_status_and_partial_cost_use_configuration_currency(self):
        app = self.app()
        def backend(method, url, **kwargs):
            path = httpx.URL(url).path
            if path == "/admin/model/status":
                payload = {"provider": "deepseek", "status": "UNVERIFIED", "local_provider": {
                    "provider": "ollama", "model": None, "status": "BLOCKED", "configured": False, "available": False}}
            elif path == "/admin/model/usage":
                payload = {"total_calls": 2, "estimated_cost": 1.25, "unknown_cost_calls": 1, "currency": "configured"}
            else:
                return self.mock_backend(method, url, **kwargs)
            return httpx.Response(200, json=payload, request=httpx.Request(method, url))
        with patch("demo.support_admin.httpx.request", side_effect=backend):
            self.by_label(app.sidebar.radio, "工作区").set_value("客服工作台").run()
        self.assertFalse(app.exception, app.exception)
        self.assertIn("Ollama：不可用", self.visible_text(app))
        self.assertIn("部分调用成本未知", self.visible_text(app))
        self.assertTrue(any(metric.label == "已知调用成本估算（配置单价币种）" for metric in app.metric))

    def test_lead_without_consent_never_reaches_backend(self):
        app = self.app()
        with patch("demo.support_admin.httpx.request", side_effect=self.mock_backend) as request:
            self.by_label(app.sidebar.radio, "工作区").set_value("客服工作台").run()
            self.by_label(app.button, "创建新的测试会话").click().run()
            self.by_label(app.button, "保存客户线索").click().run()
        self.assertFalse(app.exception, app.exception)
        self.assertIn("未取得同意", self.visible_text(app))
        self.assertFalse(any(call.args[0] == "POST" and call.args[1].endswith("/leads") for call in request.call_args_list))
        self.assertNotIn("session-private", self.visible_text(app))


    def test_new_session_resets_previous_consent_and_contact(self):
        app = self.app()
        with patch("demo.support_admin.httpx.request", side_effect=self.mock_backend):
            self.by_label(app.sidebar.radio, "工作区").set_value("客服工作台").run()
            self.by_label(app.button, "创建新的测试会话").click().run()
            self.by_label(app.checkbox, "我已了解用途并同意保存以下联系信息和需求").check()
            self.by_label(app.text_input, "联系方式（手机号或邮箱）").set_value("previous@example.test")
            app.run()
            self.by_label(app.button, "创建新的测试会话").click().run()
        self.assertFalse(app.exception, app.exception)
        self.assertFalse(self.by_label(app.checkbox, "我已了解用途并同意保存以下联系信息和需求").value)
        self.assertEqual(self.by_label(app.text_input, "联系方式（手机号或邮箱）").value, "")

    def test_consented_lead_posts_session_header(self):
        app = self.app()
        with patch("demo.support_admin.httpx.request", side_effect=self.mock_backend) as request:
            self.by_label(app.sidebar.radio, "工作区").set_value("客服工作台").run()
            self.by_label(app.button, "创建新的测试会话").click().run()
            self.by_label(app.checkbox, "我已了解用途并同意保存以下联系信息和需求").check()
            self.by_label(app.text_input, "称呼（可选）").set_value("测试访客")
            self.by_label(app.text_area, "需要人工跟进的需求").set_value("办公商品咨询")
            self.by_label(app.text_input, "联系方式（手机号或邮箱）").set_value("demo@example.test")
            self.by_label(app.button, "保存客户线索").click().run()
        self.assertFalse(app.exception, app.exception)
        lead_call = next(call for call in request.call_args_list if call.args[0] == "POST" and call.args[1].endswith("/leads"))
        self.assertTrue(lead_call.kwargs["json"]["consent"])
        self.assertEqual(lead_call.kwargs["headers"], {"X-Session-Token": "session-private"})


    def test_forced_training_score_persists_and_names_model_source(self):
        app = self.app()
        app.session_state["in_session"] = True
        app.session_state["chat_history"] = [{"role": "customer", "content": "测试开场"}]
        app.run()
        result = ScoreResult(total_score=80, dimensions={"开场": 16}, score_source="model", score_status="valid")
        with patch.object(SkillEngine, "force_end_session", return_value=result):
            self.by_label(app.button, "结束对话（评分）").click().run()
        self.assertFalse(app.exception, app.exception)
        self.assertFalse(app.session_state["in_session"])
        self.assertIn("模型评分", self.visible_text(app))
        self.assertTrue(any(metric.value == "80/100" for metric in app.metric))
        app.run()
        self.assertIn("模型评分", self.visible_text(app))

    def test_rule_score_is_clearly_labelled(self):
        app = self.app()
        app.session_state["last_score"] = ScoreResult(total_score=0, score_source="rule", score_status="valid")
        app.run()
        self.assertFalse(app.exception, app.exception)
        self.assertIn("规则评分", self.visible_text(app))
        self.assertIn("不能作为真实模型评分验收", self.visible_text(app))

    def test_training_score_error_keeps_transcript_and_active_page(self):
        app = self.app()
        app.session_state["in_session"] = True
        app.session_state["chat_history"] = [{"role": "customer", "content": "保留的对话"}]
        app.run()
        with patch.object(SkillEngine, "force_end_session", side_effect=RuntimeError("private-error")):
            self.by_label(app.button, "结束对话（评分）").click().run()
        self.assertFalse(app.exception, app.exception)
        self.assertTrue(app.session_state["in_session"])
        self.assertIn("BLOCKED", self.visible_text(app))
        self.assertIn("保留的对话", self.visible_text(app))
        self.assertNotIn("private-error", self.visible_text(app))

    def test_product_save_uses_managed_integer_price(self):
        app = self.app()
        with patch.dict(os.environ, {"ADMIN_API_TOKEN": "admin-private"}), patch("demo.support_admin.httpx.request", side_effect=self.mock_backend) as request:
            self.by_label(app.sidebar.radio, "工作区").set_value("客服工作台").run()
            self.by_label(app.text_input, "商品名称").set_value("测试办公灯")
            self.by_label(app.text_area, "商品描述").set_value("仅用于自动化测试")
            self.by_label(app.number_input, "价格（分，人民币）").set_value(12900)
            self.by_label(app.button, "保存商品").click().run()
        self.assertFalse(app.exception, app.exception)
        call = next(call for call in request.call_args_list if call.args[0] == "POST" and call.args[1].endswith("/admin/products"))
        self.assertEqual(call.kwargs["json"]["price_cents"], 12900)
        self.assertIs(type(call.kwargs["json"]["price_cents"]), int)
        self.assertEqual(call.kwargs["json"]["currency"], "CNY")
        self.assertEqual(call.kwargs["headers"], {"Authorization": "Bearer admin-private"})

    def test_chat_distinguishes_mock_and_blocked_from_real_success(self):
        for model_status, source in (("MOCK", "mock_selection"), ("BLOCKED", "human_handoff")):
            with self.subTest(model_status=model_status):
                app = self.app()
                def backend(method, url, **kwargs):
                    if httpx.URL(url).path.endswith("/messages"):
                        return httpx.Response(200, json={"reply": "测试回复", "model_status": model_status, "source": source,
                            "products": [], "citations": []}, request=httpx.Request(method, url))
                    return self.mock_backend(method, url, **kwargs)
                with patch("demo.support_admin.httpx.request", side_effect=backend):
                    self.by_label(app.sidebar.radio, "工作区").set_value("客服工作台").run()
                    self.by_label(app.button, "创建新的测试会话").click().run()
                    self.by_label(app.text_area, "客户消息").set_value("推荐办公用品")
                    self.by_label(app.button, "发送客服消息").click().run()
                self.assertFalse(app.exception, app.exception)
                self.assertIn(model_status, self.visible_text(app))
                self.assertTrue(any(model_status in element.value for element in app.warning))

    def test_manual_handoff_reply_uses_admin_endpoint(self):
        app = self.app()
        def backend(method, url, **kwargs):
            if method == "GET" and httpx.URL(url).path == "/admin/handoffs":
                return httpx.Response(200, json=[{"id": "handoff-1", "conversation_id": "conversation-1", "status": "pending", "reason": "complaint"}],
                    request=httpx.Request(method, url))
            return self.mock_backend(method, url, **kwargs)
        with patch.dict(os.environ, {"ADMIN_API_TOKEN": "admin-private"}), patch("demo.support_admin.httpx.request", side_effect=backend) as request:
            self.by_label(app.sidebar.radio, "工作区").set_value("客服工作台").run()
            self.by_label(app.text_area, "人工回复").set_value("人工客服已接到您的咨询。")
            self.by_label(app.button, "回复并完成接管").click().run()
        self.assertFalse(app.exception, app.exception)
        call = next(call for call in request.call_args_list if call.args[1].endswith("/handoffs/handoff-1/resolve"))
        self.assertEqual(call.kwargs["headers"], {"Authorization": "Bearer admin-private"})
        self.assertEqual(call.kwargs["json"], {"reply": "人工客服已接到您的咨询。"})


if __name__ == "__main__":
    unittest.main()
