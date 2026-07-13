const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface LoginResult {
  accessToken: string;
  candidateId: string;
}

export async function login(email: string, fullName: string): Promise<LoginResult> {
  const response = await fetch(`${API_BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, full_name: fullName }),
  });
  if (!response.ok) throw new Error(`login failed: ${response.status}`);
  const body = await response.json();
  return { accessToken: body.access_token, candidateId: body.candidate_id };
}

export interface SessionResult {
  id: string;
  status: string;
  currentPhase: string | null;
  resumeToken: string;
}

export async function createSession(accessToken: string): Promise<SessionResult> {
  const response = await fetch(`${API_BASE_URL}/sessions`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new Error(`session creation failed: ${response.status}`);
  const body = await response.json();
  return {
    id: body.id,
    status: body.status,
    currentPhase: body.current_phase,
    resumeToken: body.resume_token,
  };
}

export interface VideoUploadURL {
  uploadUrl: string;
  storageKey: string;
}

export async function getVideoUploadUrl(
  accessToken: string,
  sessionId: string
): Promise<VideoUploadURL> {
  const response = await fetch(`${API_BASE_URL}/sessions/${sessionId}/video-upload-url`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new Error(`presigned URL request failed: ${response.status}`);
  const body = await response.json();
  return { uploadUrl: body.upload_url, storageKey: body.storage_key };
}

export async function uploadVideoBlob(uploadUrl: string, blob: Blob): Promise<void> {
  const response = await fetch(uploadUrl, {
    method: "PUT",
    headers: { "Content-Type": "video/webm" },
    body: blob,
  });
  if (!response.ok) throw new Error(`video upload failed: ${response.status}`);
}

export async function confirmVideoUpload(accessToken: string, sessionId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/sessions/${sessionId}/video-upload-complete`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new Error(`upload confirmation failed: ${response.status}`);
}

export function wsBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_WS_BASE_URL ?? "ws://localhost:8000";
}
