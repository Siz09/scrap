import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Job, type Quality, type ScraperInfo, type SourceRow } from "../api";
import { useApp } from "../context";
import { relTime } from "../format";

const ROLE_LABELS: Record<string, string> = {
  offers: "Store (prices)", reference: "Listed prices", specs: "Specs", reviews: "Expert reviews",
};
const REGION_LABELS: Record<string, string> = { np: "Nepal", "np-ref": "Nepal", intl: "International" };

export default function Sources() {
  const { meta, refreshMeta } = useApp();
  const [rows, setRows] = useState<SourceRow[]>([]);
  const [file, setFile] = useState("");
  const [scrapers, setScrapers] = useState<ScraperInfo[]>([]);
  const [quality, setQuality] = useState<Quality | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  const load = useCallback(() => {
    api.sources().then((r) => { setRows(r.sources); setFile(r.file); setScrapers(r.scrapers); }).catch((e) => setError(e.message));
    api.quality().then(setQuality).catch(() => setQuality(null));
  }, []);
  useEffect(load, [load]);

  // Resume watching a job that's still running (e.g. after a page reload).
  useEffect(() => {
    api.jobs().then((r) => { const running = r.jobs.find((j) => j.status === "running"); if (running) setJob(running); });
  }, []);

  useEffect(() => {
    if (!job || job.status !== "running") return;
    const t = setInterval(() => {
      api.job(job.id).then((j) => {
        setJob(j);
        if (j.status !== "running") { load(); refreshMeta(); }
      });
    }, 1000);
    return () => clearInterval(t);
  }, [job, load, refreshMeta]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [job?.log.length]);

  const locked = meta.read_only || meta.sample;
  const running = job?.status === "running";

  async function start(kind: "check" | "scrape", names: string[] = []) {
    setError(null);
    try {
      setJob(await api.startJob(kind, names));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const enabled = rows.filter((r) => r.enabled);
  const working = enabled.filter((r) => r.check === "OK").length;
  const checked = enabled.filter((r) => r.check).length;

  return (
    <div className="sources">
      <div className="sources-head">
        <div>
          <h1>Data sources</h1>
          <p className="muted">
            {checked ? `${working} of ${enabled.length} enabled sources working at last check.` : "Not checked yet."}{" "}
            Prices come from Nepali stores; specs and review scores from international sites.
          </p>
        </div>
        <div className="actions">
          <button type="button" className="btn ghost" disabled={locked || running} onClick={() => start("check")}>
            Check sources
          </button>
          <button type="button" className="btn primary" disabled={locked || running} onClick={() => start("scrape")}>
            Update prices
          </button>
        </div>
      </div>

      {locked && (
        <p className="notice">
          {meta.read_only
            ? "This server is read-only: scraping runs elsewhere."
            : "Sample mode: restart without --sample to scrape real sources."}
        </p>
      )}
      {error && <p className="notice bad">{error}</p>}

      {job && (
        <section className="panel job">
          <div className="job-head">
            <h2>{job.kind === "check" ? "Checking sources" : "Updating prices"} <span className={`badge ${job.status === "done" ? "good" : job.status === "failed" ? "low" : ""}`}>{job.status}</span></h2>
            {running && <button type="button" className="btn ghost small" onClick={() => api.cancelJob()}>Stop</button>}
          </div>
          <pre ref={logRef} className="log">{job.log.join("\n") || "Starting…"}</pre>
        </section>
      )}

      <div className="pipeline">
        <section className="panel">
          <h2>Scrapers</h2>
          <p className="muted small">Tried in this order. When a site blocks one, the next takes over, and the winner is remembered for that site.</p>
          <ol className="scrapers">
            {scrapers.map((b) => (
              <li key={b.name} className={b.available ? "" : "off"}>
                <span>{b.name}</span>
                <span className={`badge ${b.available ? "good" : ""}`}>{b.available ? "installed" : "not installed"}</span>
              </li>
            ))}
          </ol>
        </section>
        <section className="panel">
          <h2>Data quality</h2>
          {quality ? (
            <>
              <p className="muted small">
                {quality.raw_records.toLocaleString("en-IN")} raw records kept for reprocessing.{" "}
                {Object.entries(quality.by_kind).map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(" · ") || "No issues logged."}
              </p>
              {quality.top.length > 0 && (
                <ul className="issues">
                  {quality.top.slice(0, 6).map((q) => (
                    <li key={q.source + q.kind + q.field}>
                      <b>{q.n}×</b> {q.source}: {q.kind.replace("_", " ")} <code>{q.field}</code>
                      <div className="muted small clamp">{q.example}</div>
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : <p className="muted small">No data yet.</p>}
        </section>
      </div>

      <div className="table-wrap">
        <table className="sources-table">
          <thead>
            <tr><th>Source</th><th>Provides</th><th>Platform</th><th>Last check</th><th>Last update</th><th><span className="sr-only">Actions</span></th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name} className={r.enabled ? "" : "disabled"}>
                <td>
                  <div className="src-name">
                    {r.url ? <a href={r.url} target="_blank" rel="noopener noreferrer">{r.name}</a> : r.name}
                    {!r.enabled && <span className="badge">off</span>}
                  </div>
                  {r.notes && <div className="muted small clamp">{r.notes}</div>}
                </td>
                <td>{ROLE_LABELS[r.role ?? ""] ?? r.role}<div className="muted small">{REGION_LABELS[r.region ?? ""] ?? r.region}</div></td>
                <td>{r.platform ?? "not detected yet"}{!r.verified && <div className="muted small">unverified</div>}</td>
                <td>
                  {r.check ? <span className={`badge ${r.check === "OK" ? "good" : "low"}`}>{r.check}</span> : <span className="muted">—</span>}
                  {r.check_detail && <div className="muted small clamp" title={r.check_detail}>{r.check_detail}</div>}
                </td>
                <td>{r.last_scraped_at ? <>{r.last_scrape_count} items{r.last_rejected ? <span className="muted small"> ({r.last_rejected} rejected)</span> : null}<div className="muted small">{relTime(r.last_scraped_at)}</div></> : <span className="muted">—</span>}</td>
                <td className="row-actions">
                  <button type="button" className="link small" disabled={locked || running} onClick={() => start("check", [r.name])}>Check</button>
                  <button type="button" className="link small" disabled={locked || running} onClick={() => start("scrape", [r.name])}>Update</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        To add a store or fix a failing one, edit <code>{file}</code> (add <code>{'{"name": "...", "type": "auto", "base_url": "https://..."}'}</code>) and press Check.
        Respect each site's terms; DeviceScout waits between requests and follows robots.txt.
      </p>
    </div>
  );
}
