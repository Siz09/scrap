import { useCallback, useEffect, useRef, useState } from "react";
import { adminKey, api, ApiError, setAdminKey, type Job, type Quality, type ScraperInfo, type SourceRow } from "../api";
import { useApp } from "../context";
import { relTime } from "../format";

const ROLE_LABELS: Record<string, string> = {
  offers: "Store (prices)", reference: "Listed prices", specs: "Specs", reviews: "Expert reviews",
};
const REGION_LABELS: Record<string, string> = { np: "Nepal", "np-ref": "Nepal", intl: "International" };
const STATUS: Record<string, { label: string; tone: string }> = {
  OK: { label: "Working", tone: "good" },
  PARTIAL: { label: "Partial", tone: "warn" },
  FAIL: { label: "Failing", tone: "low" },
  RUNNING: { label: "Checking…", tone: "" },
};

export default function Sources() {
  const { meta, refreshMeta } = useApp();
  const [rows, setRows] = useState<SourceRow[]>([]);
  const [file, setFile] = useState("");
  const [scrapers, setScrapers] = useState<ScraperInfo[]>([]);
  const [quality, setQuality] = useState<Quality | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [viewId, setViewId] = useState<string | null>(null);   // the job shown in the panel
  const [workerSeen, setWorkerSeen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [key, setKey] = useState(adminKey());
  const [keyInput, setKeyInput] = useState("");
  const [unlocked, setUnlocked] = useState(!meta.admin_required);
  const [newUrl, setNewUrl] = useState("");
  const [newRole, setNewRole] = useState("offers");
  const logRef = useRef<HTMLPreElement>(null);

  const load = useCallback(() => {
    api.sources().then((r) => {
      setRows(r.sources); setFile(r.file); setScrapers(r.scrapers);
      setError(r.error ? `Source list problem: ${r.error}` : null);
    }).catch((e) => setError(e.message));
    api.quality().then(setQuality).catch(() => setQuality(null));
    api.jobs().then((r) => {
      setWorkerSeen(r.worker_seen_at);
      setJobs(r.jobs);
    }).catch(() => undefined);
  }, []);
  useEffect(load, [load]);

  // Check a saved admin key once.
  useEffect(() => {
    if (!meta.admin_required || !key) return;
    api.verifyAdmin(key).then(() => setUnlocked(true)).catch(() => { setAdminKey(""); setKey(""); setUnlocked(false); });
  }, [meta.jobs_mode, key]);

  // Several jobs can run at once: the scheduled scrape in its own lane, plus checks/updates
  // started here. The panel shows the one picked (default: the newest running or queued).
  const isActive = (j: Job) => j.status === "running" || j.status === "queued";
  const activeJobs = jobs.filter(isActive);
  const job = jobs.find((j) => j.id === viewId) ?? activeJobs[0] ?? jobs[0] ?? null;
  const active = activeJobs.length > 0 || rows.some((r) => r.check === "RUNNING" || r.scrape_running);
  const allSourcesBusy = (kind: string) => activeJobs.some((j) => j.kind === kind && j.names.length === 0);
  const sourceBusy = (r: SourceRow) =>
    r.check === "RUNNING" || !!r.scrape_running || activeJobs.some((j) => j.names.includes(r.name));

  // Live updates while anything is running (including scheduled runs started by the scraper container).
  useEffect(() => {
    const t = setInterval(() => {
      load();
      if (!active) refreshMeta();
    }, active ? 2000 : 15000);
    return () => clearInterval(t);
  }, [active, load, refreshMeta]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [job?.log.length]);

  async function unlock(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.verifyAdmin(keyInput);
      setAdminKey(keyInput);
      setKey(keyInput);
      setUnlocked(true);
      setKeyInput("");
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "That key isn't right." : (err as Error).message);
    }
  }

  function onAuthError(e: unknown) {
    if (e instanceof ApiError && e.status === 401) { setAdminKey(""); setKey(""); setUnlocked(false); }
    setError((e as Error).message);
  }

  async function addSource(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const r = await api.addSource(newUrl.trim(), newRole);
      setNewUrl("");
      if (r.job) { setJobs((js) => [r.job!, ...js]); setViewId(r.job.id); }
      load();
    } catch (err) {
      onAuthError(err);
    }
  }

  async function toggle(r: SourceRow) {
    try { await api.setSourceEnabled(r.name, !r.enabled); load(); } catch (e) { onAuthError(e); }
  }

  async function remove(r: SourceRow) {
    if (!window.confirm(`Remove ${r.name} from the source list? Products already scraped from it stay.`)) return;
    try { await api.removeSource(r.name); load(); } catch (e) { onAuthError(e); }
  }

  async function start(kind: "check" | "scrape", names: string[] = []) {
    setError(null);
    try {
      const started = await api.startJob(kind, names);
      setJobs((js) => [started, ...js.filter((j) => j.id !== started.id)]);
      setViewId(started.id);
    } catch (e) {
      onAuthError(e);
    }
  }

  const enabled = rows.filter((r) => r.enabled);
  const count = (s: string) => enabled.filter((r) => r.check === s).length;
  const unchecked = enabled.filter((r) => !r.check).length;
  const canRun = meta.jobs_mode !== "off" && (!meta.admin_required || unlocked);
  const busy = !!job && isActive(job);
  const p = job?.progress ?? {};
  const pct = p.total ? Math.round(((p.done ?? 0) / p.total) * 100) : 0;
  const workerAge = workerSeen ? (Date.now() - new Date(workerSeen).getTime()) / 1000 : Infinity;
  const workerOnline = meta.jobs_mode === "local" || workerAge < 90;

  return (
    <div className="sources">
      <div className="sources-head">
        <div>
          <h1>Data sources</h1>
          <p className="muted">
            {[
              count("OK") && `${count("OK")} working`,
              count("PARTIAL") && `${count("PARTIAL")} partial`,
              count("FAIL") && `${count("FAIL")} failing`,
              count("RUNNING") && `${count("RUNNING")} being checked`,
              unchecked && `${unchecked} not checked yet`,
            ].filter(Boolean).join(" · ") || "No sources enabled."}
            {" "}of {enabled.length} enabled sources.
          </p>
        </div>
        {canRun && (
          <div className="actions">
            <button type="button" className="btn ghost" disabled={allSourcesBusy("check")} onClick={() => start("check")}>Check sources</button>
            <button type="button" className="btn primary" disabled={allSourcesBusy("scrape")} onClick={() => start("scrape")}>Update prices</button>
          </div>
        )}
      </div>

      {meta.jobs_mode === "queue" && !workerOnline && (
        <p className="notice bad">
          The scraper isn't running{workerSeen ? ` (last seen ${relTime(workerSeen)})` : ""}. Jobs will wait in the
          queue until it starts: <code>docker compose up -d scraper</code>.
        </p>
      )}
      {meta.jobs_mode === "off" && (
        <p className="notice">
          {meta.sample
            ? "Sample mode: restart without --sample to scrape real sources."
            : "This site was started with --read-only, so scraping can't be started from here."}
        </p>
      )}
      {meta.admin_required && !unlocked && (
        <form className="notice admin" onSubmit={unlock}>
          <label htmlFor="admin-key">Admin key</label>
          <input id="admin-key" type="password" autoComplete="current-password" value={keyInput}
                 onChange={(e) => setKeyInput(e.target.value)} placeholder="DEVICESCOUT_ADMIN_KEY" />
          <button type="submit" className="btn primary">Unlock</button>
          <span className="muted small">This site has an admin key set, so only you can start scraping.</span>
        </form>
      )}
      {meta.admin_required && unlocked && (
        <p className="muted small">
          Unlocked on this browser. <button type="button" className="link small" onClick={() => { setAdminKey(""); setKey(""); setUnlocked(false); }}>Lock</button>
        </p>
      )}
      {canRun && (
        <form className="panel add-source" onSubmit={addSource}>
          <label htmlFor="new-url"><b>Add a store or site</b> <span className="muted small">paste its link, or a category page like …/mobile-phones</span></label>
          <div className="add-row">
            <input id="new-url" type="url" required placeholder="https://www.example.com.np/mobile-phones"
                   value={newUrl} onChange={(e) => setNewUrl(e.target.value)} />
            <select value={newRole} onChange={(e) => setNewRole(e.target.value)} aria-label="What this site provides">
              <option value="offers">Store (prices)</option>
              <option value="reference">Price list site</option>
              <option value="specs">Spec database</option>
              <option value="reviews">Review site</option>
            </select>
            <button type="submit" className="btn primary">Add &amp; check</button>
          </div>
        </form>
      )}
      {error && <p className="notice bad">{error}</p>}

      {job && (
        <section className="panel job">
          <div className="job-head">
            <h2>
              {job.kind === "check" ? "Checking" : "Updating"} {job.names.length ? job.names.join(", ") : "all sources"}
              {job.origin === "schedule" && <span className="muted small"> (scheduled)</span>}{" "}
              <span className={`badge ${job.status === "done" ? "good" : job.status === "failed" ? "low" : ""}`}>
                {job.status === "queued" ? "waiting for the scraper" : job.status}
              </span>
            </h2>
            {busy && canRun && <button type="button" className="btn ghost small" onClick={() => api.cancelJob(job.id).then(load)}>Stop</button>}
          </div>
          {activeJobs.filter((j) => j.id !== job.id).length > 0 && (
            <p className="muted small">
              Also running:{" "}
              {activeJobs.filter((j) => j.id !== job.id).map((j) => (
                <button key={j.id} type="button" className="link small" onClick={() => setViewId(j.id)}>
                  {j.kind === "check" ? "Check" : "Update"} {j.names.length ? j.names.join(", ") : "all sources"}
                  {j.origin === "schedule" ? " (scheduled)" : ""}
                  {j.progress.total ? ` · ${j.progress.done ?? 0}/${j.progress.total}` : ""}
                </button>
              ))}
            </p>
          )}
          {busy && p.total ? (
            <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={p.total} aria-valuenow={p.done ?? 0}>
              <div className="progress-bar" style={{ width: `${pct}%` }} />
              <span className="progress-label">
                {p.done ?? 0} of {p.total} done{p.current ? ` · now: ${p.current}` : ""}
              </span>
            </div>
          ) : null}
          <pre ref={logRef} className="log">{job.log.join("\n") || (job.status === "queued" ? "Queued…" : "Starting…")}</pre>
        </section>
      )}

      <div className="pipeline">
        <section className="panel">
          <h2>Scrapers</h2>
          <p className="muted small">
            Tried in this order. When a site blocks one, the next takes over, and the winner is remembered for that site.
            {meta.jobs_mode !== "local" && " Shown for the scraper container."}
          </p>
          {scrapers.length === 0 && <p className="muted small">The scraper hasn't reported yet. Is it running?</p>}
          <ol className="scrapers">
            {scrapers.map((b) => (
              <li key={b.name} className={b.available ? "" : "off"}>
                <div>
                  <span>{b.name}</span>
                  {b.missing && <div className="muted small">{b.missing}</div>}
                </div>
                <span className={`badge ${b.available ? "good" : ""}`}>{b.available ? "ready" : "not available"}</span>
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
            <tr><th>Source</th><th>Provides</th><th>Platform</th><th>Last check</th><th>Last update</th>{canRun && <th><span className="sr-only">Actions</span></th>}</tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const st = r.check ? STATUS[r.check] : null;
              return (
                <tr key={r.name} className={r.enabled ? "" : "disabled"}>
                  <td>
                    <div className="src-name">
                      {r.name}
                      {!r.enabled && <span className="badge">disabled</span>}
                    </div>
                    {r.url && <a className="src-url small" href={r.url} target="_blank" rel="noopener noreferrer">{r.url.replace(/^https?:\/\//, "")}</a>}
                    {r.notes && <div className="muted small clamp">{r.notes}</div>}
                  </td>
                  <td>{ROLE_LABELS[r.role ?? ""] ?? r.role}<div className="muted small">{REGION_LABELS[r.region ?? ""] ?? r.region}</div></td>
                  <td>{r.platform ?? "not detected yet"}{!r.verified && <div className="muted small">unverified</div>}</td>
                  <td>
                    {st ? <span className={`badge ${st.tone}`}>{r.check === "RUNNING" && <span className="spinner inline" />}{st.label}</span>
                      : <span className="muted">not checked</span>}
                    {r.check !== "RUNNING" && r.check_detail && <div className="muted small clamp" title={r.check_detail}>{r.check_detail}</div>}
                    {r.checked_at && r.check !== "RUNNING" && <div className="muted small">{relTime(r.checked_at)}</div>}
                  </td>
                  <td>
                    {r.scrape_running ? <span className="badge"><span className="spinner inline" />Updating…</span>
                      : r.last_scraped_at ? <>{r.last_scrape_count} items{r.last_rejected ? <span className="muted small"> ({r.last_rejected} rejected)</span> : null}<div className="muted small">{relTime(r.last_scraped_at)}</div></>
                      : <span className="muted">—</span>}
                    {r.raw_pages || r.raw_records ? (
                      <div className="muted small" title="Kept in the raw layer exactly as fetched, so it can be re-cleaned later without scraping again">
                        stored: {(r.raw_pages ?? 0).toLocaleString()} pages · {(r.raw_records ?? 0).toLocaleString()} records
                      </div>) : null}
                  </td>
                  {canRun && (
                    <td className="row-actions">
                      <button type="button" className="link small" disabled={sourceBusy(r) || !r.enabled} onClick={() => start("check", [r.name])}>Check</button>
                      <button type="button" className="link small" disabled={sourceBusy(r) || !r.enabled} onClick={() => start("scrape", [r.name])}>Update</button>
                      <button type="button" className="link small" onClick={() => toggle(r)}>{r.enabled ? "Disable" : "Enable"}</button>
                      <button type="button" className="link small danger" onClick={() => remove(r)}>Remove</button>
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        To add a store or fix a failing one, edit <code>{file}</code> (add <code>{'{"name": "...", "type": "auto", "base_url": "https://..."}'}</code>) and run a check.
        DeviceScout waits between requests and follows robots.txt.
      </p>
    </div>
  );
}
