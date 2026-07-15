"use client";

import { motion } from "framer-motion";
import { ArrowRight, Mic } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { Button } from "@/components/ui/Button";
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
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-page px-6">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 [background:radial-gradient(60%_50%_at_50%_0%,color-mix(in_srgb,var(--accent-blue)_12%,transparent),transparent)]"
      />

      <div className="absolute right-6 top-6">
        <ThemeToggle />
      </div>

      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
        className="relative w-full max-w-sm rounded-3xl border border-border bg-surface-raised p-8 shadow-xl shadow-black/5"
      >
        <div className="mb-6 flex flex-col items-center text-center">
          <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-accent-blue text-white">
            <Mic size={22} aria-hidden="true" />
          </span>
          <h1 className="text-xl font-semibold text-ink">IELTS Speaking Platform</h1>
          <p className="mt-1 text-sm text-ink-muted">Start your automated speaking assessment.</p>
        </div>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void handleStart();
          }}
          className="flex flex-col gap-3"
        >
          <label className="flex flex-col gap-1.5 text-left text-xs font-medium text-ink-secondary">
            Email
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              type="email"
              required
              placeholder="you@example.com"
              className="rounded-xl border border-border bg-page px-3 py-2.5 text-sm text-ink outline-none transition-colors focus:border-accent-blue"
            />
          </label>
          <label className="flex flex-col gap-1.5 text-left text-xs font-medium text-ink-secondary">
            Full name
            <input
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              required
              placeholder="Jane Candidate"
              className="rounded-xl border border-border bg-page px-3 py-2.5 text-sm text-ink outline-none transition-colors focus:border-accent-blue"
            />
          </label>

          <Button type="submit" size="lg" disabled={loading} className="mt-2 w-full gap-2">
            {loading ? "Starting…" : "Begin exam"}
            {!loading && <ArrowRight size={16} aria-hidden="true" />}
          </Button>

          {error && (
            <p role="alert" className="text-center text-xs font-medium text-accent-red">
              {error}
            </p>
          )}
        </form>
      </motion.div>
    </main>
  );
}
