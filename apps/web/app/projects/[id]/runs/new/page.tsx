"use client";

import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

export default function NewRun() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.id as string;

  const [title, setTitle] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setError("");

    try {
      const response = await fetch(`/api/projects/${projectId}/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ title }),
      });

      if (!response.ok) {
        throw new Error("Failed to start run");
      }

      const run = await response.json();
      router.push(`/projects/${projectId}/runs/${run.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start run");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="container">
      <div className="header">
        <h1>New Run</h1>
        <p>Start a tutorial generation run</p>
      </div>

      <div className="card" style={{ maxWidth: 600 }}>
        <form onSubmit={handleSubmit}>
          {error && <div className="error">{error}</div>}

          <div className="form-group">
            <label htmlFor="title">Title</label>
            <input
              id="title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="e.g. LangChain v1 Tutorial"
              required
            />
          </div>

          <div className="form-actions">
            <button type="submit" disabled={loading}>
              {loading ? "Starting..." : "Start Run"}
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
