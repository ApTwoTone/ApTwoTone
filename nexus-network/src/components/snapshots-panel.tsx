"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi, postApi } from "@/lib/api";

interface Snapshot {
  id: string;
  label: string;
  created_at: string;
  db_size_mb: number;
  git_hash: string;
  git_message: string;
  ok: boolean;
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) +
      " at " + d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  } catch {
    return iso;
  }
}

export function SnapshotsPanel() {
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [loading, setLoading] = useState(true);
  const [label, setLabel] = useState("");
  const [creating, setCreating] = useState(false);
  const [restoring, setRestoring] = useState<string | null>(null);
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);

  const load = useCallback(() => {
    fetchApi<{ snapshots: Snapshot[] }>("/api/snapshots")
      .then((data) => {
        setSnapshots(data.snapshots || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleCreate = async () => {
    if (!label.trim()) return;
    setCreating(true);
    setMessage(null);
    try {
      const result = await postApi<Snapshot>("/api/snapshots", { label: label.trim() });
      if (result.ok) {
        setMessage({ text: `Snapshot "${result.label}" saved (${result.db_size_mb} MB, git:${result.git_hash})`, ok: true });
        setLabel("");
        load();
      } else {
        setMessage({ text: (result as { error?: string }).error || "Snapshot failed", ok: false });
      }
    } catch (e) {
      setMessage({ text: String(e), ok: false });
    } finally {
      setCreating(false);
    }
  };

  const handleRestore = async (snapshotId: string) => {
    if (restoring !== snapshotId) {
      // First click — show confirm
      setRestoring(snapshotId);
      return;
    }
    // Second click — confirmed
    setMessage(null);
    try {
      const result = await postApi<{ ok: boolean; note?: string; error?: string }>(
        `/api/snapshots/${snapshotId}/restore`,
        {}
      );
      if (result.ok) {
        setMessage({ text: result.note || "Restored successfully.", ok: true });
        load();
      } else {
        setMessage({ text: result.error || "Restore failed", ok: false });
      }
    } catch (e) {
      setMessage({ text: String(e), ok: false });
    } finally {
      setRestoring(null);
    }
  };

  return (
    <div className="flex-1 p-6 overflow-auto">
      <h2 className="text-xl font-bold mb-1">Version History</h2>
      <p className="text-sm text-muted mb-6">
        Snapshot before every big change. One-click restore of DB + config.
      </p>

      {/* Create snapshot */}
      <div className="bg-card border border-border rounded-xl p-5 mb-6">
        <h3 className="text-sm font-semibold mb-3">Take a Snapshot Now</h3>
        <div className="flex gap-2">
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            placeholder="e.g. before_unified_events"
            className="flex-1 bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent"
          />
          <button
            onClick={handleCreate}
            disabled={creating || !label.trim()}
            className="px-4 py-2 bg-accent text-background rounded-lg text-sm font-medium disabled:opacity-40 hover:opacity-90 transition-opacity"
          >
            {creating ? "Saving…" : "📸 Snapshot"}
          </button>
        </div>
        <p className="text-xs text-muted mt-2">
          Captures: database ({snapshots[0]?.db_size_mb ?? "–"} MB), config.json, and current git commit.
        </p>

        {message && (
          <div
            className={`mt-3 text-xs px-3 py-2 rounded-lg ${
              message.ok ? "bg-success/10 text-success" : "bg-danger/10 text-danger"
            }`}
          >
            {message.text}
          </div>
        )}
      </div>

      {/* Snapshot list */}
      <div className="bg-card border border-border rounded-xl p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold">Snapshots</h3>
          <span className="text-xs text-muted">{snapshots.length} / 20 saved</span>
        </div>

        {loading ? (
          <p className="text-sm text-muted text-center py-8">Loading…</p>
        ) : snapshots.length === 0 ? (
          <p className="text-sm text-muted text-center py-8">
            No snapshots yet. Take one before your next big change.
          </p>
        ) : (
          <div className="space-y-3">
            {snapshots.map((snap) => (
              <div
                key={snap.id}
                className="border border-border rounded-lg p-4"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium truncate">{snap.label}</span>
                      <span className="text-xs text-muted font-mono bg-background px-1.5 py-0.5 rounded">
                        git:{snap.git_hash}
                      </span>
                      <span className="text-xs text-muted">{snap.db_size_mb} MB</span>
                    </div>
                    <p className="text-xs text-muted mt-0.5">{formatDate(snap.created_at)}</p>
                    {snap.git_message && (
                      <p className="text-xs text-muted mt-1 truncate opacity-60">"{snap.git_message}"</p>
                    )}
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    {restoring === snap.id ? (
                      <>
                        <span className="text-xs text-danger">Restore DB + config?</span>
                        <button
                          onClick={() => handleRestore(snap.id)}
                          className="px-3 py-1.5 bg-danger text-white rounded-lg text-xs font-medium hover:opacity-90"
                        >
                          Confirm
                        </button>
                        <button
                          onClick={() => setRestoring(null)}
                          className="px-3 py-1.5 border border-border rounded-lg text-xs text-muted hover:text-foreground"
                        >
                          Cancel
                        </button>
                      </>
                    ) : (
                      <button
                        onClick={() => handleRestore(snap.id)}
                        className="px-3 py-1.5 border border-border rounded-lg text-xs text-muted hover:text-foreground hover:border-danger transition-colors"
                      >
                        Restore
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Info footer */}
      <p className="text-xs text-muted mt-4 text-center">
        Restore rolls back database and config only. Code changes are not reverted (intentional — safe for active development).
        A pre-restore safety backup is always created automatically before any restore.
      </p>
    </div>
  );
}
