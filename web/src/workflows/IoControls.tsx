/**
 * The board's io group: Import, Export and the repository picker.
 *
 *   Import  — choose .json/.yaml files (or a bundle Export wrote); they are
 *             POSTed to /import as a dry run, the plan is shown, and only
 *             "Apply import" posts again with apply=true.
 *   Export  — GET /export?format=json, offered as one bundle download.
 *   Repo    — lists the configured repositories from GET /repos (on any
 *             failure the picker says "No repository" instead of raising a
 *             page error). Its menu also holds "Import from repo"
 *             (POST /import {repo}) and "Export to repo" (POST /export
 *             {repo}, a commit): both show the dry-run plan first and write
 *             only on "Apply".
 */
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import {
  exportDefinitions,
  exportToRepo,
  importDefinitions,
  importFromRepo,
  listRepos,
  type ImportChange,
  type ImportPlan,
  type Repo,
} from "../api/workflows";
import Panel from "./Panel";

const KIND_HINTS: [string, string[]][] = [
  ["rules", ["trigger"]],
  ["workflows", ["steps", "edges"]],
  ["machines", ["platform"]],
  ["actors", ["harness", "model"]],
];

/** A chosen file -> the `<kind>/<id>.<ext>` paths /import expects. */
export function importPaths(name: string, text: string): Record<string, string> {
  const dot = name.lastIndexOf(".");
  const stem = dot > 0 ? name.slice(0, dot) : name;
  const ext = dot > 0 ? name.slice(dot + 1).toLowerCase() : "json";
  if (name.includes("/")) return { [name]: text };
  if (ext === "json") {
    try {
      const doc = JSON.parse(text) as Record<string, unknown>;
      // A bundle Export wrote: {format, files}.
      if (doc && typeof doc.files === "object" && doc.files !== null && "format" in doc) {
        return doc.files as Record<string, string>;
      }
      const kind = KIND_HINTS.find(([, keys]) => keys.some((k) => k in doc))?.[0] ?? "workflows";
      const id = typeof doc.id === "string" && doc.id ? doc.id : stem;
      return { [`${kind}/${id}.json`]: text };
    } catch {
      return { [`workflows/${stem}.json`]: text };
    }
  }
  return { [`workflows/${stem}.${ext}`]: text };
}

/** `File.text()`, with a FileReader fallback for engines that lack it. */
function readText(file: File): Promise<string> {
  if (typeof file.text === "function") return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

const message = (err: unknown) => (err instanceof ApiError ? err.message : String(err));
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** A plan waiting for "Apply": from chosen files, or from / to a repository. */
type Pending =
  | { kind: "import"; files: Record<string, string>; plan: ImportPlan }
  | { kind: "import"; repo: string; plan: ImportPlan }
  | { kind: "export"; repo: string; plan: { changes: ImportChange[]; errors?: ImportPlan["errors"] } };

export function IoControls({
  onImported,
  onStatus,
  onError,
}: {
  onImported: () => void;
  onStatus: (text: string) => void;
  onError: (text: string) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const importRef = useRef<HTMLButtonElement>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [repos, setRepos] = useState<Repo[] | null>(null);
  const [repo, setRepo] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const pickerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    listRepos(controller.signal)
      .then((items) => {
        setRepos(items);
        setRepo((current) => current ?? items[0]?.name ?? null);
      })
      .catch(() => {
        if (!controller.signal.aborted) setRepos([]);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (menuOpen) listRef.current?.querySelector<HTMLElement>('[aria-selected="true"], [role="option"]')?.focus();
  }, [menuOpen]);

  const onFiles = async (list: FileList | null) => {
    if (!list || list.length === 0) return;
    const files: Record<string, string> = {};
    for (const file of Array.from(list)) Object.assign(files, importPaths(file.name, await readText(file)));
    if (fileRef.current) fileRef.current.value = "";
    try {
      const plan = await importDefinitions(files, false);
      setPending({ kind: "import", files, plan });
    } catch (err) {
      onError(`Import failed: ${message(err)}`);
    }
  };

  const fromRepo = async () => {
    if (!repo) return;
    setMenuOpen(false);
    try {
      setPending({ kind: "import", repo, plan: await importFromRepo(repo, false) });
    } catch (err) {
      onError(`Import from ${repo} failed: ${message(err)}`);
    }
  };

  const toRepo = async () => {
    if (!repo) return;
    setMenuOpen(false);
    try {
      setPending({ kind: "export", repo, plan: await exportToRepo(repo, false) });
    } catch (err) {
      onError(`Export to ${repo} failed: ${message(err)}`);
    }
  };

  const apply = async () => {
    if (!pending) return;
    const where = "repo" in pending ? ` ${pending.kind === "export" ? "to" : "from"} ${pending.repo}` : "";
    try {
      if (pending.kind === "export") {
        const result = await exportToRepo(pending.repo, true);
        setPending(null);
        const n = result.changes.filter((c) => c.action !== "unchanged").length;
        onStatus(`Exported ${plural(n, "change")}${where}${result.commit ? ` (${result.commit.slice(0, 7)})` : ""}`);
        return;
      }
      const plan =
        "repo" in pending ? await importFromRepo(pending.repo, true) : await importDefinitions(pending.files, true);
      setPending(null);
      onStatus(`Imported ${plural(plan.changes.length, "change")}${where}`);
      onImported();
    } catch (err) {
      setPending(null);
      onError(`${pending.kind === "export" ? "Export" : "Import"}${where} failed: ${message(err)}`);
    }
  };

  const doExport = async () => {
    try {
      const result = await exportDefinitions("json");
      const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `culture-rules-export${repo ? `-${repo.replace(/[^A-Za-z0-9]+/g, "-")}` : ""}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      const n = Object.keys(result.files).length;
      onStatus(`Exported ${n} file${n === 1 ? "" : "s"}`);
    } catch (err) {
      onError(`Export failed: ${message(err)}`);
    }
  };

  const choose = (name: string) => {
    setRepo(name);
    setMenuOpen(false);
    pickerRef.current?.focus();
  };

  return (
    <span className="wf-io">
      <button type="button" className="wf-button" ref={importRef} onClick={() => fileRef.current?.click()}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M12 4v11M7 10l5 5 5-5M5 20h14" />
        </svg>
        Import
      </button>
      <input
        ref={fileRef}
        type="file"
        className="sr-only"
        aria-label="Import files"
        tabIndex={-1}
        multiple
        accept=".json,.yaml,.yml,application/json"
        onChange={(e) => void onFiles(e.target.files)}
      />
      <button type="button" className="wf-button" onClick={() => void doExport()}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M12 15V4M7 9l5-5 5 5M5 20h14" />
        </svg>
        Export
      </button>
      <span className="wf-repo">
        <button
          type="button"
          ref={pickerRef}
          className="wf-button wf-button--repo"
          aria-haspopup="listbox"
          aria-expanded={menuOpen}
          title={repos && repos.length === 0 ? "No repository is configured" : "Definitions repository"}
          onClick={() => setMenuOpen((o) => !o)}
        >
          {repo ?? (repos === null ? "Repository" : "No repository")} <span aria-hidden="true">▾</span>
        </button>
        {menuOpen ? (
          <div className="wf-repo__menu">
          <ul
            ref={listRef}
            className="wf-repo__list"
            role="listbox"
            aria-label="Repository"
            onKeyDown={(e) => {
              const items = Array.from(listRef.current?.querySelectorAll<HTMLElement>('[role="option"]') ?? []);
              const i = items.indexOf(document.activeElement as HTMLElement);
              if (e.key === "ArrowDown") items[Math.min(items.length - 1, i + 1)]?.focus();
              else if (e.key === "ArrowUp") items[Math.max(0, i - 1)]?.focus();
              else if (e.key === "Escape") {
                setMenuOpen(false);
                pickerRef.current?.focus();
              } else return;
              e.preventDefault();
            }}
          >
            {(repos ?? []).length === 0 ? (
              <li className="wf-repo__empty">No repositories</li>
            ) : (
              (repos ?? []).map((r) => (
                <li
                  key={r.name}
                  role="option"
                  aria-selected={r.name === repo}
                  tabIndex={-1}
                  className="wf-repo__option"
                  onClick={() => choose(r.name)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      choose(r.name);
                    }
                  }}
                >
                  {r.name}
                </li>
              ))
            )}
          </ul>
          {repo ? (
            <div className="wf-repo__actions" role="group" aria-label={`Repository ${repo}`}>
              <button type="button" className="wf-repo__action" onClick={() => void fromRepo()}>
                Import from repo
              </button>
              <button type="button" className="wf-repo__action" onClick={() => void toRepo()}>
                Export to repo
              </button>
            </div>
          ) : null}
          </div>
        ) : null}
      </span>
      {pending ? (
        <Panel
          label={pending.kind === "export" ? "Export plan" : "Import plan"}
          className="wf-panel--import"
          onClose={() => setPending(null)}
          returnFocus={pending.kind === "export" || "repo" in pending ? pickerRef.current : importRef.current}
        >
          <div className="wf-form">
            <h2 className="wf-panel__title">{pending.kind === "export" ? "Export plan" : "Import plan"}</h2>
            {"repo" in pending ? (
              <p className="wf-plan__repo">
                {pending.kind === "export" ? "Commit to" : "Read from"} <strong>{pending.repo}</strong>
              </p>
            ) : null}
            {pending.plan.changes.length === 0 ? <p>Nothing would change.</p> : null}
            <ul className="wf-plan">
              {pending.plan.changes.map((c) => (
                <li key={c.path} className="wf-plan__change">
                  <span className={`wf-plan__action wf-plan__action--${c.action}`}>{c.action}</span>
                  <span className="wf-plan__path">{c.path}</span>
                </li>
              ))}
            </ul>
            {(pending.plan.errors ?? []).length > 0 ? (
              <ul className="wf-plan wf-plan--errors" aria-label="Import errors">
                {(pending.plan.errors ?? []).map((e) => (
                  <li key={`${e.path}-${e.code}`}>
                    {e.path}: {e.message}
                  </li>
                ))}
              </ul>
            ) : null}
            <div className="wf-form__actions">
              <button type="button" className="wf-button" onClick={() => setPending(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="wf-button wf-button--primary"
                disabled={(pending.plan.errors ?? []).length > 0}
                onClick={() => void apply()}
              >
                {pending.kind === "export" ? "Apply export" : "Apply import"}
              </button>
            </div>
          </div>
        </Panel>
      ) : null}
    </span>
  );
}

export default IoControls;
