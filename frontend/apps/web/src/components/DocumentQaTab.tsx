import type { QaCitationOut, StockDocumentOut } from "@sv/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useIsAdmin } from "../lib/session";
import { useI18n } from "../lib/i18n";
import { LoadingSpinner } from "./LoadingSpinner";

const STARTER_KEYS = ["overview", "financials", "valuation", "risks", "news"] as const;

export function DocumentQaTab({ ticker }: { ticker: string }) {
  const { t } = useI18n();
  const isAdmin = useIsAdmin();
  const isHk = /\.hk$/i.test(ticker);
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);

  const [threadId, setThreadId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [allowWeb, setAllowWeb] = useState(false);
  const [selectedDocId, setSelectedDocId] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const aiSettings = useQuery({ queryKey: ["ai-settings"], queryFn: api.aiSettings, staleTime: 60_000, retry: false });
  const aiConfigured = aiSettings.data ? !!aiSettings.data.configured : true;

  const docs = useQuery({ queryKey: ["docs", ticker], queryFn: () => api.documents(ticker), enabled: !!ticker });
  const docDetail = useQuery({
    queryKey: ["doc-detail", ticker, selectedDocId],
    queryFn: () => api.documentDetail(ticker, selectedDocId!),
    enabled: selectedDocId != null,
  });
  const threads = useQuery({ queryKey: ["qa-threads", ticker], queryFn: () => api.qaThreads(ticker), enabled: !!ticker });
  const activeThreadId = threadId ?? threads.data?.[0]?.id ?? null;
  const thread = useQuery({
    queryKey: ["qa-thread", ticker, activeThreadId],
    queryFn: () => api.qaThread(ticker, activeThreadId!),
    enabled: !!activeThreadId,
  });

  const syncMut = useMutation({
    mutationFn: () => api.syncDocuments(ticker, true, 3),
    onSuccess: (res) => {
      setNotice(t("qa.syncDone", { added: res.added, total: res.total_documents }));
      void qc.invalidateQueries({ queryKey: ["docs", ticker] });
    },
  });

  const uploadMut = useMutation({
    mutationFn: (file: File) => api.uploadDocument(ticker, file, file.name),
    onSuccess: (doc) => {
      setNotice(t("qa.uploadDone", { title: doc.title, chunks: doc.chunk_count }));
      void qc.invalidateQueries({ queryKey: ["docs", ticker] });
    },
  });

  const delDocMut = useMutation({
    mutationFn: (id: number) => api.deleteDocument(ticker, id),
    onSuccess: (_, id) => {
      if (selectedDocId === id) setSelectedDocId(null);
      void qc.invalidateQueries({ queryKey: ["docs", ticker] });
    },
  });

  const askMut = useMutation({
    mutationFn: (override?: { q?: string; web?: boolean }) =>
      api.qaAsk(ticker, {
        question: (override?.q ?? question).trim(),
        thread_id: activeThreadId ?? undefined,
        allow_web: override?.web ?? allowWeb,
      }),
    onSuccess: (res) => {
      setQuestion("");
      setThreadId(res.thread_id);
      void qc.invalidateQueries({ queryKey: ["qa-threads", ticker] });
      void qc.invalidateQueries({ queryKey: ["qa-thread", ticker, res.thread_id] });
      void qc.invalidateQueries({ queryKey: ["docs", ticker] });
    },
  });

  const delThreadMut = useMutation({
    mutationFn: (id: string) => api.deleteQaThread(ticker, id),
    onSuccess: () => {
      setThreadId(null);
      void qc.invalidateQueries({ queryKey: ["qa-threads", ticker] });
    },
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!question.trim() || askMut.isPending) return;
    askMut.mutate(undefined);
  };

  const askQuick = (text: string) => {
    if (askMut.isPending) return;
    setQuestion(text);
    askMut.mutate({ q: text });
  };

  const lastUserQuestion = (() => {
    const msgs = thread.data?.messages ?? [];
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === "user") return msgs[i].content;
    }
    return "";
  })();

  return (
    <div className="row" style={{ alignItems: "flex-start" }}>
      {/* Main column: private Q&A conversation */}
      <section className="card col-main" aria-labelledby="qa-heading" style={{ gap: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <div>
            <h2 id="qa-heading" style={{ margin: 0 }}>{t("qa.title")}</h2>
            <span className="muted small">{t("qa.subtitle")}</span>
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            {threads.data && threads.data.length > 0 && (
              <select
                aria-label={t("qa.threads")}
                value={activeThreadId ?? ""}
                onChange={(e) => setThreadId(e.target.value || null)}
                style={{ maxWidth: 220 }}
              >
                {threads.data.map((th) => (
                  <option key={th.id} value={th.id}>{th.title}</option>
                ))}
              </select>
            )}
            <button className="btn secondary small" type="button" onClick={() => setThreadId("")}>
              {t("qa.newThread")}
            </button>
            {activeThreadId && (
              <button
                className="btn secondary small"
                type="button"
                disabled={delThreadMut.isPending}
                onClick={() => delThreadMut.mutate(activeThreadId)}
              >
                {t("qa.deleteThread")}
              </button>
            )}
          </div>
        </div>

        {!aiConfigured && (
          <div className="banner warn" role="note" style={{ justifyContent: "space-between" }}>
            <span style={{ flex: "1 1 260px", lineHeight: 1.5 }}>{t("qa.noAiBanner")}</span>
            <Link className="btn secondary small" to="/settings">{t("qa.goSettings")}</Link>
          </div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 12, minHeight: 220 }}>
          {!thread.data?.messages?.length && !askMut.isPending && (
            <div style={{ display: "flex", flexDirection: "column", gap: 10, margin: "8px 0" }}>
              <p className="muted" style={{ margin: 0 }}>{t("qa.emptyChat", { ticker })}</p>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {STARTER_KEYS.map((k) => {
                  const qText = t(`qa.starter.${k}`, { ticker });
                  return (
                    <button
                      key={k}
                      type="button"
                      className="btn secondary small"
                      style={{ fontWeight: 400, whiteSpace: "normal", textAlign: "left" }}
                      onClick={() => askQuick(qText)}
                    >
                      {qText}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {thread.data?.messages?.map((m) => (
            <article
              key={m.id}
              style={{
                padding: 12,
                borderRadius: 8,
                border: "1px solid var(--sv-line-faint)",
                background: m.role === "user" ? "var(--sv-bg-subtle, rgba(127,127,127,0.06))" : "transparent",
                display: "flex",
                flexDirection: "column",
                gap: 8,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                <strong>{m.role === "user" ? t("qa.role.user") : t("qa.role.assistant")}</strong>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                  {m.used_statements && <span className="tag primary">{t("qa.badge.statements")}</span>}
                  {(m.citations ?? []).some((c) => c.source_type === "library") && <span className="tag neutral">{t("qa.badge.library")}</span>}
                  {m.used_web && <span className="tag warn">{t("qa.badge.web")}</span>}
                  {m.insufficient_evidence && <span className="tag neutral">{t("qa.badge.insufficient")}</span>}
                  {m.role === "assistant" && !m.model && <span className="tag warn">{t("qa.badge.noAi")}</span>}
                  {m.model && <span className="muted small">{m.model}</span>}
                </div>
              </div>
              <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.65 }}>{m.content}</div>
              {m.role === "assistant" && m.insufficient_evidence && (
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", paddingTop: 4 }}>
                  {!allowWeb && lastUserQuestion && (
                    <button
                      type="button"
                      className="btn secondary small"
                      disabled={askMut.isPending}
                      onClick={() => { setAllowWeb(true); askMut.mutate({ q: lastUserQuestion, web: true }); }}
                    >
                      {t("qa.retryWithWeb")}
                    </button>
                  )}
                  {!docs.data?.length && (
                    <button
                      type="button"
                      className="btn secondary small"
                      disabled={syncMut.isPending}
                      onClick={() => syncMut.mutate()}
                    >
                      {t("qa.syncLibrary")}
                    </button>
                  )}
                  {!aiConfigured && (
                    <Link className="btn secondary small" to="/settings">{t("qa.goSettings")}</Link>
                  )}
                </div>
              )}
              {(m.citations ?? []).length > 0 && (
                <div style={{ borderTop: "1px solid var(--sv-line-faint)", paddingTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
                  <span className="muted small">{t("qa.citations")}</span>
                  {(m.citations ?? []).map((c) => <CitationItem key={`${m.id}-${c.id}`} c={c} />)}
                </div>
              )}
            </article>
          ))}

          {askMut.isPending && (
            <div className="banner info" role="status" aria-live="polite">
              <LoadingSpinner size="sm" label={t("qa.asking")} />
              <span>{t("qa.asking")}</span>
            </div>
          )}
        </div>

        {askMut.error && (
          <div className="banner err" role="alert">{(askMut.error as Error).message}</div>
        )}

        <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <textarea
            rows={3}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={t("qa.placeholder", { ticker })}
            aria-label={t("qa.title")}
            maxLength={2000}
            style={{ width: "100%", resize: "vertical" }}
          />
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }} className="small">
              <input type="checkbox" checked={allowWeb} onChange={(e) => setAllowWeb(e.target.checked)} />
              {t("qa.allowWeb")}
            </label>
            <button className="btn primary" type="submit" disabled={!question.trim() || askMut.isPending}>
              {askMut.isPending
                ? <span className="loading-row"><LoadingSpinner size="sm" label={t("qa.asking")} />{t("qa.asking")}</span>
                : t("qa.ask")}
            </button>
          </div>
        </form>
      </section>

      {/* Side column: shared stock document library */}
      <aside className="card col-side" aria-labelledby="docs-heading" style={{ gap: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <h2 id="docs-heading" style={{ margin: 0, fontSize: "var(--sv-fs-lg)" }}>{t("qa.libraryTitle")}</h2>
          <span className="tag neutral">{docs.data?.length ?? 0}</span>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            className="btn secondary small"
            type="button"
            disabled={syncMut.isPending}
            onClick={() => syncMut.mutate()}
          >
            {syncMut.isPending ? <span className="loading-row"><LoadingSpinner size="sm" label={t("qa.syncing")} />{t("qa.syncing")}</span> : t("qa.syncLibrary")}
          </button>
          <button
            className="btn secondary small"
            type="button"
            disabled={uploadMut.isPending}
            onClick={() => fileRef.current?.click()}
          >
            {uploadMut.isPending ? <span className="loading-row"><LoadingSpinner size="sm" label={t("common.loading")} />{t("common.loading")}</span> : t("qa.upload")}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.md,.markdown,.html,.htm,.txt"
            style={{ display: "none" }}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) uploadMut.mutate(file);
              e.target.value = "";
            }}
          />
        </div>

        {notice && <div className="banner info" role="status">{notice}</div>}
        {(syncMut.error || uploadMut.error) && (
          <div className="banner err" role="alert">{((syncMut.error ?? uploadMut.error) as Error).message}</div>
        )}

        {!docs.data?.length && !docs.isLoading && (
          <p className="muted small" style={{ margin: 0 }}>{t(isHk ? "qa.emptyLibrary.hkex" : "qa.emptyLibrary")}</p>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {docs.data?.map((d: StockDocumentOut) => (
            <div
              key={d.id}
              style={{
                padding: 8,
                borderRadius: 6,
                border: "1px solid var(--sv-line-faint)",
                display: "flex",
                flexDirection: "column",
                gap: 4,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", gap: 6, alignItems: "flex-start" }}>
                <button
                  type="button"
                  onClick={() => setSelectedDocId(selectedDocId === d.id ? null : d.id)}
                  style={{ background: "none", border: "none", padding: 0, textAlign: "left", cursor: "pointer", fontWeight: 600, color: "inherit" }}
                >
                  {d.title}
                </button>
                {isAdmin && (
                  <button
                    className="btn secondary small"
                    type="button"
                    onClick={() => delDocMut.mutate(d.id)}
                    aria-label={t("qa.deleteDoc")}
                  >
                    ×
                  </button>
                )}
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }} className="small muted">
                <span className="tag neutral">{t(`qa.kind.${d.source_kind}`)}</span>
                {d.doc_date && <span>{d.doc_date}</span>}
                <span>{t("qa.chunks", { n: d.chunk_count })}</span>
                {d.url && d.url.startsWith("http") && (
                  <a href={d.url} target="_blank" rel="noopener noreferrer">{t("qa.openSource")}</a>
                )}
              </div>
              {selectedDocId === d.id && docDetail.data && (
                <div style={{ marginTop: 6, paddingTop: 6, borderTop: "1px solid var(--sv-line-faint)", fontSize: 13 }}>
                  {docDetail.data.summary && <p style={{ margin: "0 0 6px", lineHeight: 1.5 }}>{docDetail.data.summary}</p>}
                  {(docDetail.data.section_summaries ?? []).slice(0, 6).map((s, idx) => (
                    <div key={idx} className="small muted" style={{ marginBottom: 4 }}>
                      <strong>{String(s.section ?? "")}</strong>: {String(s.summary ?? "")}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}

function CitationItem({ c }: { c: QaCitationOut }) {
  const { t } = useI18n();
  const badgeKey = c.source_type === "web" ? "qa.badge.web" : c.source_type === "statements" ? "qa.badge.statements" : "qa.badge.library";
  const isExt = !!c.url && /^https?:\/\//i.test(c.url);
  return (
    <div style={{ fontSize: 13, lineHeight: 1.5, display: "flex", flexDirection: "column", gap: 2 }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <strong>[{c.id}]</strong>
        <span className={c.source_type === "web" ? "tag warn" : c.source_type === "statements" ? "tag primary" : "tag neutral"}>
          {t(badgeKey)}
        </span>
        <span>{c.title}{c.section ? ` · ${c.section}` : ""}</span>
        {(c.fetched_at || c.doc_date) && <span className="muted small">({c.fetched_at ?? c.doc_date})</span>}
        {c.url && (isExt
          ? <a href={c.url} target="_blank" rel="noopener noreferrer">{t("qa.openSource")}</a>
          : <Link to={c.url}>{t("qa.openSource")}</Link>)}
      </div>
      {c.quote && <span className="muted small">{c.quote}</span>}
    </div>
  );
}
