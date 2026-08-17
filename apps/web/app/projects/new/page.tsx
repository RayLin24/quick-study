"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiError, apiPost } from "../../../lib/api";

export default function NewProject() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [outputLanguage, setOutputLanguage] = useState("zh");
  const [readerLevel, setReaderLevel] = useState("intermediate");
  const [lengthPreset, setLengthPreset] = useState("standard");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setError("");

    try {
      const project = await apiPost<{ id: string }>("/api/projects", {
        name,
        slug,
        output_language: outputLanguage,
        reader_level: readerLevel,
        length_preset: lengthPreset,
      });
      router.push(`/projects/${project.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to create project");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="container">
      <div className="header">
        <h1>New Project</h1>
        <p>Create a new tutorial generation project</p>
      </div>

      <div className="card" style={{ maxWidth: 600 }}>
        <form onSubmit={handleSubmit}>
          {error && <div className="error">{error}</div>}

          <div className="form-group">
            <label htmlFor="name">Name</label>
            <input
              id="name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="slug">Slug</label>
            <input
              id="slug"
              value={slug}
              onChange={(event) => setSlug(event.target.value)}
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="outputLanguage">Output Language</label>
            <select
              id="outputLanguage"
              value={outputLanguage}
              onChange={(event) => setOutputLanguage(event.target.value)}
            >
              <option value="zh">Chinese</option>
              <option value="en">English</option>
            </select>
          </div>

          <div className="form-group">
            <label htmlFor="readerLevel">Reader Level</label>
            <select
              id="readerLevel"
              value={readerLevel}
              onChange={(event) => setReaderLevel(event.target.value)}
            >
              <option value="beginner">Beginner</option>
              <option value="intermediate">Intermediate</option>
              <option value="advanced">Advanced</option>
            </select>
          </div>

          <div className="form-group">
            <label htmlFor="lengthPreset">Length Preset</label>
            <select
              id="lengthPreset"
              value={lengthPreset}
              onChange={(event) => setLengthPreset(event.target.value)}
            >
              <option value="brief">Brief</option>
              <option value="standard">Standard</option>
              <option value="deep">Deep</option>
            </select>
          </div>

          <div className="form-actions">
            <button type="submit" disabled={loading}>
              {loading ? "Creating..." : "Create Project"}
            </button>
            <button type="button" onClick={() => router.push("/projects")}>
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
