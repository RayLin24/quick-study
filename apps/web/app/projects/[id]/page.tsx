"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, apiGet } from "../../../lib/api";

interface Project {
  id: string;
  name: string;
  slug: string;
  output_language: string;
  reader_level: string;
  length_preset: string;
}

interface Source {
  id: string;
  kind: string;
  locator: string;
  display_name: string;
}

interface Run {
  id: string;
  status: string;
  phase: string;
  created_at: string;
}

export default function ProjectDetail() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.id as string;

  const [project, setProject] = useState<Project | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      apiGet<Project>(`/api/projects/${projectId}`),
      apiGet<Source[]>(`/api/projects/${projectId}/sources`),
      apiGet<Run[]>(`/api/projects/${projectId}/runs`),
    ])
      .then(([nextProject, nextSources, nextRuns]) => {
        setProject(nextProject);
        setSources(nextSources);
        setRuns(nextRuns);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) {
          router.push("/login");
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to load project");
      })
      .finally(() => setLoading(false));
  }, [projectId, router]);

  if (loading) {
    return (
      <div className="container">
        <div className="loading">Loading project...</div>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="container">
        <div className="error">{error || "Project not found"}</div>
      </div>
    );
  }

  return (
    <div className="container">
      <div className="header">
        <h1>{project.name}</h1>
        <p>
          {project.slug} · {project.output_language} · {project.reader_level} ·{" "}
          {project.length_preset}
        </p>
      </div>

      <div className="nav">
        <Link href={`/projects/${projectId}/sources/new`}>Add Source</Link>
        <Link href={`/projects/${projectId}/runs/new`}>New Run</Link>
        <Link href="/projects">All Projects</Link>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="card">
        <h2>Sources</h2>
        {sources.length === 0 ? (
          <p>No sources yet. Add a documentation site or repository to get started.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Kind</th>
                <th>Locator</th>
                <th>Name</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((source) => (
                <tr key={source.id}>
                  <td>{source.kind}</td>
                  <td>{source.locator}</td>
                  <td>{source.display_name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h2>Runs</h2>
        {runs.length === 0 ? (
          <p>No runs yet. Start a new run to generate a tutorial.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Status</th>
                <th>Phase</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td>
                    <span className={`status-badge status-${run.status}`}>{run.status}</span>
                  </td>
                  <td>{run.phase}</td>
                  <td>{new Date(run.created_at).toLocaleString()}</td>
                  <td>
                    <Link href={`/projects/${projectId}/runs/${run.id}`}>View</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
