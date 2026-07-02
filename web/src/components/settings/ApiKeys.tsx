"use client";

import { useState, useTransition } from "react";
import { isoDateLabel } from "@/lib/format";
import { listKeys, mintKey, revokeKey, type ApiKeyRow } from "@/app/settings/actions";

const MCP_SNIPPET = `{
  "mcpServers": {
    "fielddesk": {
      "type": "http",
      "url": "https://api.peon.works/mcp",
      "headers": { "Authorization": "Bearer <your key>" }
    }
  }
}`;

const box: React.CSSProperties = {
  border: "1.5px solid var(--ink)",
  background: "var(--paper)",
};

export function ApiKeys({ initialKeys }: { initialKeys: ApiKeyRow[] }) {
  const [keys, setKeys] = useState<ApiKeyRow[]>(initialKeys);
  const [label, setLabel] = useState("");
  const [minted, setMinted] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmHash, setConfirmHash] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function onCreate(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = label.trim();
    if (!trimmed) return;
    setError(null);
    setMinted(null);
    setCopied(false);
    startTransition(async () => {
      try {
        const key = await mintKey(trimmed);
        setMinted(key);
        setLabel("");
        setKeys(await listKeys());
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not create the key.");
      }
    });
  }

  function onRevoke(hash: string) {
    setError(null);
    startTransition(async () => {
      try {
        await revokeKey(hash);
        setConfirmHash(null);
        setKeys(await listKeys());
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not revoke the key.");
      }
    });
  }

  async function copyMinted() {
    if (!minted) return;
    try {
      await navigator.clipboard.writeText(minted);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  const active = keys.filter((k) => !k.revoked_at);

  return (
    <div>
      <h1 className="display" style={{ fontSize: "2.2rem", margin: 0 }}>Settings</h1>
      <p className="font-mono" style={{ fontSize: "0.8rem", color: "var(--muted)", marginTop: 8 }}>
        Manage the personal API keys that connect Field Desk to the MCP server.
      </p>

      <section style={{ marginTop: 32 }}>
        <div className="eyebrow" style={{ color: "var(--ink)" }}>Create a key</div>
        <p className="t-sm" style={{ color: "var(--ink-soft)", marginTop: 6, marginBottom: 14 }}>
          Give the key a label so you can tell it apart later. The full key is shown once, right
          after you create it.
        </p>
        <form onSubmit={onCreate} style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Laptop, laptop CLI, Claude Desktop…"
            className="font-mono"
            style={{
              flex: "1 1 260px",
              background: "transparent",
              border: "1.5px solid var(--ink)",
              padding: "10px 12px",
              fontSize: "0.9rem",
              color: "var(--ink)",
            }}
          />
          <button
            type="submit"
            disabled={pending || !label.trim()}
            className="w-strong"
            style={{
              padding: "10px 18px",
              background: "var(--ink)",
              color: "var(--paper)",
              border: "none",
              cursor: pending || !label.trim() ? "not-allowed" : "pointer",
              fontSize: "0.9rem",
            }}
          >
            {pending ? "Working…" : "Create key"}
          </button>
        </form>

        {error && (
          <p className="font-mono" style={{ color: "var(--ink)", fontSize: "0.8rem", marginTop: 12, fontWeight: 600 }}>
            {error}
          </p>
        )}

        {minted && (
          <div style={{ ...box, marginTop: 16, padding: 16, boxShadow: "var(--shadow-hard)" }}>
            <div className="eyebrow" style={{ color: "var(--ink)" }}>Your new key</div>
            <div
              className="font-mono"
              style={{
                marginTop: 8,
                padding: "10px 12px",
                border: "1.5px solid var(--line)",
                background: "var(--paper-2)",
                fontSize: "0.85rem",
                wordBreak: "break-all",
                color: "var(--ink)",
              }}
            >
              {minted}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 10 }}>
              <button
                onClick={copyMinted}
                className="w-strong"
                style={{
                  padding: "7px 14px",
                  background: "transparent",
                  border: "1.5px solid var(--ink)",
                  color: "var(--ink)",
                  cursor: "pointer",
                  fontSize: "0.82rem",
                }}
              >
                {copied ? "Copied" : "Copy key"}
              </button>
              <span className="t-sm" style={{ color: "var(--muted)" }}>
                Copy it now &mdash; it won&apos;t be shown again.
              </span>
            </div>
          </div>
        )}
      </section>

      <section style={{ marginTop: 40 }}>
        <div className="eyebrow" style={{ color: "var(--ink)" }}>Your keys</div>
        {keys.length === 0 ? (
          <p className="t-sm" style={{ color: "var(--muted)", marginTop: 12 }}>
            You don&apos;t have any keys yet.
          </p>
        ) : (
          <div style={{ ...box, marginTop: 12, overflowX: "auto" }}>
            <table className="nums" style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
              <thead>
                <tr style={{ borderBottom: "1.5px solid var(--ink)" }}>
                  <Th>Label</Th>
                  <Th>Created</Th>
                  <Th>Last used</Th>
                  <Th>Status</Th>
                  <Th align="right">Action</Th>
                </tr>
              </thead>
              <tbody>
                {keys.map((k) => {
                  const revoked = !!k.revoked_at;
                  return (
                    <tr key={k.key_hash} style={{ borderBottom: "1px solid var(--line)" }}>
                      <Td>
                        <span className="w-strong" style={{ color: revoked ? "var(--muted)" : "var(--ink)" }}>
                          {k.label}
                        </span>
                      </Td>
                      <Td muted>{isoDateLabel(k.created_at)}</Td>
                      <Td muted>{k.last_used_at ? isoDateLabel(k.last_used_at) : "Never"}</Td>
                      <Td>{revoked ? <span style={{ color: "var(--muted)" }}>Revoked</span> : "Active"}</Td>
                      <Td align="right">
                        {revoked ? (
                          <span style={{ color: "var(--muted)" }}>&mdash;</span>
                        ) : confirmHash === k.key_hash ? (
                          <span style={{ display: "inline-flex", gap: 10, alignItems: "center" }}>
                            <button
                              onClick={() => onRevoke(k.key_hash)}
                              disabled={pending}
                              className="w-strong"
                              style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: "var(--ink)", fontSize: "0.82rem" }}
                            >
                              Confirm revoke
                            </button>
                            <button
                              onClick={() => setConfirmHash(null)}
                              className="t-sm"
                              style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: "var(--muted)" }}
                            >
                              Cancel
                            </button>
                          </span>
                        ) : (
                          <button
                            onClick={() => setConfirmHash(k.key_hash)}
                            className="t-sm"
                            style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: "var(--ink-soft)" }}
                          >
                            Revoke
                          </button>
                        )}
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section style={{ marginTop: 40 }}>
        <div className="eyebrow" style={{ color: "var(--ink)" }}>Connect an MCP client</div>
        <p className="t-sm" style={{ color: "var(--ink-soft)", marginTop: 6, marginBottom: 12 }}>
          Add this to your MCP client config, then replace{" "}
          <span className="font-mono">&lt;your key&gt;</span> with{" "}
          {active.length ? "an active key above." : "a key you create above."}
        </p>
        <pre
          className="font-mono"
          style={{
            ...box,
            margin: 0,
            padding: 16,
            fontSize: "0.8rem",
            lineHeight: 1.5,
            overflowX: "auto",
            color: "var(--ink)",
            whiteSpace: "pre",
          }}
        >
          {MCP_SNIPPET}
        </pre>
      </section>
    </div>
  );
}

function Th({ children, align }: { children: React.ReactNode; align?: "right" }) {
  return (
    <th
      className="eyebrow"
      style={{ textAlign: align ?? "left", padding: "10px 14px", color: "var(--ink)", whiteSpace: "nowrap" }}
    >
      {children}
    </th>
  );
}

function Td({ children, align, muted }: { children: React.ReactNode; align?: "right"; muted?: boolean }) {
  return (
    <td
      style={{
        textAlign: align ?? "left",
        padding: "10px 14px",
        color: muted ? "var(--muted)" : "var(--ink)",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </td>
  );
}
