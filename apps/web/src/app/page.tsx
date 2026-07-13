"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { createSession, login } from "@/lib/api";

// Phase 1 dev-only entry point into the media spine test flow. Real
// candidate auth/landing UI is a later-phase concern — this exists so the
// WS gateway loopback can be exercised from an actual browser.
export default function HomePage() {
  const router = useRouter();
  const [email, setEmail] = useState("candidate@example.com");
  const [fullName, setFullName] = useState("Test Candidate");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleStart = async () => {
    setLoading(true);
    setError(null);
    try {
      const { accessToken } = await login(email, fullName);
      const session = await createSession(accessToken);
      sessionStorage.setItem("ielts_access_token", accessToken);
      router.push(`/exam/${session.id}`);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <main style={{ padding: "2rem", fontFamily: "sans-serif" }}>
      <h1>IELTS Speaking Platform</h1>
      <p>Phase 1 — media spine test.</p>

      <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem", maxWidth: 320 }}>
        <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="email" />
        <input
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
          placeholder="full name"
        />
        <button onClick={() => void handleStart()} disabled={loading}>
          {loading ? "Starting…" : "Start Media Spine Test"}
        </button>
        {error && <p style={{ color: "red" }}>{error}</p>}
      </div>
    </main>
  );
}
