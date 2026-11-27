import type { AiSuggestionOut, NewsItemOut } from "@sv/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";

export function NewsAndSuggestionsTab({ ticker }: { ticker: string }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);

  const news = useQuery({ queryKey: ["news", ticker], queryFn: () => api.news(ticker), enabled: !!ticker });
  const sugs = useQuery({ queryKey: ["suggestions", ticker], queryFn: () => api.suggestions(ticker), enabled: !!ticker });

  const genMut = useMutation({
    mutationFn: () => api.generateSuggestions(ticker, ["T1", "T2"]),
    onSuccess: (res) => {
      setNotice(t("news.genDone", { created: res.created, rejected: res.rejected_unverified }));
      void qc.invalidateQueries({ queryKey: ["suggestions", ticker] });
    },
  });

  const decideMut = useMutation({
    mutationFn: ({ id, status }: { id: number; status: "pending" | "accepted" | "dismissed" }) =>
      api.decideSuggestion(id, status),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["suggestions", ticker] }),
  });

  return (
    <div className="row" style={{ alignItems: "flex-start" }}>
      {/* Main column: T1/T2 verified AI suggestions with Accept/Dismiss workflow */}
      <section className="card col-main" aria-labelledby="sugs-h" style={{ gap: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <div>
            <h2 id="sugs-h" style={{ margin: 0 }}>{t("news.sugsTitle")}</h2>
            <span className="muted small">{t("news.sugsSubtitle")}</span>
          </div>
          <button className="btn primary small" type="button" disabled={genMut.isPending} onClick={() => genMut.mutate()}>
            {genMut.isPending ? t("news.generating") : t("news.generate")}
          </button>
        </div>

        {notice && <div className="banner info" role="status">{notice}</div>}
        {genMut.error && <div className="banner err" role="alert">{(genMut.error as Error).message}</div>}

        {!sugs.data?.length && !sugs.isLoading && (
          <p className="muted" style={{ margin: 0 }}>{t("news.emptySugs")}</p>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {sugs.data?.map((s: AiSuggestionOut) => {
            const isExt = /^https?:\/\//i.test(s.source_url);
            return (
              <article
                key={s.id}
                style={{
                  padding: 12,
                  borderRadius: 8,
                  border: "1px solid var(--sv-line-faint)",
                  opacity: s.status === "dismissed" ? 0.6 : 1,
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                  <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                    <span className="tag primary">{s.task_key}</span>
                    <strong>{s.title}</strong>
                  </div>
                  <span className={s.status === "accepted" ? "tag primary" : s.status === "dismissed" ? "tag neutral" : "tag warn"}>
                    {t(`news.status.${s.status}`)}
                  </span>
                </div>
                <p style={{ margin: 0, lineHeight: 1.55 }}>{s.detail}</p>
                <div className="small muted" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                  <span>{t("news.source")}: {s.source_title}{s.source_date ? ` (${s.source_date})` : ""}</span>
                  {isExt
                    ? <a href={s.source_url} target="_blank" rel="noopener noreferrer">{t("qa.openSource")}</a>
                    : <Link to={s.source_url}>{t("qa.openSource")}</Link>}
                  {s.decided_by && <span>· {s.decided_by}</span>}
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
                  {s.status !== "accepted" && (
                    <button
                      className="btn primary small"
                      type="button"
                      disabled={decideMut.isPending}
                      onClick={() => decideMut.mutate({ id: s.id, status: "accepted" })}
                    >
                      {t("news.accept")}
                    </button>
                  )}
                  {s.status !== "dismissed" && (
                    <button
                      className="btn secondary small"
                      type="button"
                      disabled={decideMut.isPending}
                      onClick={() => decideMut.mutate({ id: s.id, status: "dismissed" })}
                    >
                      {t("news.dismiss")}
                    </button>
                  )}
                  {s.status !== "pending" && (
                    <button
                      className="btn secondary small"
                      type="button"
                      disabled={decideMut.isPending}
                      onClick={() => decideMut.mutate({ id: s.id, status: "pending" })}
                    >
                      {t("news.reset")}
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      </section>

      {/* Side column: unified D4 news & filings stream */}
      <aside className="card col-side" aria-labelledby="news-feed-h" style={{ gap: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2 id="news-feed-h" style={{ margin: 0, fontSize: "var(--sv-fs-lg)" }}>{t("news.feedTitle")}</h2>
          <span className="tag neutral">{news.data?.length ?? 0}</span>
        </div>
        {news.isLoading && <p className="muted small">{t("common.loading")}</p>}
        {!news.data?.length && !news.isLoading && <p className="muted small" style={{ margin: 0 }}>{t("news.emptyFeed")}</p>}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {news.data?.map((n: NewsItemOut) => (
            <div key={n.id} style={{ padding: 8, borderRadius: 6, border: "1px solid var(--sv-line-faint)", display: "flex", flexDirection: "column", gap: 4 }}>
              <a href={n.url} target="_blank" rel="noopener noreferrer" style={{ fontWeight: 600 }}>{n.title}</a>
              <div className="small muted" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <span className="tag neutral">{n.source.toUpperCase()}</span>
                {n.published_at && <span>{n.published_at}</span>}
              </div>
              {n.summary && <span className="small muted" style={{ lineHeight: 1.4 }}>{n.summary}</span>}
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}
