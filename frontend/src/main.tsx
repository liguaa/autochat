import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowLeft,
  ArrowUpRight,
  CaretDown,
  Check,
  Copy,
  GearSix,
  Plus,
  SidebarSimple,
  Sparkle,
  X,
} from "@phosphor-icons/react";
import { connect } from "./bridge";
import type { Adapter, Card, Hypothesis, Output, Snapshot } from "./types";
import "./style.css";

const modeNames: Record<string, string> = {
  "jev-dynamic": "深入分析",
  "llm-only": "快速回复",
  jev: "Jev 判断",
  "jev-rank": "Jev 判断与排序",
};
const evidenceNames: Record<string, string> = {
  sufficient: "原文支持",
  insufficient: "证据不足",
  conflicting: "原文有冲突",
  unjudged: "尚未判断",
};
const stageNames: Record<string, string> = {
  propose: "整理可能解释",
  judge: "核对原文依据",
  reply: "写回复",
};
const stageSymbols: Record<string, string> = {
  complete: "✓",
  running: "•",
  failed: "!",
  cancelled: "×",
  pending: "○",
};

function App() {
  const [adapter, setAdapter] = useState<Adapter | null>(null);
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [panel, setPanel] = useState(
    new URLSearchParams(location.search).get("view") === "panel",
  );
  const [drawer, setDrawer] = useState(false);
  const [settings, setSettings] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{id: string; name: string; revision: number} | null>(null);
  const [deleting, setDeleting] = useState(false);
  const deleteDialog = useRef<HTMLDialogElement | null>(null);
  const [mobileView, setMobileView] = useState<"chat" | "reply">("chat");
  const [draft, setDraft] = useState("");
  const [role, setRole] = useState<"contact" | "me">("contact");
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [candidateIndex, setCandidateIndex] = useState(0);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [toast, setToast] = useState("");
  const [restoreToast, setRestoreToast] = useState<{session_id: string; message_id: string; recall_id: string} | null>(null);
  const [busyMessage, setBusyMessage] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [factsDraft, setFactsDraft] = useState("");
  const [samplesDraft, setSamplesDraft] = useState("");
  const [highlight, setHighlight] = useState<string | null>(null);
  const messageRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const applied = useRef<{sequence: number; selected_id: string}>({sequence: -1, selected_id: ""});
  const toastTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    let unsubscribe = () => {};
    let stopped = false;
    connect().then((next) => {
      if (stopped) return;
      setAdapter(next);
      const applySnapshot = (value: Snapshot) => {
        if (value.sequence < applied.current.sequence) return;
        if (value.sequence === applied.current.sequence && applied.current.selected_id && value.selected_id !== applied.current.selected_id) return;
        applied.current = {sequence: value.sequence, selected_id: value.selected_id};
        setSnap(value);
      };
      const refresh = () =>
        next.snapshot().then((value) => {
          if (!stopped) applySnapshot(value);
        });
      applySnapshotRef.current = applySnapshot;
      unsubscribe = next.subscribe(refresh);
      refresh();
    });
    return () => {
      stopped = true;
      unsubscribe();
    };
  }, []);
  const applySnapshotRef = useRef<(value: Snapshot) => void>(() => {});

  useEffect(() => {
    if (!snap) return;
    setFactsDraft(snap.confirmed_facts.join("\n"));
    setSamplesDraft(snap.samples.join("\n"));
  }, [
    snap?.selected_id,
    snap?.confirmed_facts.join("\n"),
    snap?.samples.join("\n"),
  ]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSettings(false);
        setDrawer(false);
        document.querySelectorAll<HTMLDetailsElement>("details.message-menu[open]").forEach((menu) => {
          menu.open = false;
          (menu.querySelector("summary") as HTMLElement | null)?.focus();
        });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const notify = (message: string) => {
    setToast(message);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => { setToast(""); setRestoreToast(null); }, 6000);
  };
  useEffect(() => {
    if (deleteTarget) deleteDialog.current?.showModal();
    else deleteDialog.current?.close();
  }, [deleteTarget]);
  const act = async (action: string, payload: Record<string, unknown>) => {
    if (!adapter) return undefined;
    try {
      const result = await adapter.invoke(action, payload);
      if (result && typeof result === "object" && "schema_version" in result) applySnapshotRef.current(result as Snapshot);
      applySnapshotRef.current(await adapter.snapshot());
      return result;
    } catch (error) {
      notify(error instanceof Error ? error.message : "操作失败");
      return undefined;
    }
  };

  const session = snap?.sessions.find((row) => row.id === snap.selected_id);
  const lastMessage = [...(snap?.messages ?? [])].reverse().find((row) => row.state === "active");
  const lastIndex = lastMessage ? snap!.messages.findIndex((row) => row.id === lastMessage.id) : -1;
  const currentCard = lastMessage ? snap?.cards[lastMessage.id] : undefined;
  const output = currentCard?.output;
  const candidates = output?.result?.candidates ?? [];
  const safeIndex = Math.min(
    candidateIndex,
    Math.max(0, candidates.length - 1),
  );
  const currentCandidate = candidates[safeIndex];
  const candidateKey = `${snap?.selected_id}:${output?.request_id}:${safeIndex}`;
  const candidateText = edits[candidateKey] ?? currentCandidate?.text ?? "";
  const stale =
    !currentCard ||
    currentCard.state !== "complete" ||
    currentCard.context_version !== snap?.context_version ||
    output?.pipeline_mode !== snap?.mode ||
    currentCard.session_revision !== snap?.session_revision ||
    currentCard.config_revision !== snap?.config_revision;
  const activeHere = snap?.active?.session_id === snap?.selected_id;
  const canAnalyze =
    !draft.trim() &&
    lastIndex >= 0 &&
    lastMessage?.role === "contact" &&
    !snap?.active && !snap?.ending;

  const addMessage = async () => {
    if (!snap || !draft.trim()) {
      notify("先输入一段原文。");
      return;
    }
    const result = await act("message.append", {
      session_id: snap.selected_id,
      role,
      text: draft,
      confirmed_sent: role === "me",
    });
    if (result) {
      setDraft("");
      setCandidateIndex(0);
      setMobileView("chat");
    }
  };
  const analyze = async () => {
    if (!snap) return;
    if (draft.trim()) {
      notify("输入框还有未加入的文字，请先加入消息。");
      return;
    }
    if (!canAnalyze) {
      notify("请先加入一条对方消息。");
      return;
    }
    if (adapter?.demo && currentCard?.state === "complete")
      notify("正在回放固定合成示例，未调用模型。");
    await act("analysis.start", {
      session_id: snap.selected_id,
      context_version: snap.context_version,
    });
  };
  const copy = async () => {
    if (!snap || stale) {
      notify("候选已过期，请重新分析。");
      return;
    }
    const result = await act("candidate.copy", {
      session_id: snap.selected_id,
      message_id: lastMessage?.id,
      session_revision: snap.session_revision,
      config_revision: snap.config_revision,
      index: lastIndex,
      request_id: output?.request_id,
      context_version: snap.context_version,
      mode: snap.mode,
      candidate_index: safeIndex,
      text: candidateText,
    });
    if (result) notify(result.message);
  };
  const locate = (sourceId: string, sourceMap: Record<string, string>) => {
    if (!snap) return;
    const messageId = sourceMap[sourceId];
    if (!messageId || !snap.messages.some((row) => row.id === messageId && row.state === "active")) {
      notify("对应原文已撤回或不在当前载入范围。");
      return;
    }
    setMobileView("chat");
    setHighlight(messageId);
    window.setTimeout(
      () =>
        messageRefs.current[messageId]?.scrollIntoView({
          block: "center",
          behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
            ? "instant"
            : "smooth",
        }),
      40,
    );
    window.setTimeout(() => setHighlight(null), 850);
  };
  const recall = async (messageId: string) => {
    if (!snap || busyMessage) return;
    const sessionId = snap.selected_id;
    setBusyMessage(messageId);
    const result = await act("message.recall", {session_id: sessionId, message_id: messageId,
      expected_revision: snap.session_revision, operation_id: crypto.randomUUID()});
    setBusyMessage(null);
    if (result?.recall_id) {
      setRestoreToast({session_id: sessionId, message_id: messageId, recall_id: result.recall_id});
      notify("已撤回这条记录，不再用于后续分析。");
      window.setTimeout(() => document.getElementById(`restore-${messageId}`)?.focus(), 30);
    }
  };
  const restore = async (sessionId: string, messageId: string, recallId: string) => {
    if (!snap || busyMessage) return;
    if (snap.selected_id !== sessionId) { notify("请返回原会话恢复这条记录。"); return; }
    setBusyMessage(messageId);
    const result = await act("message.restore", {session_id: sessionId, message_id: messageId,
      recall_id: recallId, expected_revision: snap.session_revision, operation_id: crypto.randomUUID()});
    setBusyMessage(null);
    if (result) { setRestoreToast(null); notify("记录已恢复，请重新分析。");
      window.setTimeout(() => document.getElementById(`menu-${messageId}`)?.focus(), 30); }
  };
  const selectSession = async (id: string) => {
    if (draft.trim()) {
      notify("请先加入或清空当前未加入的草稿。");
      return;
    }
    if (await act("session.select", { session_id: id })) {
      setDrawer(false);
      setCandidateIndex(0);
    }
  };
  const savePreferences = async () => {
    if (!snap) return;
    const facts = factsDraft
      .split("\n")
      .map((x) => x.trim())
      .filter(Boolean);
    const samples = samplesDraft
      .split("\n")
      .map((x) => x.trim())
      .filter(Boolean);
    if (await act("preferences.update", { confirmed_facts: facts, samples })) {
      notify("设置已保存；受影响的候选需要重新分析。");
      setSettings(false);
    }
  };
  const openWindow = async (command: string) => {
    if (adapter?.demo) {
      if (command === "open_panel" || command === "open_workbench")
        setPanel(command === "open_panel");
      else notify("请在桌面版中配置 API 密钥。");
      return;
    }
    await act("window.command", { command });
  };
  if (!snap) return <div className="loading">正在打开 AutoChat…</div>;

  return (
    <div
      className={`app ${panel ? "is-panel" : ""} ${mobileView === "reply" ? "show-reply" : ""}`}
    >
      {adapter?.demo && (
        <div className="demo-ribbon">合成示例 · 浏览器预览未连接模型或微信</div>
      )}
      <div className="shell">
        <header className="app-header">
          <div className="brand">
            <span className="brand-mark">ac</span>
            <strong>AutoChat</strong>
            <span className="brand-caption">让回应更有分寸</span>
          </div>
          <div className="header-actions">
            <span className="manual-label">手动聊天助手</span>
            <div className="mobile-tabs">
              <button
                className={mobileView === "chat" ? "active" : ""}
                onClick={() => setMobileView("chat")}
              >
                对话
              </button>
              <button
                className={mobileView === "reply" ? "active" : ""}
                onClick={() => setMobileView("reply")}
              >
                回应
              </button>
            </div>
            {panel && (
              <button
                className="text-button"
                onClick={() => openWindow("open_workbench")}
              >
                展开工作台 <ArrowUpRight size={15} />
              </button>
            )}
            {!panel && (
              <button
                className="text-button panel-switch"
                onClick={() => openWindow("open_panel")}
              >
                轻面板
              </button>
            )}
            <button
              className="icon-button"
              aria-label="设置"
              onClick={() => setSettings(true)}
            >
              <GearSix size={19} weight="light" />
            </button>
            <button
              className="text-button collapse"
              onClick={() => openWindow("collapse")}
            >
              收起
            </button>
          </div>
        </header>
        <main className="workspace">
          <aside className={`sidebar ${drawer ? "open" : ""}`}>
            <div className="sidebar-heading">
              <span>本地会话</span>
              <button
                className="icon-button"
                aria-label="新建会话"
                onClick={() => setCreating(true)}
              >
                <Plus size={16} />
              </button>
            </div>
            {creating && (
              <div className="create-session">
                <input
                  autoFocus
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="给会话命名"
                  maxLength={40}
                />
                <button
                  onClick={async () => {
                    if (await act("session.create", { name: newName })) {
                      setCreating(false);
                      setNewName("");
                      setDrawer(false);
                    }
                  }}
                >
                  建立
                </button>
              </div>
            )}
            <nav aria-label="本地会话">
              {snap.sessions.map((row) => (
                <button
                  key={row.id}
                  className={`session-item ${row.id === snap.selected_id ? "active" : ""}`}
                  onClick={() => selectSession(row.id)}
                  aria-current={
                    row.id === snap.selected_id ? "page" : undefined
                  }
                >
                  <span className="avatar">{row.name[0]}</span>
                  <span className="session-label">
                    <strong>{row.name}</strong>
                    <small>{row.count} 条手动消息</small>
                  </span>
                </button>
              ))}
            </nav>
            <div className="sidebar-note">
              只整理你主动提供的文字。
              <br />
              每次回应，都由你决定。
            </div>
          </aside>
          <section className="conversation" aria-label="对话与分析">
            <div className="conversation-head">
              <div className="title-row">
                <button
                  className="icon-button drawer-trigger"
                  aria-label="会话列表"
                  onClick={() => setDrawer(!drawer)}
                >
                  <SidebarSimple size={20} />
                </button>
                <div>
                  <p className="eyebrow">当前本地会话</p>
                  <h1>{session?.name}</h1>
                </div>
              </div>
              <div>
                <span className="count-pill">{snap.active_message_count} 条有效原文</span>
                <button className="inline-action" disabled={adapter?.demo}
                  onClick={() => setDeleteTarget({id: snap.selected_id, name: session?.name || "本地会话", revision: snap.session_revision})}>
                  删除会话
                </button>
              </div>
            </div>
            {!!snap.session_storage_errors?.length && <p className="stale-note" role="alert">
              {snap.session_storage_pending ? "部分会话尚未保存，请勿直接退出。" : "部分历史会话加载失败。"}
              <button className="inline-action" onClick={() => setSettings(true)}>查看存储详情</button>
            </p>}
            <div className="thread">
              {snap.messages.length === 0 && (
                <div className="empty-state">
                  <span className="empty-symbol">
                    <Sparkle size={24} weight="light" />
                  </span>
                  <h2>从原文开始。</h2>
                  <p>粘贴一段对话，核对说话方，再加入本地会话。</p>
                </div>
              )}
              {snap.active_message_count === 0 && snap.messages.length > 0 && (
                <p className="context-notice">当前没有可分析的消息。恢复记录后可重新分析。</p>
              )}
              {snap.messages.map((message, index) => (
                <React.Fragment key={`${snap.selected_id}-${message.id}`}>
                  {message.state === "revoked" ? (
                    <div className="recall-placeholder" ref={(node) => {messageRefs.current[message.id] = node;}}>
                      <span>已撤回一条{message.role === "me" ? "本人" : "对方"}记录 · 不再用于分析</span>
                      <button id={`restore-${message.id}`} className="inline-action" disabled={busyMessage === message.id}
                        onClick={() => restore(snap.selected_id, message.id, message.recall_id!)}>恢复记录</button>
                    </div>
                  ) : (
                  <div
                    className={`message-row ${message.role === "me" ? "mine" : ""}`}
                    ref={(node) => {
                      messageRefs.current[message.id] = node;
                    }}
                  >
                    {message.role === "contact" && (
                      <span className="avatar small">
                        {session?.name[0] || "对"}
                      </span>
                    )}
                    <div>
                      <span className="message-label">
                        {message.role === "me"
                          ? "我 · 确认已发送"
                          : "对方 · 手动导入"}
                      </span>
                      <details className="message-menu">
                        <summary id={`menu-${message.id}`} aria-label="更多记录操作" aria-haspopup="menu">更多</summary>
                        <div className="message-menu-items" role="menu">
                          <button role="menuitem" onClick={async () => { const result = await act("message.copy", {session_id: snap.selected_id, message_id: message.id}); if (result) notify(result.message); }}>复制原文</button>
                          <button role="menuitem" disabled={busyMessage === message.id} onClick={() => recall(message.id)}>撤回这条记录</button>
                          <small>仅影响 AutoChat，不会撤回微信消息。</small>
                        </div>
                      </details>
                      <div
                        className={`message-bubble ${highlight === message.id ? "highlight" : ""}`}
                      >
                        {message.text}
                      </div>
                    </div>
                  </div>)}
                  {message.state === "active" && message.role === "contact" && snap.cards[message.id] && (
                    <AnalysisCard
                      card={snap.cards[message.id]}
                      index={index}
                      latest={index === lastIndex}
                      stale={
                        index !== lastIndex ||
                        snap.cards[message.id].context_version !==
                          snap.context_version ||
                        snap.cards[message.id].output?.pipeline_mode !== snap.mode ||
                        snap.cards[message.id].session_revision !== snap.session_revision ||
                        snap.cards[message.id].config_revision !== snap.config_revision
                      }
                      showAll={showAll}
                      setShowAll={setShowAll}
                      locate={locate}
                      dismiss={() =>
                        act("card.dismiss", {
                          session_id: snap.selected_id,
                          message_id: message.id,
                        })
                      }
                    />
                  )}
                </React.Fragment>
              ))}
              {activeHere && currentCard?.state !== "progress" && (
                <div className="analysis-placeholder">
                  分析正在进行，阶段会在这里更新。
                </div>
              )}
              {snap.ending && <div className="analysis-placeholder">上一任务正在结束，请稍后再分析。</div>}
              {snap.messages.some((row) => row.state === "revoked") && (
                <div className="context-notice">上下文已更新，旧分析和回复已失效。{canAnalyze && <button className="inline-action" onClick={analyze}>重新分析</button>}</div>
              )}
            </div>
            <div className="composer">
              <div className="input-tray">
                <div className="input-core">
                  <div className="input-top">
                    <span>原文草稿</span>
                    <span>仅加入本地会话，不发送</span>
                  </div>
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    placeholder="粘贴或输入一条消息…"
                    aria-label="原文草稿"
                    maxLength={4000}
                  />
                </div>
              </div>
              <div className="composer-actions">
                <select
                  aria-label="说话方"
                  value={role}
                  onChange={(e) => setRole(e.target.value as "contact" | "me")}
                >
                  <option value="contact">对方</option>
                  <option value="me">我 · 已发送</option>
                </select>
                <button
                  className="secondary"
                  disabled={!draft.trim()}
                  onClick={addMessage}
                >
                  {role === "me" ? "记录我已发送的文字" : "加入消息"}
                </button>
                {activeHere ? (
                  <button
                    className="secondary warn-button"
                    onClick={() =>
                      act("analysis.cancel", {
                        request_id: snap.active!.request_id,
                      })
                    }
                  >
                    取消分析
                  </button>
                ) : (
                  <button
                    className="primary"
                    disabled={!canAnalyze}
                    onClick={analyze}
                  >
                    分析 <ArrowUpRight size={16} />
                  </button>
                )}
              </div>
              {draft.trim() && (
                <p className="draft-notice">
                  草稿尚未加入上下文；加入或清空后才能分析。
                </p>
              )}
              {!draft.trim() && lastMessage?.role === "me" && <p className="draft-notice">加入新的对方消息后再分析。</p>}
              {snap.facts_pending_review && <p className="draft-notice">部分事实待复核；本次分析不使用未复核的事实。</p>}
              {!snap.credentials.deepseek && !adapter?.demo && (
                <p className="config-notice">
                  尚未配置 DeepSeek 密钥。
                  <button onClick={() => setSettings(true)}>
                    查看配置方法
                  </button>
                </p>
              )}
            </div>
          </section>
          <aside className="reply-panel" aria-label="回复建议">
            <div className="reply-scroll">
              <p className="eyebrow">回应建议</p>
              <h2>把理解，变成一句话。</h2>
              <p className="reply-intro">
                候选是草稿。核对原文，再用你的语气修改。
              </p>
              {currentCard?.state === "error" && (
                <div className="reply-empty error">
                  <strong>本次分析失败</strong>
                  <p>{currentCard.message}</p>
                  <button className="secondary" onClick={analyze}>
                    重试整轮分析
                  </button>
                </div>
              )}
              {!candidates.length && currentCard?.state !== "error" && (
                <div className="reply-empty">
                  <Sparkle size={22} />
                  <strong>
                    {currentCard?.state === "progress"
                      ? "正在准备回复…"
                      : "还没有回复候选"}
                  </strong>
                  <p>
                    {currentCard?.state === "progress"
                      ? "已完成的判断会先显示在原文下方。"
                      : "加入对方消息后，点击分析。"}
                  </p>
                </div>
              )}
              {candidates.length > 0 && (
                <>
                  <div
                    className="candidate-tabs"
                    role="tablist"
                    aria-label="回复候选"
                  >
                    {candidates.map((candidate, index) => (
                      <button
                        key={index}
                        role="tab"
                        aria-selected={safeIndex === index}
                        className={safeIndex === index ? "active" : ""}
                        onClick={() => setCandidateIndex(index)}
                      >
                        {candidate.style || `候选 ${index + 1}`}
                      </button>
                    ))}
                  </div>
                  <div className="draft-tray">
                    <div className="draft-core">
                      <label htmlFor="reply-text">
                        你的回复草稿 · 可直接修改
                      </label>
                      <textarea
                        id="reply-text"
                        value={candidateText}
                        onChange={(e) =>
                          setEdits({ ...edits, [candidateKey]: e.target.value })
                        }
                        maxLength={4000}
                      />
                    </div>
                  </div>
                  {stale && (
                    <p className="stale-note">对话或设置已变化，请重新分析。</p>
                  )}
                  <button
                    className="primary copy-button"
                    disabled={stale || !candidateText.trim()}
                    onClick={copy}
                  >
                    <Copy size={16} /> 复制回复
                  </button>
                  <p className="copy-help">
                    复制后由你在微信核对、粘贴并发送。这里不会发送消息。
                  </p>
                </>
              )}
              <div className="detail-section">
                <h3>已确认的事实</h3>
                {snap.facts_pending_review && <p className="stale-note">未复核的事实暂不用于分析。</p>}
                {snap.confirmed_facts.length ? (
                  <ul>
                    {snap.confirmed_facts.map((fact, i) => (
                      <li key={i}>{fact}{snap.facts_pending_review && (snap.facts_reviewed_indices.includes(i)
                        ? <span className="fact-reviewed"> · 已复核</span>
                        : <button className="inline-action" onClick={() => act("facts.confirm", {session_id: snap.selected_id, expected_revision: snap.session_revision, index: i})}>确认这条</button>)}</li>
                    ))}
                  </ul>
                ) : (
                  <p>当前没有额外确认的事实。</p>
                )}
                <button
                  className="inline-action"
                  onClick={() => setSettings(true)}
                >
                  编辑事实 <ArrowUpRight size={13} />
                </button>
                {snap.facts_pending_review && <button className="inline-action" onClick={() => act("facts.confirm", {session_id: snap.selected_id, expected_revision: snap.session_revision})}>确认这些事实仍成立</button>}
              </div>
              <div className="detail-section">
                <h3>回复偏好</h3>
                <p>
                  {snap.styles.find((row) => row.id === snap.style)?.label ||
                    snap.style}
                </p>
                <button
                  className="inline-action"
                  onClick={() => setSettings(true)}
                >
                  调整偏好 <ArrowUpRight size={13} />
                </button>
              </div>
              {output && (
                <details className="meta-details">
                  <summary>
                    本次分析详情 <CaretDown size={14} />
                  </summary>
                  <p>
                    模式：
                    {modeNames[output.pipeline_mode] || output.pipeline_mode}
                  </p>
                  <p>本次原文与配置只用于这轮分析；候选未发送。</p>
                  {output.jev && <p>判断来源：Jev。支持度未经中文聊天校准。</p>}
                  {output.jev_reply_direction?.choice && (
                    <p>
                      回复方向（Choice）：{output.jev_reply_direction.choice}
                      ；所选类别相对概率
                      {output.jev_reply_direction.probabilities?.[
                        output.jev_reply_direction.choice
                      ] == null
                        ? "未判断"
                        : ` ${(output.jev_reply_direction.probabilities[output.jev_reply_direction.choice] * 100).toFixed(0)}%`}
                    </p>
                  )}
                  {typeof output.jev?.answers?.relationship_tension?.score ===
                    "number" && (
                    <p>
                      关系紧张量表：
                      {output.jev.answers.relationship_tension.score.toFixed(2)}{" "}
                      / 4， 不是“生气概率”。
                    </p>
                  )}
                  {output.dynamic_analysis?.fallback_reason && (
                    <p>阶段状态：{output.dynamic_analysis.fallback_reason}</p>
                  )}
                </details>
              )}
            </div>
          </aside>
        </main>
      </div>
      {settings && (
        <div
          className="scrim"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setSettings(false);
          }}
        >
          <div
            className="settings-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="设置"
          >
            <div className="settings-head">
              <div>
                <p className="eyebrow">AutoChat</p>
                <h2>设置</h2>
              </div>
              <button
                className="icon-button"
                aria-label="关闭设置"
                onClick={() => setSettings(false)}
              >
                <X size={20} />
              </button>
            </div>
            <div className="settings-scroll">
              <section>
                <h3>分析方式</h3>
                <select
                  value={snap.mode}
                  onChange={(e) =>
                    act("preferences.update", { mode: e.target.value })
                  }
                >
                  <option value="jev-dynamic">深入分析 · DS → Jev → DS</option>
                  <option value="llm-only">快速回复 · 仅 DeepSeek</option>
                  <option value="jev">Jev 判断 + DeepSeek</option>
                  <option value="jev-rank">
                    Jev 判断 + DeepSeek + Jev 排序
                  </option>
                </select>
                <p>
                  仅点击“分析”才调用服务。深入分析会把本次文字发送给 DeepSeek 和
                  TypeSafe。
                </p>
              </section>
              <section>
                <h3>回复偏好</h3>
                <select
                  value={snap.style}
                  onChange={(e) =>
                    act("preferences.update", { style: e.target.value })
                  }
                >
                  {snap.styles.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.label}
                    </option>
                  ))}
                </select>
                <label className="field-label">本人语气示例 · 每行一条</label>
                <textarea
                  value={samplesDraft}
                  onChange={(e) => setSamplesDraft(e.target.value)}
                  placeholder="只模仿表达，不作为事实；“按我的语气”至少需要 2 条"
                />
                <h3>文件人格 · SKILL.md</h3>
                <p>在下方目录创建“人格名/SKILL.md”，编辑后重新加载，再从回复偏好中选择。文件只定义表达方式，不执行脚本。</p>
                {snap.persona_directory && <p style={{ overflowWrap: "anywhere" }}>{snap.persona_directory}</p>}
                <button className="secondary" disabled={adapter?.demo} onClick={() => act("personas.reload", {})}>
                  重新加载人格文件
                </button>
                {adapter?.demo && <p>浏览器示例不能读取本机人格文件，请在桌面版使用。</p>}
                {snap.persona_errors?.map((error, i) => <p className="stale-note" key={i}>{error}</p>)}
                {snap.persona_preview && <details className="meta-details">
                  <summary>查看所选人格内容</summary>
                  <p>{snap.persona_preview.description}</p>
                  <p>内容版本：{snap.persona_preview.sha256.slice(0, 12)}</p>
                  <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", font: "inherit" }}>{snap.persona_preview.instructions}</pre>
                </details>}
                <p>所选文件变化后，旧候选失效；内容随本次回复请求提交给 DeepSeek。修改不会自动调用模型。</p>
              </section>
              <section>
                <h3>已确认的事实</h3>
                <textarea
                  value={factsDraft}
                  onChange={(e) => setFactsDraft(e.target.value)}
                  placeholder="每行一条。不要把潜台词推测写成事实。"
                />
              </section>
              <section>
                <h3>API 配置</h3>
                <p>
                  DeepSeek：{snap.credentials.deepseek ? "已配置" : "未配置"} ·
                  TypeSafe：{snap.credentials.typesafe ? "已配置" : "未配置"}
                </p>
                <p>
                  在终端运行 <code>python -m autochat.credentials set</code>
                  ，Jev 还需 <code>--provider typesafe</code>
                  。密钥保存在现有凭据管理器，界面只读取配置状态。
                </p>
                <div className="key-actions">
                  <button
                    className="secondary"
                    onClick={() => openWindow("configure_deepseek")}
                  >
                    配置 DeepSeek
                  </button>
                  <button
                    className="secondary"
                    onClick={() => openWindow("configure_typesafe")}
                  >
                    配置 TypeSafe
                  </button>
                </div>
              </section>
              <section>
                <h3>记录保存</h3>
                <p>
                  {adapter?.demo
                    ? "浏览器演示不保存结果。"
                    : "会话原文、撤回状态和事实自动保存，启动时自动恢复。历史分析结果另行保留；恢复后需重新分析。"}
                  复制不会添加已发送记录。未加入的输入草稿不保存。
                </p>
                {!adapter?.demo && (
                  <>
                    <p>会话目录：<code>{snap.session_storage_location}</code></p>
                    <p>分析结果目录：<code>{snap.storage_location}</code></p>
                    <p>删除会话会移到会话目录下的 trash，不自动清理分析结果，也不影响微信消息。文件为本机明文 JSON。</p>
                    {snap.session_storage_errors?.map((error, i) => <p className="stale-note" key={i}>{error}</p>)}
                    <button className="secondary" onClick={() => act("sessions.save", {})}>重试保存会话</button>
                  </>
                )}
              </section>
            </div>
            <button className="primary settings-save" onClick={savePreferences}>
              保存设置
            </button>
          </div>
        </div>
      )}
      <dialog ref={deleteDialog} className="session-delete-dialog" onCancel={() => setDeleteTarget(null)}>
        <h2>删除“{deleteTarget?.name}”？</h2>
        <p>会话将移到本机会话目录下的 trash，重启后不再自动加载。可以关闭应用后从该目录手动恢复。</p>
        <p>本会话的分析任务会停止；未加入的输入草稿会清空。历史分析结果文件、微信消息仍保留。</p>
        <div className="key-actions">
          <button className="secondary" disabled={deleting} onClick={() => setDeleteTarget(null)}>取消</button>
          <button className="primary" disabled={deleting} onClick={async () => {
            if (!deleteTarget) return;
            setDeleting(true);
            const result = await act("session.delete", {session_id: deleteTarget.id, expected_revision: deleteTarget.revision});
            setDeleting(false);
            if (result) {
              setDraft(""); setEdits({}); setRestoreToast(null); setCandidateIndex(0); setDeleteTarget(null);
              notify("会话已移至回收目录；历史分析结果仍保留。");
            }
          }}>{deleting ? "正在删除…" : "移至回收目录"}</button>
        </div>
      </dialog>
      {drawer && (
        <button
          className="drawer-backdrop"
          aria-label="关闭会话列表"
          onClick={() => setDrawer(false)}
        />
      )}
      <div
        className={`toast ${toast ? "visible" : ""}`}
        role="status"
        aria-live="polite"
      >
        {toast}
        {restoreToast && <button className="toast-restore" onClick={() => restore(restoreToast.session_id, restoreToast.message_id, restoreToast.recall_id)}>恢复</button>}
      </div>
    </div>
  );
}

function AnalysisCard({
  card,
  index,
  latest,
  stale,
  showAll,
  setShowAll,
  locate,
  dismiss,
}: {
  card: Card;
  index: number;
  latest: boolean;
  stale: boolean;
  showAll: boolean;
  setShowAll: (value: boolean) => void;
  locate: (sourceId: string, sourceMap: Record<string, string>) => void;
  dismiss: () => void;
}) {
  const output = card.output;
  const rows = output?.subtext_hypotheses ?? [];
  const visible = showAll ? rows : rows.slice(0, 2);
  const stages = output?.dynamic_analysis?.stages;
  return (
    <section
      className={`analysis-note ${stale ? "is-stale" : ""}`}
      aria-label={`第 ${index + 1} 条消息的分析`}
    >
      <div className="analysis-top">
        <span>这句话的分析</span>
        <span>
          {stale
            ? "历史分析"
            : card.state === "progress"
              ? "分析中"
              : "当前分析"}
        </span>
      </div>
      {card.state === "error" && (
        <p className="analysis-error">分析失败：{card.message}</p>
      )}
      {output && <EmotionIntentSummary output={output} stages={stages} />}
      <p className="hypotheses-heading">这句话，可能是在说</p>
      {rows.length > 0 ? (
        <div className="hypotheses">
          {visible.map((row) => (
            <HypothesisRow key={row.id} row={row} locate={(sourceId) => locate(sourceId, card.source_message_ids || {})} />
          ))}
        </div>
      ) : (
        card.state !== "error" && (
          <p className="analysis-summary">
            {output?.result?.analysis?.subtext ||
              "目前没有通过原文引用校验的解释；请结合原文判断。"}
          </p>
        )
      )}
      {rows.length > 2 && (
        <button className="inline-action" onClick={() => setShowAll(!showAll)}>
          {showAll ? "收起其他解释" : `查看其余 ${rows.length - 2} 条解释`}
        </button>
      )}
      {output?.result?.analysis?.literal && (
        <p className="analysis-literal">
          字面意思：{output.result.analysis.literal}
        </p>
      )}
      {output?.result?.analysis?.history_gap && (
        <p className="analysis-literal">
          历史缺口：{output.result.analysis.history_gap}
        </p>
      )}
      {output?.history_guard_applied && (
        <p className="uncertain-note">已启用历史缺口保护，候选改为请求核对。</p>
      )}
      {stages && (
        <div className="stages" aria-label="分析阶段">
          {Object.entries(stageNames).map(([key, label]) => (
            <span
              key={key}
              className={`stage ${stages[key]?.status || "pending"}`}
            >
              <b>{stageSymbols[stages[key]?.status || "pending"]}</b>
              {label}
            </span>
          ))}
        </div>
      )}
      {stages?.reply?.status === "failed" && (
        <p className="analysis-error">
          写回复失败；已有判断保留。重试会重新调用整轮模型。
        </p>
      )}
      {stages?.judge?.status === "failed" && (
        <p className="analysis-error">
          Jev 判断失败；假设尚未得到核对，不能由 DeepSeek 代判。
        </p>
      )}
      <div className="analysis-foot">
        <span>支持度是模型对原文的判断，未经校准。</span>
        {latest && card.state === "complete" && (
          <button className="inline-action" onClick={dismiss}>
            移除卡片
          </button>
        )}
      </div>
    </section>
  );
}

function EmotionIntentSummary({
  output,
  stages,
}: {
  output: Output;
  stages?: Record<string, { status: string; error?: string | null }>;
}) {
  const fromJev = output.pipeline_mode === "jev-dynamic" || !!output.jev;
  const signal = (key: "emotion" | "intent") => {
    const answer = output.jev?.answers?.[key];
    const saved = output.result?.scorecard?.[key];
    const label = fromJev
      ? answer?.choice || saved?.label
      : output.result?.analysis?.[key] || saved?.label;
    const rawProbability = label ? answer?.probabilities?.[label] : undefined;
    const probability =
      typeof rawProbability === "number" &&
      Number.isFinite(rawProbability) &&
      rawProbability >= 0 &&
      rawProbability <= 1
        ? rawProbability
        : typeof saved?.score === "number" &&
            Number.isFinite(saved.score) &&
            saved.score >= 0 &&
            saved.score <= 100
          ? saved.score / 100
          : undefined;
    return {
      label:
        label ||
        (stages?.judge?.status === "failed"
          ? "Jev 判断失败"
          : "尚未判断"),
      detail:
        fromJev && probability !== undefined
          ? `所选类别相对概率 ${Math.round(probability * 100)}%`
          : !fromJev && probability !== undefined
            ? `试验性匹配强度 ${Math.round(probability * 100)} / 100`
            : "",
    };
  };
  const emotion = signal("emotion");
  const intent = signal("intent");
  return (
    <div className="signal-summary" aria-label="情绪与意图分析">
      <div className="signal-item">
        <span>情绪</span>
        <strong>{emotion.label}</strong>
      </div>
      <div className="signal-item">
        <span>主要意图</span>
        <strong>{intent.label}</strong>
      </div>
      <details className="signal-details">
        <summary>查看分数与来源</summary>
        {emotion.detail && <p>情绪：{emotion.detail}</p>}
        {intent.detail && <p>意图：{intent.detail}</p>}
        <p className="signal-source">
          {fromJev
            ? "来源：Jev Choice 判断。类别概率只在给定选项内比较，未经中文聊天校准。"
            : "来源：DeepSeek 文本判断。匹配强度为模型自报，未经统计校准。"}
        </p>
      </details>
    </div>
  );
}

function HypothesisRow({
  row,
  locate,
}: {
  row: Hypothesis;
  locate: (sourceId: string) => void;
}) {
  const support =
    row.support_probability == null
      ? "未判断"
      : `原文支持度 ${(row.support_probability * 100).toFixed(0)}%`;
  const uncertain =
    row.evidence_status !== "sufficient" || row.status !== "judged";
  return (
    <div className="hypothesis">
      <div className="hypothesis-main">
        <strong>{row.hypothesis}</strong>
        <span className={`evidence-pill ${uncertain ? "uncertain" : ""}`}>
          {evidenceNames[row.evidence_status || "unjudged"] || "需核对"}
        </span>
      </div>
      <div className="support-line">{support}</div>
      {row.evidence_refs?.length || row.missing_context?.length ? (
        <details>
          <summary>
            查看原文依据 <CaretDown size={13} />
          </summary>
          {row.evidence_refs?.map((ref, index) => (
            ref.source_type === "message" ?
              <button className="quote" key={index} onClick={() => locate(String(ref.source_id))}>
                “{ref.quote}” <ArrowUpRight size={13} />
              </button> :
              <p className="missing" key={index}>已确认事实：“{ref.quote}”</p>
          ))}
          {row.missing_context?.map((text, index) => (
            <p className="missing" key={index}>
              待核实：{text}
            </p>
          ))}
        </details>
      ) : null}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
