import type { Adapter, Snapshot, Output } from "../types";

const example: Output = {
  context_version: "demo-v1",
  pipeline_mode: "jev-dynamic",
  request_id: "demo-request",
  subtext_hypotheses: [
    {
      id: "h1",
      hypothesis: "想暂时换个话题",
      status: "judged",
      support_probability: 0.92,
      evidence_status: "sufficient",
      evidence_refs: [
        { source_type: "message", source_id: "m003", quote: "先不聊工作了" },
      ],
      missing_context: [],
    },
    {
      id: "h2",
      hypothesis: "可能也想被安慰",
      status: "needs_review",
      support_probability: 0.46,
      evidence_status: "insufficient",
      evidence_refs: [
        { source_type: "message", source_id: "m001", quote: "有点累" },
      ],
      missing_context: ["尚不清楚对方现在更想聊天还是休息"],
    },
  ],
  dynamic_analysis: {
    stages: {
      propose: { status: "complete" },
      judge: { status: "complete" },
      reply: { status: "complete" },
    },
  },
  result: {
    analysis: {
      emotion: "复杂或不明",
      intent: "结束话题",
      literal: "对方明确表示先不聊工作。",
      subtext: "可能希望把话题放轻一点。",
      history_gap: "未提供更早的聊天原文。",
    },
    scorecard: {
      emotion: { label: "复杂或不明", score: 61, reason: "Jev 情绪 Choice 题的所选类别概率" },
      intent: { label: "结束话题", score: 78, reason: "Jev 意图 Choice 题的所选类别概率" },
    },
    candidates: [
      {
        style: "温和关心",
        text: "好，那先不聊工作。想聊点别的，还是安静待一会儿？",
      },
      { style: "自然短句", text: "好，不聊工作了。想聊点什么？" },
    ],
    needs_user_review: true,
    action: "reply",
  },
  jev: {
    answers: {
      emotion: {
        choice: "复杂或不明",
        probabilities: {
          "平静或中性": 0.28,
          开心: 0.02,
          难过: 0.08,
          焦虑: 0.01,
          生气: 0,
          "复杂或不明": 0.61,
        },
      },
      intent: {
        choice: "结束话题",
        probabilities: {
          提问: 0.02,
          请求帮助: 0,
          邀约: 0,
          倾诉: 0.01,
          确认: 0.04,
          拒绝: 0.04,
          结束话题: 0.78,
          信息分享: 0.02,
          "其他或不明": 0.09,
        },
      },
      relationship_tension: { score: 0.4 },
      reply_direction: { choice: "answer_or_continue" },
    },
  },
};

export function createDemoAdapter(): Adapter {
  const listeners = new Set<() => void>();
  let timer: number | undefined;
  let data: Snapshot = {
    schema_version: 2,
    sequence: 0,
    session_revision: 3,
    config_revision: 0,
    active_message_count: 3,
    facts_pending_review: false,
    facts_reviewed_indices: [],
    ending: false,
    selected_id: "demo-session",
    sessions: [{ id: "demo-session", name: "林 · 示例会话", count: 3 }],
    messages: [
      { id: "demo-m1", role: "contact", state: "active", text: "最近真的有点累。", recall_id: null },
      { id: "demo-m2", role: "me", state: "active", text: "工作上的事吗？", recall_id: null },
      { id: "demo-m3", role: "contact", state: "active", text: "嗯，先不聊工作了。", recall_id: null },
    ],
    cards: {
      "demo-m3": {
        state: "complete", context_version: "demo-v1", output: example,
        source_message_ids: { m001: "demo-m1", m002: "demo-m2", m003: "demo-m3" },
        session_revision: 3, config_revision: 0,
      },
    },
    context_version: "demo-v1",
    confirmed_facts: [],
    mode: "jev-dynamic",
    style: "natural",
    samples: [],
    active: null,
    status: "合成示例 · 未连接模型或微信",
    storage_location: "浏览器演示不保存结果",
    credentials: { deepseek: false, typesafe: false },
    styles: [
      { id: "natural", label: "自然短句" },
      { id: "warm", label: "温和关心" },
      { id: "direct", label: "直接干脆" },
      { id: "light", label: "轻松随口" },
    ],
  };
  const originalText = new Map(data.messages.map((message) => [message.id, message.text!]));
  const operations = new Map<string, { signature: string; receipt: Record<string, unknown> }>();
  let nextId = 4;
  let nextVersion = 2;
  const updateCount = () => {
    data.active_message_count = data.messages.filter((message) => message.state === "active").length;
    data.sessions.find((session) => session.id === data.selected_id)!.count = data.active_message_count;
  };
  const invalidate = () => {
    const affectedCards = Object.keys(data.cards).length;
    const cancelRequested = Boolean(data.active?.session_id === data.selected_id);
    if (cancelRequested) {
      window.clearInterval(timer);
      data.active = null;
    }
    data.cards = {};
    data.session_revision += 1;
    data.context_version = `demo-v${nextVersion++}`;
    if (data.confirmed_facts.length) {
      data.facts_pending_review = true;
      data.facts_reviewed_indices = [];
    }
    updateCount();
    return { affectedCards, cancelRequested };
  };
  const changed = () => {
    data = { ...data, sequence: data.sequence + 1 };
    listeners.forEach((fn) => fn());
  };
  return {
    demo: true,
    snapshot: async () => structuredClone(data),
    subscribe: (fn) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    async invoke(action, payload) {
      if (action === "window.command") return structuredClone(data);
      if (action === "session.create") {
        const name = String(payload.name || "").trim();
        if (!name) throw new Error("请输入会话名称");
        const id = `demo-session-${nextId++}`;
        data.sessions.push({ id, name, count: 0 });
        data.selected_id = id;
        data.messages = [];
        data.cards = {};
        data.context_version = null;
        data.session_revision = 0;
        data.active_message_count = 0;
        data.confirmed_facts = [];
        data.facts_pending_review = false;
        data.facts_reviewed_indices = [];
        originalText.clear();
        operations.clear();
      } else if (action === "session.select") {
        if (payload.session_id !== data.selected_id)
          throw new Error("浏览器演示只保留当前示例会话；桌面版支持会话切换。");
      } else if (action === "message.append") {
        if (payload.session_id !== data.selected_id) throw new Error("本地会话不存在，请刷新界面");
        if (payload.role !== "me" && payload.role !== "contact") throw new Error("说话方无效");
        if (payload.role === "me" && payload.confirmed_sent !== true)
          throw new Error("请确认已实际发送");
        const text = String(payload.text || "").trim();
        if (!text) throw new Error("请输入文字");
        const id = `demo-m${nextId++}`;
        data.messages.push({
          id,
          role: payload.role as "me" | "contact",
          state: "active",
          text,
          recall_id: null,
        });
        originalText.set(id, text);
        invalidate();
        data.status = "演示消息已加入；浏览器演示不能分析新输入。";
      } else if (action === "message.recall" || action === "message.restore") {
        const allowed = action === "message.recall"
          ? ["session_id", "message_id", "expected_revision", "operation_id"]
          : ["session_id", "message_id", "recall_id", "expected_revision", "operation_id"];
        if (Object.keys(payload).length !== allowed.length || Object.keys(payload).some((key) => !allowed.includes(key))
            || payload.session_id !== data.selected_id || typeof payload.message_id !== "string" || !payload.message_id
            || typeof payload.operation_id !== "string" || !payload.operation_id)
          throw new Error("撤回操作参数无效");
        const signature = JSON.stringify({ action, ...payload });
        const previous = operations.get(payload.operation_id);
        if (previous) {
          if (previous.signature !== signature) throw new Error("操作编号已用于其他请求");
          return structuredClone(previous.receipt);
        }
        if (payload.expected_revision !== data.session_revision)
          throw new Error("会话已变化，请刷新后重试");
        const index = data.messages.findIndex((message) => message.id === payload.message_id);
        if (index < 0) throw new Error("消息不存在或不属于该会话");
        const message = data.messages[index];
        let recallId: string;
        if (action === "message.recall") {
          if (message.state === "revoked") throw new Error("这条记录已经撤回，请刷新界面");
          recallId = `demo-recall-${nextId++}`;
          data.messages[index] = { id: message.id, role: message.role, state: "revoked", recall_id: recallId };
          data.status = "已撤回这条记录，不再用于后续分析。";
        } else {
          if (typeof payload.recall_id !== "string" || message.state !== "revoked"
              || message.recall_id !== payload.recall_id)
            throw new Error("恢复入口已过期，请刷新界面");
          recallId = payload.recall_id;
          data.messages[index] = { id: message.id, role: message.role, state: "active",
            text: originalText.get(message.id)!, recall_id: null };
          data.status = "记录已恢复，请重新分析。";
        }
        const { affectedCards, cancelRequested } = invalidate();
        const receipt = { operation_id: payload.operation_id, session_id: payload.session_id,
          message_id: payload.message_id, index, revision: data.session_revision, recall_id: recallId,
          affected_cards: affectedCards, cancel_requested: cancelRequested };
        operations.set(payload.operation_id, { signature, receipt });
        if (operations.size > 128) operations.delete(operations.keys().next().value!);
        changed();
        return structuredClone(receipt);
      } else if (action === "preferences.update") {
        const mode = payload.mode === undefined ? data.mode : String(payload.mode);
        const style = payload.style === undefined ? data.style : String(payload.style);
        const samples = payload.samples === undefined ? data.samples : payload.samples;
        const facts = payload.confirmed_facts === undefined ? data.confirmed_facts : payload.confirmed_facts;
        if (!["jev-dynamic", "llm-only", "jev", "jev-rank"].includes(mode)
            || !data.styles.some((entry) => entry.id === style))
          throw new Error("分析模式或回复偏好无效");
        if (!Array.isArray(samples) || !Array.isArray(facts)) throw new Error("设置格式无效");
        const configChanged = mode !== data.mode || style !== data.style
          || JSON.stringify(samples) !== JSON.stringify(data.samples);
        const factsChanged = JSON.stringify(facts) !== JSON.stringify(data.confirmed_facts);
        if (configChanged) {
          data.config_revision += 1;
          data.cards = {};
          if (data.active) {
            window.clearInterval(timer);
            data.active = null;
          }
        }
        if (factsChanged) {
          data.confirmed_facts = [...facts];
          invalidate();
          data.facts_pending_review = false;
          data.facts_reviewed_indices = [];
        }
        data.mode = mode;
        data.style = style;
        data.samples = [...samples];
        data.status = "设置已更新；旧候选如受影响会标为过期。";
      } else if (action === "facts.confirm") {
        if (payload.session_id !== data.selected_id) throw new Error("本地会话不存在，请刷新界面");
        if (payload.expected_revision !== data.session_revision)
          throw new Error("会话已变化，请刷新后重试");
        if (data.facts_pending_review) {
          const index = payload.index;
          if (index !== undefined && (typeof index !== "number" || !Number.isInteger(index)
              || index < 0 || index >= data.confirmed_facts.length)) throw new Error("事实编号无效");
          if (index === undefined) data.facts_reviewed_indices = data.confirmed_facts.map((_, i) => i);
          else if (!data.facts_reviewed_indices.includes(index)) data.facts_reviewed_indices.push(index);
          data.facts_pending_review = data.facts_reviewed_indices.length < data.confirmed_facts.length;
          data.cards = {};
          data.session_revision += 1;
          data.context_version = `demo-v${nextVersion++}`;
        }
        data.status = "事实已复核，重新分析后生效。";
      } else if (action === "analysis.start") {
        if (payload.session_id !== data.selected_id || payload.context_version !== data.context_version)
          throw new Error("对话或设置已变化，请重新检查");
        if (data.active) throw new Error("已有分析正在进行，请等待或取消");
        const target = [...data.messages].reverse().find((message) => message.state === "active");
        if (!target || target.role !== "contact") throw new Error("加入新的对方消息后再分析");
        if (data.context_version !== "demo-v1")
          throw new Error("浏览器演示不能分析新输入；请运行桌面版。");
        const request_id = `demo-request-${nextId++}`;
        data.active = {
          session_id: data.selected_id,
          index: data.messages.findIndex((message) => message.id === target.id),
          message_id: target.id,
          request_id,
          context_version: "demo-v1",
          mode: data.mode,
        };
        let stage = 0;
        data.cards["demo-m3"] = {
          state: "progress",
          context_version: "demo-v1",
          source_message_ids: { m001: "demo-m1", m002: "demo-m2", m003: "demo-m3" },
          session_revision: data.session_revision,
          config_revision: data.config_revision,
          output: {
            ...example,
            request_id,
            result: null,
            jev: null,
            subtext_hypotheses: [],
            dynamic_analysis: {
              stages: {
                propose: { status: "running" },
                judge: { status: "pending" },
                reply: { status: "pending" },
              },
            },
          },
        };
        timer = window.setInterval(() => {
          if (!data.active || data.active.request_id !== request_id) {
            clearInterval(timer);
            return;
          }
          stage += 1;
          if (stage === 1) {
            data.cards["demo-m3"].output!.subtext_hypotheses = example.subtext_hypotheses?.map(
              (row) => ({
                ...row,
                status: "unjudged",
                support_probability: null,
                evidence_status: "unjudged",
              }),
            );
            data.cards["demo-m3"].output!.dynamic_analysis!.stages = {
              propose: { status: "complete" },
              judge: { status: "running" },
              reply: { status: "pending" },
            };
          }
          if (stage === 2) {
            data.cards["demo-m3"].output!.jev = example.jev;
            data.cards["demo-m3"].output!.subtext_hypotheses = example.subtext_hypotheses;
            data.cards["demo-m3"].output!.dynamic_analysis!.stages = {
              propose: { status: "complete" },
              judge: { status: "complete" },
              reply: { status: "running" },
            };
          }
          if (stage >= 3) {
            clearInterval(timer);
            data.cards["demo-m3"] = {
              state: "complete",
              context_version: "demo-v1",
              output: { ...example, request_id },
              source_message_ids: { m001: "demo-m1", m002: "demo-m2", m003: "demo-m3" },
              session_revision: data.session_revision,
              config_revision: data.config_revision,
            };
            data.active = null;
          }
          changed();
        }, 850);
      } else if (action === "analysis.cancel") {
        if (!data.active || payload.request_id !== data.active.request_id)
          throw new Error("没有匹配的分析任务");
        window.clearInterval(timer);
        data.active = null;
        data.cards = {};
        data.status = "已取消示例分析。";
      } else if (action === "candidate.copy") {
        if (payload.session_id !== data.selected_id) throw new Error("会话已切换，候选不可复制");
        const targetId = typeof payload.message_id === "string" ? payload.message_id
          : data.messages[Number(payload.index)]?.id;
        const card = targetId ? data.cards[targetId] : undefined;
        const target = data.messages.find((message) => message.id === targetId);
        const lastActive = [...data.messages].reverse().find((message) => message.state === "active");
        if (!card || card.state !== "complete" || target?.state !== "active"
            || lastActive?.id !== targetId || data.context_version !== card.context_version
            || data.session_revision !== card.session_revision
            || data.config_revision !== card.config_revision
            || payload.session_revision !== data.session_revision
            || payload.config_revision !== data.config_revision
            || payload.request_id !== card.output?.request_id
            || payload.context_version !== card.context_version || payload.mode !== data.mode)
          throw new Error("演示候选已过期，请重新分析");
        await navigator.clipboard.writeText(String(payload.text));
        return { message: "示例文字已复制；未发送。" };
      } else if (action === "message.copy") {
        if (payload.session_id !== data.selected_id) throw new Error("会话已切换，请刷新界面");
        const message = data.messages.find((item) => item.id === payload.message_id);
        if (!message || message.state !== "active" || !message.text)
          throw new Error("这条记录已撤回或不存在");
        await navigator.clipboard.writeText(message.text);
        return { message: "原文已复制。" };
      } else if (action === "card.dismiss") {
        if (payload.session_id !== data.selected_id) throw new Error("会话已切换，请刷新界面");
        const targetId = typeof payload.message_id === "string" ? payload.message_id
          : data.messages[Number(payload.index)]?.id;
        if (!targetId || !data.cards[targetId]) throw new Error("该消息没有可删除的分析卡片");
        delete data.cards[targetId];
      } else throw new Error("未知动作");
      changed();
      return structuredClone(data);
    },
  };
}
