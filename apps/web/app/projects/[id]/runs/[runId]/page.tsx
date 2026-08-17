"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, apiGet, apiPost } from "../../../../../lib/api";

interface OutlineChapter {
  slug: string;
  title: string;
  ordinal: number;
  summary: string;
}

interface Outline {
  id: string;
  version: number;
  title: string;
  summary: string;
  status: string;
  chapters: OutlineChapter[];
}

interface Run {
  id: string;
  title: string;
  status: string;
  phase: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  outline: Outline | null;
}

export default function RunDetail() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.id as string;
  const runId = params.runId as string;

  const [run, setRun] = useState<Run | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deciding, setDeciding] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const next = await apiGet<Run>(`/api/projects/${projectId}/runs/${runId}`);
        if (!cancelled) {
          setRun(next);
          setError("");
        }
      } catch (err) {
        if (cancelled) {
          return;
        }
        if (err instanceof ApiError && err.status === 401) {
          router.push("/login");
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to load run");
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void load();
    const timer = window.setInterval(() => {
      void load();
    }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [projectId, runId, router]);

  const decide = async (decision: "approved" | "rejected") => {
    if (!run?.outline) {
      return;
    }
    setDeciding(true);
    setError("");
    try {
      await apiPost(`/api/projects/${projectId}/approvals/${run.outline.id}`, {
        decision,
        note: "",
      });
      const next = await apiGet<Run>(`/api/projects/${projectId}/runs/${runId}`);
      setRun(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit decision");
    } finally {
      setDeciding(false);
    }
  };

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
        <div className="error">{error || "Run not found"}</div>
      </div>
    );
  }

  return (
    <div className="container">
      <div className="header">
        <h1>{run.title || "Untitled run"}</h1>
        <p>
          <span className={`status-badge status-${run.status}`}>{run.status}</span> {run.phase}
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

      {run.status === "suspended" && run.outline && (
        <div className="card">
          <h2>Outline v{run.outline.version}</h2>
          <p>{run.outline.title}</p>
          <ol>
            {run.outline.chapters.map((chapter) => (
              <li key={chapter.slug}>{chapter.title}</li>
            ))}
          </ol>
          <div className="form-actions">
            <button type="button" disabled={deciding} onClick={() => void decide("approved")}>
              {deciding ? "Submitting..." : "Approve"}
            </button>
            <button type="button" disabled={deciding} onClick={() => void decide("rejected")}>
              Reject
            </button>
          </div>
        </div>
      )}

      {run.status === "succeeded" && (
        <div className="card">
          <h2>Export</h2>
          <p>Download the generated tutorial as a Markdown bundle.</p>
          <a href={`/api/projects/${projectId}/exports/${runId}/markdown`} download>
            <button type="button">Download Markdown Bundle</button>
          </a>
        </div>
      )}

      <div className="card">
        <Link href={`/projects/${projectId}`}>Back to Project</Link>
      </div>
    </div>
  );
}
