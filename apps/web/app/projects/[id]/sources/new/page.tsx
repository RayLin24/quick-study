"use client";

import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import { ApiError, apiPost } from "../../../../../lib/api";

export default function NewSource() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.id as string;

  const [kind, setKind] = useState("website");
  const [locator, setLocator] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setError("");

    try {
      await apiPost(`/api/projects/${projectId}/sources`, {
        kind,
        locator,
        display_name: displayName,
      });
      router.push(`/projects/${projectId}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
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
              <option value="website">Documentation Site</option>
              <option value="github_repo">GitHub Repository</option>
            </select>
          </div>

          <div className="form-group">
            <label htmlFor="locator">URL or Repository</label>
            <input
              id="locator"
              value={locator}
              onChange={(event) => setLocator(event.target.value)}
              placeholder="https://docs.example.com or https://github.com/owner/repo"
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
