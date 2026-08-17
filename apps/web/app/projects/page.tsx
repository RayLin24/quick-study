"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, apiGet } from "../../lib/api";

interface Project {
  id: string;
  name: string;
  slug: string;
  output_language: string;
}

export default function Projects() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    apiGet<Project[]>("/api/projects")
      .then(setProjects)
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) {
          router.push("/login");
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to load projects");
      })
      .finally(() => setLoading(false));
  }, [router]);

  if (loading) {
    return (
      <div className="container">
        <div className="loading">Loading projects...</div>
      </div>
    );
  }

  return (
    <div className="container">
      <div className="header">
        <h1>Projects</h1>
        <p>Manage your tutorial generation projects</p>
      </div>

      <div className="nav">
        <Link href="/projects/new" className="active">
          New Project
        </Link>
      </div>

      {error && <div className="error">{error}</div>}

      {projects.length === 0 ? (
        <div className="card">
          <p>No projects yet. Create your first project to get started.</p>
        </div>
      ) : (
        <div className="card">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Slug</th>
                <th>Language</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((project) => (
                <tr key={project.id}>
                  <td>{project.name}</td>
                  <td>{project.slug}</td>
                  <td>{project.output_language}</td>
                  <td>
                    <Link href={`/projects/${project.id}`}>Open</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
