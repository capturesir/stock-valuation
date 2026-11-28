import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";

type ObsRow = {
  ticker: string;
  as_of_date: string;
  fair_value: number;
  price: number;
  upside: number;
  fwd_date: string;
  fwd_price: number;
  fwd_return: number;
  bucket: "undervalued" | "fair" | "overvalued";
  source: string;
};

type ScorecardOut = {
  horizon_days: number;
  sample_count: number;
  tickers_tested: number;
  rank_ic: number | null;
  hit_rate: number | null;
  undervalued_avg_return: number | null;
  fair_avg_return: number | null;
  overvalued_avg_return: number | null;
  spread_under_minus_over: number | null;
  buckets: { undervalued: number; fair: number; overvalued: number };
  observations: ObsRow[];
};

const BUCKET_TAG: Record<string, string> = { undervalued: "ok", fair: "neutral", overvalued: "warn" };

export function BacktestScorecardCard({ tickers }: { tickers?: string[] }) {
  const { t, f } = useI18n();
  const key = (tickers ?? []).join(",");
  const q = useQuery({
    queryKey: ["backtest-scorecard", key],
    queryFn: () => api.backtestScorecard(tickers?.length ? tickers : undefined) as Promise<ScorecardOut>,
    staleTime: 5 * 60_000,
    retry: false,
  });

  const d = q.data;
  if (q.isLoading || !d) return null;

  return (
    <section className="card" aria-labelledby="bt-h" style={{ gap: 12 }}>
      <div>
        <h2 id="bt-h" style={{ fontSize: "var(--sv-fs-lg)", margin: 0 }}>{t("backtest.title")}</h2>
        <span className="muted small">{t("backtest.subtitle", { days: d.horizon_days })}</span>
      </div>

      {d.sample_count === 0 ? (
        <p className="muted small" style={{ margin: 0 }}>{t("backtest.empty")}</p>
      ) : (
        <>
          <div className="kv">
            <div><span className="muted small">{t("backtest.samples")}</span><strong>{d.sample_count} ({d.tickers_tested})</strong></div>
            <div><span className="muted small">{t("backtest.rankIc")}</span><strong>{d.rank_ic != null ? d.rank_ic.toFixed(3) : "—"}</strong></div>
            <div><span className="muted small">{t("backtest.hitRate")}</span><strong>{d.hit_rate != null ? f.pct(d.hit_rate, 1, false) : "—"}</strong></div>
            <div><span className="muted small">{t("backtest.underRet")}</span><strong className="pos">{d.undervalued_avg_return != null ? f.pct(d.undervalued_avg_return, 1, true) : "—"}</strong></div>
            <div><span className="muted small">{t("backtest.overRet")}</span><strong className="neg">{d.overvalued_avg_return != null ? f.pct(d.overvalued_avg_return, 1, true) : "—"}</strong></div>
            <div><span className="muted small">{t("backtest.spread")}</span>
              <strong className={(d.spread_under_minus_over ?? 0) >= 0 ? "pos" : "neg"}>
                {d.spread_under_minus_over != null ? f.pct(d.spread_under_minus_over, 1, true) : "—"}
              </strong>
            </div>
          </div>

          <div className="tbl-wrap">
            <table className="tbl" style={{ minWidth: 640 }}>
              <thead>
                <tr>
                  <th scope="col" className="l">{t("wl.col.stock")}</th>
                  <th scope="col">{t("backtest.col.date")}</th>
                  <th scope="col">{t("backtest.col.fv")}</th>
                  <th scope="col">{t("backtest.col.price")}</th>
                  <th scope="col">{t("backtest.col.upside")}</th>
                  <th scope="col">{t("backtest.col.fwdRet")}</th>
                  <th scope="col" className="l">{t("backtest.col.bucket")}</th>
                </tr>
              </thead>
              <tbody>
                {d.observations.slice(0, 15).map((o, i) => (
                  <tr key={`${o.ticker}-${o.as_of_date}-${i}`}>
                    <th scope="row" className="l">{o.ticker}</th>
                    <td>{o.as_of_date}</td>
                    <td>{f.value(o.fair_value)}</td>
                    <td>{f.value(o.price)}</td>
                    <td><span className={o.upside >= 0 ? "pos" : "neg"}>{f.pct(o.upside, 1, true)}</span></td>
                    <td><span className={o.fwd_return >= 0 ? "pos" : "neg"}>{f.pct(o.fwd_return, 1, true)}</span></td>
                    <td className="l"><span className={`tag ${BUCKET_TAG[o.bucket] ?? "neutral"}`}>{t(`backtest.bucket.${o.bucket}`)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
