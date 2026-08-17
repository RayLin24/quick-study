"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, apiGet } from "../lib/api";

export default function Home() {
  const router = useRouter();
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    apiGet("/api/auth/me")
      .then(() => router.push("/projects"))
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 401) {
          router.push("/login");
          return;
        }
        router.push("/login");
      })
      .finally(() => setChecking(false));
  }, [router]);

  if (checking) {
    return (
      <div className="container">
        <div className="loading">Checking authentication...</div>
      </div>
    );
  }

  return null;
}
