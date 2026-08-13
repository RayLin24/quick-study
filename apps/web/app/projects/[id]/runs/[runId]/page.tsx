"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

interface Run {
  id: string;
  title: string;
  status: string;
  phase: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export default function RunDetail() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.id as string;
  const runId = params.runId as string;

  const [run, setRun] = useState<Run | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`/api/projects/${projectId}/runs/${runId}`)
      .then((response) => {
        if (response.status === 401) {
          router.push("/login");
          return null;
        }
        if (!response.ok) {
          throw new Error("Failed to load run");
        }
        return response.json();
      })
      .then((data) => {
        if (data) {
          setRun(data);
        }
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [projectId, runId, router]);

  if (loading) {
    return (
      <div className="container">
        <div className="loading">Loading run...</div>
      </div>
    );
  }

  if (!run) {
    return (
      <div className="container">
        <div className="error">Run not found</div>
      </div>
    );
  }

  return (
    <div className="container">
      <div className="header">
        <h1>{run.title}</h1>
        <p>
          <span className={`status-badge status-${run.status}`}>{run.status}</span>{" "}
          {run.phase}
        </p>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="card">
        <h2>Details</h2>
        <table className="table">
          <tbody>
            <tr>
              <td>Status</td>
              <td>{run.status}</td>
            </tr>
            <tr>
              <td>Phase</td>
              <td>{run.phase}</td>
            </tr>
            <tr>
              <td>Created</td>
              <td>{new Date(run.created_at).toLocaleString()}</td>
            </tr>
            {run.started_at && (
              <tr>
                <td>Started</td>
                <td>{new Date(run.started_at).toLocaleString()}</td>
              </tr>
            )}
            {run.finished_at && (
              <tr>
                <td>Finished</td>
                <td>{new Date(run.finished_at).toLocaleString()}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {run.status === "succeeded" && (
        <div className="card">
          <h2>Export</h2>
          <p>Download the generated tutorial as a Markdown bundle.</p>
          <a
            href={`/api/projects/${projectId}/exports/${runId}/markdown`}
            download
          >
            <button>Download Markdown Bundle</button>
          </a>
        </div>
      )}

      <div className="card">
        <Link href={`/projects/${projectId}`}>Back to Project</Link>
      </div>
    </div>
  );
}
