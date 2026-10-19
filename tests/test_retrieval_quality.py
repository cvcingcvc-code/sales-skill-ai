"""Deterministic retrieval and product-policy matching; model selections are mocks."""

import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from customer_service import CustomerService, Store


class SelectionGateway:
    def __init__(self):
        self.selection = {"product_ids": [], "knowledge_ids": [], "needs_human": False}
        self.on_chat = None
        self.calls = []

    def chat(self, messages, system="", model=None):
        self.calls.append(json.loads(messages[0]["content"]))
        if self.on_chat:
            self.on_chat()
        return SimpleNamespace(text=json.dumps(self.selection), provider="mock", model="offline-retrieval",
                               prompt_tokens=20, completion_tokens=10, estimated_cost=None)


class NoopBudget:
    """No live calls or billing; budget behavior has a separate regression suite."""
    def reserve(self, *_args):
        return "offline-reservation"

    def complete(self, *_args, **_kwargs):
        pass


class TestRetrievalQuality(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Path(self.directory.name) / "support.db")
        self.store.upsert_product("p1", name="轻享耳机", description="轻便无线耳机，适合通勤", price_cents=19900)
        self.store.upsert_product("p2", name="静享耳机", description="降噪无线耳机，适合通勤", price_cents=39900)
        self.gateway = SelectionGateway()
        self.service = CustomerService(self.store, self.gateway, NoopBudget())
        self.session = self.service.create_conversation()

    def knowledge(self, kid, title, content, product_id="p1", active=True):
        return self.store.upsert_knowledge(kid, title=title, content=content, kind="policy",
                                          product_id=product_id, active=active)

    def chat(self, message, product_ids=(), knowledge_ids=()):
        self.gateway.selection = {"product_ids": list(product_ids), "knowledge_ids": list(knowledge_ids), "needs_human": False}
        return self.service.chat(self.session["id"], self.session["session_token"], message)

    def add_overflow_documents(self):
        for i in range(8):
            self.knowledge(f"noise-{i}", f"A{i} 轻享耳机包装与耳机说明", "轻享耳机使用原厂纸盒，随附耳机收纳袋。")
        self.knowledge("waterproof", "Z 防水等级", "防水等级为 IPX4，避免浸泡。")

    def test_relevant_ninth_document_survives_eight_document_limit(self):
        self.add_overflow_documents()
        _, knowledge = self.service._retrieve("轻享耳机的防水等级是多少？", [])
        self.assertEqual(knowledge[0]["id"], "waterproof")
        self.assertLessEqual(len(knowledge), 8)

    def test_shared_product_name_terms_do_not_beat_question_terms(self):
        self.add_overflow_documents()
        result = self.chat("轻享耳机的防水等级是多少？", knowledge_ids=["waterproof"])
        self.assertIsNone(result["handoff"])
        self.assertIn("IPX4", result["reply"])

    def test_knowledge_order_is_stable_for_equal_relevance(self):
        self.knowledge("b", "B 保修期限", "保修一年。")
        self.knowledge("a", "A 保修期限", "保修一年。")
        first = self.service._retrieve("轻享耳机保修期限", [])[1]
        second = self.service._retrieve("轻享耳机保修期限", [])[1]
        self.assertEqual([k["id"] for k in first], [k["id"] for k in second])

    def test_explicit_model_excludes_other_models_policy_candidates(self):
        self.knowledge("p1-warranty", "保修政策", "保修一年。")
        self.knowledge("p2-warranty", "保修政策", "保修三年。", "p2")
        _, knowledge = self.service._retrieve("轻享耳机保修政策是什么？", [])
        self.assertEqual([k["id"] for k in knowledge], ["p1-warranty"])

    def test_named_model_cannot_receive_another_models_warranty(self):
        self.knowledge("p1-warranty", "保修政策", "保修一年。")
        self.knowledge("p2-warranty", "保修政策", "保修三年。", "p2")
        result = self.chat("轻享耳机的保修政策是什么？", ["p1"], ["p2-warranty"])
        self.assertIsNotNone(result["handoff"])
        self.assertNotIn("保修三年", result["reply"])
        self.assertEqual(result["citations"], [])

    def test_selected_product_and_policy_must_match_without_named_model(self):
        self.knowledge("p2-warranty", "保修政策", "保修三年。", "p2")
        result = self.chat("推荐耳机并说明保修政策", ["p1"], ["p2-warranty"])
        self.assertIsNotNone(result["handoff"])
        self.assertNotIn("保修三年", result["reply"])

    def test_valid_knowledge_only_answer_identifies_real_associated_product(self):
        self.knowledge("p1-warranty", "保修政策", "保修一年。")
        result = self.chat("轻享耳机保修多久？", knowledge_ids=["p1-warranty"])
        self.assertIsNone(result["handoff"])
        self.assertEqual(result["products"], [])
        self.assertEqual(result["citations"][0]["product_name"], "轻享耳机")
        self.assertIn("适用商品：轻享耳机", result["reply"])

    def test_two_product_comparison_keeps_each_policy_label(self):
        self.knowledge("p1-warranty", "保修政策", "保修一年。")
        self.knowledge("p2-warranty", "保修政策", "保修三年。", "p2")
        result = self.chat("比较轻享耳机和静享耳机的保修政策", ["p1", "p2"], ["p1-warranty", "p2-warranty"])
        self.assertIsNone(result["handoff"])
        self.assertIn("适用商品：轻享耳机", result["reply"])
        self.assertIn("适用商品：静享耳机", result["reply"])

    def test_two_product_knowledge_only_comparison_remains_valid(self):
        self.knowledge("p1-warranty", "保修政策", "保修一年。")
        self.knowledge("p2-warranty", "保修政策", "保修三年。", "p2")
        result = self.chat("比较轻享耳机和静享耳机的保修政策", knowledge_ids=["p1-warranty", "p2-warranty"])
        self.assertIsNone(result["handoff"])
        self.assertEqual(len(result["citations"]), 2)

    def test_general_policy_can_answer_specific_product_question(self):
        self.knowledge("shipping", "发货政策", "通常两个工作日发货。", product_id=None)
        result = self.chat("轻享耳机什么时候发货？", ["p1"], ["shipping"])
        self.assertIsNone(result["handoff"])
        self.assertNotIn("product_name", result["citations"][0])

    def test_general_knowledge_only_answer_remains_valid(self):
        self.knowledge("shipping", "发货政策", "通常两个工作日发货。", product_id=None)
        result = self.chat("什么时候发货？", knowledge_ids=["shipping"])
        self.assertIsNone(result["handoff"])
        self.assertIn("两个工作日", result["reply"])

    def test_policy_relinked_during_inference_cannot_change_consulted_model(self):
        self.knowledge("warranty", "保修政策", "保修一年。")
        self.gateway.on_chat = lambda: self.knowledge("warranty", "保修政策", "保修三年。", "p2")
        result = self.chat("轻享耳机保修多久？", knowledge_ids=["warranty"])
        self.assertIsNotNone(result["handoff"])
        self.assertNotIn("保修三年", result["reply"])

    def test_pronoun_uses_latest_named_model_instead_of_all_old_models(self):
        self.knowledge("p1-warranty", "保修政策", "保修一年。")
        self.knowledge("p2-warranty", "保修政策", "保修三年。", "p2")
        history = [{"role": "user", "content": "轻享耳机介绍"},
                   {"role": "assistant", "content": "轻享耳机的说明"},
                   {"role": "user", "content": "接着了解静享耳机"}]
        _, knowledge = self.service._retrieve("它的保修多久？", history)
        self.assertEqual([k["id"] for k in knowledge], ["p2-warranty"])

    def test_current_explicit_model_takes_priority_over_pronoun_history(self):
        self.knowledge("p1-warranty", "保修政策", "保修一年。")
        self.knowledge("p2-warranty", "保修政策", "保修三年。", "p2")
        history = [{"role": "user", "content": "轻享耳机介绍"}]
        _, knowledge = self.service._retrieve("这个静享耳机保修多久？", history)
        self.assertEqual([k["id"] for k in knowledge], ["p2-warranty"])

    def test_product_id_prefix_is_not_an_explicit_match(self):
        self.store.upsert_product("p10", name="远航耳机", description="旅行专用耳机", price_cents=50000)
        self.knowledge("p1-warranty", "保修政策", "保修一年。")
        self.knowledge("p10-warranty", "保修政策", "保修五年。", "p10")
        _, knowledge = self.service._retrieve("P10的保修政策是什么？", [])
        self.assertEqual([k["id"] for k in knowledge], ["p10-warranty"])

    def test_longer_product_name_does_not_implicitly_name_base_model(self):
        self.store.upsert_product("pro", name="轻享耳机Pro", description="专业版耳机", price_cents=59900)
        self.knowledge("p1-warranty", "保修政策", "保修一年。")
        self.knowledge("pro-warranty", "保修政策", "保修五年。", "pro")
        _, knowledge = self.service._retrieve("轻享耳机Pro的保修政策", [])
        self.assertEqual([k["id"] for k in knowledge], ["pro-warranty"])

    def test_legitimate_base_and_longer_name_comparison_keeps_both(self):
        self.store.upsert_product("pro", name="轻享耳机Pro", description="专业版耳机", price_cents=59900)
        self.knowledge("p1-warranty", "保修政策", "保修一年。")
        self.knowledge("pro-warranty", "保修政策", "保修五年。", "pro")
        result = self.chat("比较轻享耳机和轻享耳机Pro的保修政策", knowledge_ids=["p1-warranty", "pro-warranty"])
        self.assertIsNone(result["handoff"])
        self.assertEqual(len(result["citations"]), 2)

    def test_disabled_or_unpublished_knowledge_remains_excluded(self):
        self.knowledge("disabled", "防水等级", "IPX8", active=False)
        self.store.upsert_product("off", name="下架耳机", description="旧款", price_cents=100, active=False)
        self.knowledge("unpublished", "防水等级", "IPX8", "off")
        _, knowledge = self.service._retrieve("耳机的防水等级", [])
        self.assertEqual(knowledge, [])


if __name__ == "__main__":
    unittest.main()
