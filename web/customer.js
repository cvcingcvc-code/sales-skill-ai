/* Same-origin visitor client: no administrator credentials or model access. */
"use strict";
const byId = (id) => document.getElementById(id);
let session = null;
try { session = JSON.parse(sessionStorage.getItem("sales-support-session") || "null"); } catch { session = null; }
function status(message) { byId("status").textContent = message; }
async function request(path, body) {
  const headers = {"Content-Type": "application/json"};
  if (session) headers["X-Session-Token"] = session.session_token;
  const response = await fetch(path, {method: body === undefined ? "GET" : "POST", headers,
    body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin", cache: "no-store"});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail?.message || "请求未完成，请检查输入或联系人工客服。");
  return data;
}
function showWorkspace() { byId("welcome").hidden = true; byId("workspace").hidden = false; }
async function refresh() {
  if (!session) return;
  const data = await request(`/support/conversations/${encodeURIComponent(session.id)}`);
  const area = byId("messages");
  area.replaceChildren();
  for (const message of data.messages || []) {
    const item = document.createElement("article");
    const roles = {user: "您", assistant: "客服", human: "人工客服", agent: "人工客服"};
    item.textContent = `${roles[message.role] || "客服"}：\n${message.content}`;
    area.appendChild(item);
  }
  if (data.status === "human") status("本会话已转人工，AI 回复暂停。可继续留言或查看人工回复。");
}
async function guarded(action) {
  const buttons = document.querySelectorAll("button");
  buttons.forEach((button) => { button.disabled = true; });
  try { await action(); } catch (error) { status(error.message || "服务暂不可用，请稍后重试。"); }
  finally { buttons.forEach((button) => { button.disabled = false; }); }
}
byId("start").addEventListener("click", () => guarded(async () => {
  if (!byId("notice").checked) { status("请先阅读并确认对话保存告知。"); return; }
  session = await request("/support/conversations", {});
  sessionStorage.setItem("sales-support-session", JSON.stringify({id: session.id, session_token: session.session_token}));
  showWorkspace(); status("已开始咨询。联系方式请仅通过下方同意表单提交。"); await refresh();
}));
byId("chat-form").addEventListener("submit", (event) => {
  event.preventDefault(); guarded(async () => {
    const result = await request(`/support/conversations/${encodeURIComponent(session.id)}/messages`, {message: byId("message").value});
    byId("message").value = ""; await refresh();
    if (result.model_status === "BLOCKED") status("模型调用 BLOCKED，本次没有生成模型回答，已转人工。");
    else if (result.handoff) status("已进入人工接管队列，可继续留言。");
    else status(result.model_status === "NOT_CALLED" ? "本次为规则提示，未调用模型。" : "答复引用已管理的商品与知识资料。");
  });
});
byId("handoff").addEventListener("click", () => guarded(async () => {
  await request(`/support/conversations/${encodeURIComponent(session.id)}/handoff`, {reason: "customer_request"});
  await refresh(); status("已申请人工接管，可继续留言。");
}));
byId("refresh").addEventListener("click", () => guarded(refresh));
byId("new-session").addEventListener("click", () => {
  sessionStorage.removeItem("sales-support-session");
  session = null;
  byId("welcome").hidden = false;
  byId("workspace").hidden = true;
  byId("notice").checked = false;
  byId("lead-form").reset();
  byId("message").value = "";
  status("新会话需重新确认告知；历史对话仍由客服按保留策略管理。");
});
byId("lead-form").addEventListener("submit", (event) => {
  event.preventDefault(); guarded(async () => {
    if (!byId("consent").checked) { status("未同意，不保存联系线索。"); return; }
    await request(`/support/conversations/${encodeURIComponent(session.id)}/leads`, {
      consent: true, name: byId("lead-name").value, contact: byId("contact").value, need: byId("need").value});
    byId("lead-form").reset(); status("联系线索已在您的明确同意下保存，供人工客服后续联系。");
  });
});
if (session?.id && session?.session_token) { showWorkspace(); guarded(refresh); }
