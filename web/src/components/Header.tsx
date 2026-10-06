import { NavLink } from "react-router-dom";
import { useWhoami, type WhoamiState } from "../hooks/useWhoami";

/**
 * The shell header from the design canvas ('Chosen' row): the brand dot and
 * the `rules` wordmark, the five primary tabs in a floating pill, and the
 * signed-in avatar.
 *
 * Exactly five tabs, and the count is the decision (CLAUDE.md, issue #2):
 * runs, history, ledger and inbox are never top-level navigation — they
 * appear contextually inside a rule or workflow. Adding a sixth link means
 * changing the assertion in App.test.tsx and e2e/shell.spec.ts.
 */
export const TABS = [
  { to: "/rules", label: "Rules" },
  { to: "/workflows", label: "Workflows" },
  { to: "/actors", label: "Actors" },
  { to: "/variables", label: "Variables" },
  { to: "/statistics", label: "Statistics" },
] as const;

const KIND: Record<string, string> = { sso: "SSO", service: "service token", agent: "agent" };

export function identityLabel(whoami: WhoamiState): string {
  switch (whoami.status) {
    case "signed-in":
      return `Signed in as ${whoami.displayName} (${KIND[whoami.kind] ?? whoami.kind})`;
    case "unauthenticated":
      return "Not signed in";
    case "unavailable":
      return "Identity unavailable";
    case "loading":
      return "Identity loading";
  }
}

function initial(whoami: WhoamiState): string {
  if (whoami.status !== "signed-in") return "?";
  return whoami.displayName.trim().charAt(0).toUpperCase() || "?";
}

export function Header() {
  const whoami = useWhoami();
  const label = identityLabel(whoami);
  return (
    <header className="app-header">
      <div className="app-header__brand">
        <span className="brand-dot" aria-hidden="true" />
        <span className="app-header__wordmark">rules</span>
      </div>
      <nav className="tabs" aria-label="Primary">
        {TABS.map((tab) => (
          <NavLink key={tab.to} to={tab.to} className="tabs__tab">
            {tab.label}
          </NavLink>
        ))}
      </nav>
      <button
        type="button"
        className="avatar"
        id="identity"
        aria-label={label}
        title={label}
        data-identity-status={whoami.status}
        data-identity-role={whoami.status === "signed-in" ? (whoami.role ?? "") : ""}
      >
        {initial(whoami)}
      </button>
    </header>
  );
}

export default Header;
