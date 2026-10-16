/* Same-origin visitor client: no administrator credentials or model access. */
"use strict";
const byId = (id) => document.getElementById(id);
const storageKey = "sales-support-session";
let session = null;
let conversationState = null;
let busy = false;
try {
  const saved = JSON.parse(sessionStorage.getItem(storageKey) || "null");
  if (typeof saved?.id === "string" && typeof saved?.session_token === "string" && saved.id && saved.session_token) {
    session = {id: saved.id, session_token: saved.session_token};
  }
} catch { session = null; }
class VisitorAPIError extends Error {}
function status(message) { byId("status").textContent = message; }
function conversationPath() {
  if (!session) throw new VisitorAPIError("请先开始本次咨询。");
  return `/support/conversations/${encodeURIComponent(session.id)}`;
}
async function request(path, body, method) {
  const headers = {"Content-Type": "application/json"};
  if (session) headers["X-Session-Token"] = session.session_token;
  let response;
  try {
    response = await fetch(path, {method: method || (body === undefined ? "GET" : "POST"), headers,
      body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin", cache: "no-store",
      redirect: "error"});
  } catch { throw new VisitorAPIError("客服服务暂不可用，请稍后重试。"); }
  // Never display raw server errors, which may contain personal data or credentials.
  if (!response.ok) {
    const errors = {
      400: "请求未保存，请检查输入内容。", 401: "本次会话凭证无效或已过期，请重新开始咨询。",
      403: "本次会话凭证无效或已过期，请重新开始咨询。", 404: "本次会话不存在或已删除。",
      409: "会话状态不允许此操作，请刷新记录。", 422: "请求未保存，请检查必填字段与确认选项。",
      429: "请求过于频繁或已达到服务额度，请稍后重试或联系人工。",
      502: "模型或客服服务暂不可用，请稍后重试。", 503: "模型或客服服务暂不可用，请稍后重试。",
      504: "客服服务请求超时，请稍后重试。"
    };
    throw new VisitorAPIError(errors[response.status] || "请求未完成，请稍后重试或联系人工客服。");
  }
  try { return await response.json(); }
  catch { throw new VisitorAPIError("客服服务返回了无效数据，请稍后重试。"); }
}
function applyState() {
  document.querySelectorAll("button").forEach((button) => { button.disabled = busy; });
  const closed = conversationState === "closed";
  for (const element of document.querySelectorAll("#chat-form textarea, #chat-form button[type='submit'], #lead-form input, #lead-form textarea, #lead-form button")) {
    element.disabled = busy || closed || !session;
  }
  byId("handoff").disabled = busy || closed || !session;
  byId("close-session").disabled = busy || closed || !session;
  byId("withdraw-lead").disabled = busy || !session || !byId("withdraw-confirm").checked;
  byId("delete-session").disabled = busy || !session || !byId("delete-confirm").checked;
  byId("withdraw-confirm").disabled = busy || !session;
  byId("delete-confirm").disabled = busy || !session;
}
function showWorkspace() { byId("welcome").hidden = true; byId("workspace").hidden = false; }
async function refresh() {
  if (!session) return;
  const data = await request(conversationPath());
  conversationState = data.status;
  const area = byId("messages");
  area.replaceChildren();
  for (const message of data.messages || []) {
    const item = document.createElement("article");
    const roles = {user: "您", assistant: "客服", human: "人工客服", agent: "人工客服"};
    item.textContent = `${roles[message.role] || "客服"}：\n${message.content}`;
    area.appendChild(item);
  }
  if (data.status === "human") status("本会话已转人工，AI 回复暂停。可继续留言或查看人工回复。");
  if (data.status === "closed") status("本次会话已关闭，不能继续发送消息、留资或转人工。您仍可查看、撤回线索或删除会话。");
  applyState();
}
async function guarded(action) {
  if (busy) return;
  busy = true; applyState();
  try { await action(); }
  catch (error) { status(error instanceof VisitorAPIError ? error.message : "服务暂不可用，请稍后重试。"); }
  finally { busy = false; applyState(); }
}
function clearLocalSession() {
  sessionStorage.removeItem(storageKey);
  session = null; conversationState = null;
  byId("welcome").hidden = false;
  byId("workspace").hidden = true;
  byId("notice").checked = false;
  byId("lead-form").reset();
  byId("withdraw-confirm").checked = false;
  byId("delete-confirm").checked = false;
  byId("message").value = "";
  byId("messages").replaceChildren();
  applyState();
}
byId("start").addEventListener("click", () => guarded(async () => {
  if (!byId("notice").checked) { status("请先阅读并确认对话保存告知。"); return; }
  const created = await request("/support/conversations", {});
  if (typeof created.id !== "string" || typeof created.session_token !== "string" || !created.id || !created.session_token) {
    throw new VisitorAPIError("无法建立客服会话，请稍后重试。");
  }
  session = {id: created.id, session_token: created.session_token};
  sessionStorage.setItem(storageKey, JSON.stringify(session));
  showWorkspace(); status("已开始咨询。联系方式请仅通过下方同意表单提交。"); await refresh();
}));
byId("chat-form").addEventListener("submit", (event) => {
  event.preventDefault(); guarded(async () => {
    if (conversationState === "closed") { status("本次会话已关闭，请开始新会话。"); return; }
    const result = await request(conversationPath() + "/messages", {message: byId("message").value});
    byId("message").value = ""; await refresh();
    if (result.model_status === "BLOCKED") status("模型调用 BLOCKED，本次没有生成模型回答，已转人工。");
    else if (result.model_status === "DISABLED") status("模型已主动暂停，本次未连接模型；可查询管理资料或申请人工协助。");
    else if (result.handoff) status("已进入人工接管队列，可继续留言。");
    else status(result.model_status === "NOT_CALLED" ? "本次为规则提示，未调用模型。" : "答复引用已管理的商品与知识资料。");
  });
});
byId("handoff").addEventListener("click", () => guarded(async () => {
  if (conversationState === "closed") return;
  await request(conversationPath() + "/handoff", {reason: "customer_request"});
  await refresh(); status("已申请人工接管，可继续留言。");
}));
byId("refresh").addEventListener("click", () => guarded(refresh));
byId("new-session").addEventListener("click", () => {
  clearLocalSession();
  status("新会话需重新确认告知；历史对话仍由客服按保留策略管理。开始新会话不会删除历史记录。");
});
byId("lead-form").addEventListener("submit", (event) => {
  event.preventDefault(); guarded(async () => {
    if (conversationState === "closed") return;
    if (!byId("consent").checked) { status("未同意，不保存联系线索。"); return; }
    await request(conversationPath() + "/leads", {
      consent: true, name: byId("lead-name").value, contact: byId("contact").value, need: byId("need").value});
    byId("lead-form").reset(); status("联系线索已在您的明确同意下保存，供人工客服后续联系。");
  });
});
byId("close-session").addEventListener("click", () => guarded(async () => {
  await request(conversationPath() + "/close", {});
  await refresh();
}));
byId("withdraw-confirm").addEventListener("change", applyState);
byId("delete-confirm").addEventListener("change", applyState);
byId("withdraw-lead").addEventListener("click", () => guarded(async () => {
  if (!byId("withdraw-confirm").checked) { status("请先明确确认撤回联系线索。"); return; }
  await request(conversationPath() + "/leads", {confirm: true}, "DELETE");
  byId("lead-form").reset(); byId("withdraw-confirm").checked = false;
  status("本次会话的联系线索和联系同意已撤回，消息仍按保留策略管理。");
}));
byId("delete-session").addEventListener("click", () => guarded(async () => {
  if (!byId("delete-confirm").checked) { status("请先明确确认永久删除本次会话。"); return; }
  await request(conversationPath(), {confirm: true}, "DELETE");
  clearLocalSession();
  status("本次会话及关联数据已永久删除；本浏览器中的会话凭证已清除。");
}));
applyState();
if (session?.id && session?.session_token) { showWorkspace(); guarded(refresh); }
