"""Streamlit catalog interactions through FastAPI TestClient and isolated SQLite.

The in-process HTTP transport is explicit; no real model or network is used.
"""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from agent_skill.api_server import create_app
from customer_service.store import Store


class TestCatalogEditor(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = patch.dict(os.environ, {
            "ADMIN_API_TOKEN": "offline-catalog-admin-only", "MODEL_ENABLED": "false",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.path = Path(self.temp.name) / "catalog.db"
        self.store = Store(self.path)
        self.product = self.store.upsert_product(
            "p1", name="合成文档资料包", description="PDF 格式，仅供个人学习。", price_cents=1299,
            url="https://example.invalid/p1", active=False)
        self.other_product = self.store.upsert_product(
            "p2", name="合成表格资料包", description="XLSX 格式，兼容条件由人工确认。", price_cents=2999)
        self.document = self.store.upsert_knowledge(
            "k1", title="合成资料售后政策", content="文件无法打开时请联系人工。", kind="policy",
            product_id="p1", active=False)
        self.other_document = self.store.upsert_knowledge(
            "k2", title="合成常见问题", content="下载方式由人工确认。", kind="faq")
        self.client = TestClient(create_app(store=self.store))
        self.addCleanup(self.client.close)
        self.requests = []
        self.catalog_unavailable = False
        transport = patch("demo.support_admin.httpx.request", side_effect=self.request)
        transport.start()
        self.addCleanup(transport.stop)

    def request(self, method, url, **kwargs):
        path = httpx.URL(url).path
        self.requests.append((method, path, kwargs))
        if self.catalog_unavailable and method == "GET" and path == "/admin/products":
            return httpx.Response(503, json={}, request=httpx.Request(method, url))
        response = self.client.request(method, path, json=kwargs.get("json"), headers=kwargs.get("headers"))
        return httpx.Response(response.status_code, content=response.content, headers=response.headers,
                              request=httpx.Request(method, url))

    def app(self):
        app = AppTest.from_string(
            "from demo.support_admin import SupportAPI, _managed_catalog\n"
            "_managed_catalog(SupportAPI('http://testserver', 'offline-catalog-admin-only'), True)"
        ).run()
        self.assertFalse(app.exception, app.exception)
        return app

    @staticmethod
    def field(elements, label):
        return next(element for element in elements if element.label == label)

    def select(self, app, label, value):
        self.field(app.selectbox, label).set_value(value).run()
        self.assertFalse(app.exception, app.exception)

    def save(self, app, label):
        self.field(app.button, label).click().run()
        self.assertFalse(app.exception, app.exception)

    @staticmethod
    def without_updated_at(record):
        return {key: value for key, value in record.items() if key != "updated_at"}

    def test_product_prefill_single_field_edit_and_reopen_preserve_other_fields(self):
        app = self.app()
        self.select(app, "选择商品", "p1")
        self.assertTrue(self.field(app.text_input, "商品 ID").disabled)
        self.assertEqual(self.field(app.text_input, "商品名称").value, self.product["name"])
        self.assertEqual(self.field(app.text_area, "商品描述").value, self.product["description"])
        self.assertEqual(self.field(app.number_input, "价格（分，人民币）").value, 1299)
        self.assertEqual(self.field(app.text_input, "商品链接").value, self.product["url"])
        self.assertFalse(self.field(app.checkbox, "商品上架").value)

        self.field(app.text_input, "商品名称").set_value("合成文档资料包（新版）")
        self.save(app, "保存商品")

        expected = {**self.product, "name": "合成文档资料包（新版）"}
        stored = Store(self.path).get_product("p1")
        self.assertEqual(self.without_updated_at(stored), self.without_updated_at(expected))
        self.assertEqual(len(self.store.list_products()), 2)
        self.assertEqual(self.field(app.selectbox, "选择商品").value, "p1")
        self.assertEqual(self.field(app.text_input, "商品名称").value, expected["name"])
        catalog = self.client.get("/support/products").json()
        self.assertNotIn("p1", [item["id"] for item in catalog])
        reopened = self.app()
        self.select(reopened, "选择商品", "p1")
        self.assertEqual(self.field(reopened.text_input, "商品名称").value, expected["name"])

    def test_knowledge_prefill_update_preserves_kind_association_and_disabled_status(self):
        app = self.app()
        self.select(app, "选择知识", "k1")
        self.assertTrue(self.field(app.text_input, "知识 ID").disabled)
        self.assertEqual(self.field(app.text_input, "知识标题").value, self.document["title"])
        self.assertEqual(self.field(app.selectbox, "知识类型").value, "售后政策")
        self.assertEqual(self.field(app.text_input, "关联商品 ID（可选）").value, "p1")
        self.assertFalse(self.field(app.checkbox, "知识启用").value)
        self.field(app.text_area, "知识内容").set_value("文件打不开时由人工核对软件版本。")
        self.save(app, "保存知识")

        expected = {**self.document, "content": "文件打不开时由人工核对软件版本。"}
        stored = next(item for item in Store(self.path).list_knowledge() if item["id"] == "k1")
        self.assertEqual(self.without_updated_at(stored), self.without_updated_at(expected))
        self.assertEqual(self.field(app.selectbox, "选择知识").value, "k1")
        reopened = self.app()
        self.select(reopened, "选择知识", "k1")
        self.assertEqual(self.field(reopened.text_area, "知识内容").value, expected["content"])

    def test_switching_records_and_new_forms_does_not_mix_fields(self):
        app = self.app()
        self.select(app, "选择商品", "p1")
        self.field(app.text_area, "商品描述").set_value("未保存的草稿")
        self.select(app, "选择商品", "p2")
        self.assertEqual(self.field(app.text_input, "商品名称").value, self.other_product["name"])
        self.assertEqual(self.field(app.text_area, "商品描述").value, self.other_product["description"])
        self.assertEqual(self.field(app.number_input, "价格（分，人民币）").value, 2999)
        self.assertTrue(self.field(app.checkbox, "商品上架").value)
        self.select(app, "选择商品", None)
        self.assertEqual(self.field(app.text_input, "商品名称").value, "")
        self.assertEqual(self.field(app.number_input, "价格（分，人民币）").value, 0)
        self.assertFalse(self.field(app.text_input, "商品 ID").disabled)

        self.select(app, "选择知识", "k1")
        self.field(app.text_area, "知识内容").set_value("另一份未保存草稿")
        self.select(app, "选择知识", "k2")
        self.assertEqual(self.field(app.text_area, "知识内容").value, self.other_document["content"])
        self.assertEqual(self.field(app.selectbox, "知识类型").value, "常见问题")
        self.assertEqual(self.field(app.text_input, "关联商品 ID（可选）").value, "")
        self.assertTrue(self.field(app.checkbox, "知识启用").value)
        self.select(app, "选择知识", None)
        self.assertEqual(self.field(app.text_area, "知识内容").value, "")
        self.assertFalse(any(method == "POST" for method, _, _ in self.requests))

    def test_new_product_is_selected_after_save_and_repeat_save_updates_same_record(self):
        app = self.app()
        self.field(app.text_input, "商品名称").set_value("新建合成资料")
        self.field(app.text_area, "商品描述").set_value("仅用于验收的虚构内容。")
        self.field(app.number_input, "价格（分，人民币）").set_value(1234)
        self.save(app, "保存商品")
        selected_id = self.field(app.selectbox, "选择商品").value
        self.assertIsNotNone(selected_id)
        self.assertTrue(self.field(app.text_input, "商品 ID").disabled)
        self.save(app, "保存商品")
        self.assertEqual(len(self.store.list_products()), 3)
        self.assertEqual(self.store.get_product(selected_id)["price_cents"], 1234)

    def test_new_knowledge_is_selected_after_save_and_repeat_save_updates_same_record(self):
        app = self.app()
        self.field(app.text_input, "知识标题").set_value("新建合成 FAQ")
        self.field(app.text_area, "知识内容").set_value("适用条件由人工核对。")
        self.field(app.selectbox, "知识类型").set_value("常见问题")
        self.field(app.text_input, "关联商品 ID（可选）").set_value("p2")
        self.save(app, "保存知识")
        self.assertIsNotNone(self.field(app.selectbox, "选择知识").value)
        self.assertTrue(self.field(app.text_input, "知识 ID").disabled)
        self.save(app, "保存知识")
        self.assertEqual(len(self.store.list_knowledge()), 3)

    def test_new_form_cannot_overwrite_an_existing_id(self):
        app = self.app()
        self.field(app.text_input, "商品 ID").set_value("p1")
        self.field(app.text_input, "商品名称").set_value("不应覆盖的商品")
        self.field(app.text_area, "商品描述").set_value("不应覆盖的描述")
        self.save(app, "保存商品")
        self.field(app.text_input, "知识 ID").set_value("k1")
        self.field(app.text_input, "知识标题").set_value("不应覆盖的标题")
        self.field(app.text_area, "知识内容").set_value("不应覆盖的内容")
        self.save(app, "保存知识")
        self.assertFalse(any(method == "POST" for method, _, _ in self.requests))
        self.assertEqual(self.store.get_product("p1"), self.product)
        self.assertEqual(next(item for item in self.store.list_knowledge() if item["id"] == "k1"), self.document)

    def test_invalid_fields_and_backend_rejection_preserve_saved_records(self):
        app = self.app()
        self.select(app, "选择商品", "p1")
        self.field(app.text_input, "商品名称").set_value("")
        self.save(app, "保存商品")
        self.assertTrue(app.warning)
        self.assertFalse(any(method == "POST" for method, _, _ in self.requests))
        self.select(app, "选择知识", "k1")
        self.field(app.text_input, "关联商品 ID（可选）").set_value("missing-product")
        self.save(app, "保存知识")
        self.assertTrue(app.error)
        self.assertEqual(self.store.get_product("p1"), self.product)
        self.assertEqual(next(item for item in self.store.list_knowledge() if item["id"] == "k1"), self.document)
        self.assertEqual(self.field(app.text_input, "关联商品 ID（可选）").value, "missing-product")

    def test_failed_catalog_read_offers_no_save_form(self):
        self.catalog_unavailable = True
        app = self.app()
        self.assertTrue(app.error)
        self.assertFalse(app.button)
        self.assertFalse(any(method == "POST" for method, _, _ in self.requests))
