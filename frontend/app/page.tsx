"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Status = "loading" | "ok" | "error";

export default function Home() {
  const [status, setStatus] = useState<Status>("loading");

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_URL}/health`, { signal: controller.signal })
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(String(res.status)))))
      .then((data: { status: string }) => setStatus(data.status === "ok" ? "ok" : "error"))
      .catch((err: unknown) => {
        if (!(err instanceof DOMException && err.name === "AbortError")) setStatus("error");
      });
    return () => controller.abort();
  }, []);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 p-8">
      <h1 className="text-4xl font-semibold tracking-tight">FitCoach PRO</h1>
      <p className="text-zinc-600 dark:text-zinc-400">
        API status:{" "}
        <span
          data-testid="api-status"
          className={status === "ok" ? "text-green-600" : status === "error" ? "text-red-600" : ""}
        >
          {status === "loading" ? "checking…" : status === "ok" ? "online" : "unreachable"}
        </span>
      </p>
    </main>
  );
}
