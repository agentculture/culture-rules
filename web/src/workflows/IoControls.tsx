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
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
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

/**
 * A chosen file's text. `Blob#text` is in every browser the build targets
 * (Vite's default: Chrome 87, Edge 88, Firefox 78, Safari 14), so no
 * FileReader fallback; jsdom lacks it, and vitest.setup.ts polyfills it.
 */
export function readText(file: File): Promise<string> {
  return file.text();
}

const message = (err: unknown) => (err instanceof ApiError ? err.message : String(err));
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

type PlanErrors = NonNullable<ImportPlan["errors"]>;

/** A plan waiting for "Apply": from chosen files, or from / to a repository. */
type Pending =
  | { kind: "import"; files: Record<string, string>; plan: ImportPlan }
  | { kind: "import"; repo: string; plan: ImportPlan }
  | { kind: "export"; repo: string; plan: { changes: ImportChange[]; errors?: PlanErrors } };

/** " to <repo>" / " from <repo>" for a repository plan, "" for chosen files. */
function whereText(pending: Pending): string {
  if (!("repo" in pending)) return "";
  const direction = pending.kind === "export" ? "to" : "from";
  return ` ${direction} ${pending.repo}`;
}

/** Post a pending plan for real (apply=true); resolves to the status line to show. */
async function applyPending(pending: Pending): Promise<{ status: string; imported: boolean }> {
  const where = whereText(pending);
  if (pending.kind === "export") {
    const result = await exportToRepo(pending.repo, true);
    const n = result.changes.filter((c) => c.action !== "unchanged").length;
    const commit = result.commit ? ` (${result.commit.slice(0, 7)})` : "";
    return { status: `Exported ${plural(n, "change")}${where}${commit}`, imported: false };
  }
  const plan =
    "repo" in pending ? await importFromRepo(pending.repo, true) : await importDefinitions(pending.files, true);
  return { status: `Imported ${plural(plan.changes.length, "change")}${where}`, imported: true };
}

/** The bundle download's file name, suffixed with the chosen repository. */
function exportFileName(repo: string | null): string {
  const suffix = repo ? `-${repo.replace(/[^A-Za-z0-9]+/g, "-")}` : "";
  return `culture-rules-export${suffix}.json`;
}

/** Offer `data` as a JSON file download. */
function downloadJson(name: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** The picker's open menu: the repository listbox, then that repository's import / export. */
function RepoMenu({
  repos,
  repo,
  onChoose,
  onDismiss,
  onImport,
  onExport,
}: Readonly<{
  repos: Repo[];
  repo: string | null;
  onChoose: (name: string) => void;
  onDismiss: () => void;
  onImport: () => void;
  onExport: () => void;
}>) {
  const listRef = useRef<HTMLUListElement>(null);

  // Opening the menu puts focus on the chosen repository (else the first).
  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>('[aria-selected="true"], [role="option"]')?.focus();
  }, []);

  const onListKey = (e: KeyboardEvent<HTMLUListElement>) => {
    const items = Array.from(listRef.current?.querySelectorAll<HTMLElement>('[role="option"]') ?? []);
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowDown") items[Math.min(items.length - 1, i + 1)]?.focus();
    else if (e.key === "ArrowUp") items[Math.max(0, i - 1)]?.focus();
    else if (e.key === "Escape") onDismiss();
    else return;
    e.preventDefault();
  };

  const onOptionKey = (e: KeyboardEvent<HTMLLIElement>, name: string) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onChoose(name);
    }
  };

  return (
    <div className="wf-repo__menu">
      <ul ref={listRef} className="wf-repo__list" role="listbox" aria-label="Repository" onKeyDown={onListKey}>
        {repos.length === 0 ? (
          <li className="wf-repo__empty">No repositories</li>
        ) : (
          repos.map((r) => (
            <li
              key={r.name}
              role="option"
              aria-selected={r.name === repo}
              tabIndex={-1}
              className="wf-repo__option"
              onClick={() => onChoose(r.name)}
              onKeyDown={(e) => onOptionKey(e, r.name)}
            >
              {r.name}
            </li>
          ))
        )}
      </ul>
      {repo ? (
        <fieldset className="wf-repo__actions plain-group" aria-label={`Repository ${repo}`}>
          <button type="button" className="wf-repo__action" onClick={onImport}>
            Import from repo
          </button>
          <button type="button" className="wf-repo__action" onClick={onExport}>
            Export to repo
          </button>
        </fieldset>
      ) : null}
    </div>
  );
}

/** The dry-run plan, shown until "Apply" (or Cancel). */
function PlanPanel({
  pending,
  returnFocus,
  onCancel,
  onApply,
}: Readonly<{
  pending: Pending;
  returnFocus: HTMLElement | null;
  onCancel: () => void;
  onApply: () => void;
}>) {
  const exporting = pending.kind === "export";
  const title = exporting ? "Export plan" : "Import plan";
  const errors = pending.plan.errors ?? [];
  return (
    <Panel label={title} className="wf-panel--import" onClose={onCancel} returnFocus={returnFocus}>
      <div className="wf-form">
        <h2 className="wf-panel__title">{title}</h2>
        {"repo" in pending ? (
          <p className="wf-plan__repo">
            {exporting ? "Commit to" : "Read from"} <strong>{pending.repo}</strong>
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
        {errors.length > 0 ? (
          <ul className="wf-plan wf-plan--errors" aria-label="Import errors">
            {errors.map((e) => (
              <li key={`${e.path}-${e.code}`}>
                {e.path}: {e.message}
              </li>
            ))}
          </ul>
        ) : null}
        <div className="wf-form__actions">
          <button type="button" className="wf-button" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="wf-button wf-button--primary" disabled={errors.length > 0} onClick={onApply}>
            {exporting ? "Apply export" : "Apply import"}
          </button>
        </div>
      </div>
    </Panel>
  );
}

/** The repository picker's words: the chosen one, else whether any are configured. */
function pickerText(repo: string | null, repos: Repo[] | null): string {
  if (repo) return repo;
  return repos === null ? "Repository" : "No repository";
}

export function IoControls({
  onImported,
  onStatus,
  onError,
}: Readonly<{
  onImported: () => void;
  onStatus: (text: string) => void;
  onError: (text: string) => void;
}>) {
  const fileRef = useRef<HTMLInputElement>(null);
  const importRef = useRef<HTMLButtonElement>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [repos, setRepos] = useState<Repo[] | null>(null);
  const [repo, setRepo] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const pickerRef = useRef<HTMLButtonElement>(null);

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

  const onFiles = async (list: FileList | null) => {
    if (!list || list.length === 0) return;
    const chosen = Array.from(list);
    const texts = await Promise.all(chosen.map(readText));
    const files: Record<string, string> = {};
    chosen.forEach((file, i) => Object.assign(files, importPaths(file.name, texts[i])));
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
    try {
      const done = await applyPending(pending);
      setPending(null);
      onStatus(done.status);
      if (done.imported) onImported();
    } catch (err) {
      setPending(null);
      const verb = pending.kind === "export" ? "Export" : "Import";
      onError(`${verb}${whereText(pending)} failed: ${message(err)}`);
    }
  };

  const doExport = async () => {
    try {
      const result = await exportDefinitions("json");
      downloadJson(exportFileName(repo), result);
      const n = Object.keys(result.files).length;
      onStatus(`Exported ${plural(n, "file")}`);
    } catch (err) {
      onError(`Export failed: ${message(err)}`);
    }
  };

  const choose = (name: string) => {
    setRepo(name);
    setMenuOpen(false);
    pickerRef.current?.focus();
  };

  const dismissMenu = () => {
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
          title={repos?.length === 0 ? "No repository is configured" : "Definitions repository"}
          onClick={() => setMenuOpen((o) => !o)}
        >
          {pickerText(repo, repos)} <span aria-hidden="true">▾</span>
        </button>
        {menuOpen ? (
          <RepoMenu
            repos={repos ?? []}
            repo={repo}
            onChoose={choose}
            onDismiss={dismissMenu}
            onImport={() => void fromRepo()}
            onExport={() => void toRepo()}
          />
        ) : null}
      </span>
      {pending ? (
        <PlanPanel
          pending={pending}
          returnFocus={pending.kind === "export" || "repo" in pending ? pickerRef.current : importRef.current}
          onCancel={() => setPending(null)}
          onApply={() => void apply()}
        />
      ) : null}
    </span>
  );
}

export default IoControls;
