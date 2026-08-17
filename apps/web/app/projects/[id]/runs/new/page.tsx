"use client";

import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import { ApiError, apiPost } from "../../../../../lib/api";

export default function NewRun() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.id as string;

  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setError("");

    try {
      const run = await apiPost<{ id: string }>(`/api/projects/${projectId}/runs`, {});
      router.push(`/projects/${projectId}/runs/${run.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
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
          <p>The worker will take the project sources through the generation pipeline.</p>
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
