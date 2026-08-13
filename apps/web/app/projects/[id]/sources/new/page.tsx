"use client";

import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

export default function NewSource() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.id as string;

  const [kind, setKind] = useState("web");
  const [locator, setLocator] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setError("");

    try {
      const response = await fetch(`/api/projects/${projectId}/sources`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          kind,
          locator,
          display_name: displayName,
        }),
      });

      if (!response.ok) {
        throw new Error("Failed to add source");
      }

      router.push(`/projects/${projectId}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add source");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="container">
      <div className="header">
        <h1>Add Source</h1>
        <p>Register a documentation site or repository</p>
      </div>

      <div className="card" style={{ maxWidth: 600 }}>
        <form onSubmit={handleSubmit}>
          {error && <div className="error">{error}</div>}

          <div className="form-group">
            <label htmlFor="kind">Kind</label>
            <select
              id="kind"
              value={kind}
              onChange={(event) => setKind(event.target.value)}
            >
              <option value="web">Documentation Site</option>
              <option value="github">GitHub Repository</option>
            </select>
          </div>

          <div className="form-group">
            <label htmlFor="locator">URL or Repository</label>
            <input
              id="locator"
              value={locator}
              onChange={(event) => setLocator(event.target.value)}
              placeholder="https://docs.example.com or owner/repo"
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="displayName">Display Name</label>
            <input
              id="displayName"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
            />
          </div>

          <div className="form-actions">
            <button type="submit" disabled={loading}>
              {loading ? "Adding..." : "Add Source"}
            </button>
            <button type="button" onClick={() => router.push(`/projects/${projectId}`)}>
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
