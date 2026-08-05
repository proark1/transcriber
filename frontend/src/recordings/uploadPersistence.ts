import type { Language } from "../api/contracts.ts";

const STORAGE_KEY_PREFIX = "transcriber.pending-upload.v2";

export interface PendingUpload {
  clientRequestId: string;
  uploadSessionId: string | null;
  recordingId: string | null;
  fileSignature: string;
  filename: string;
  sizeBytes: number;
  language: Language;
}

export function signatureForFile(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function storageKey(username: string): string {
  return `${STORAGE_KEY_PREFIX}.${username}`;
}

export function loadPendingUpload(username: string): PendingUpload | null {
  const key = storageKey(username);
  const value = localStorage.getItem(key);
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as Partial<PendingUpload>;
    if (
      typeof parsed.clientRequestId !== "string" ||
      typeof parsed.fileSignature !== "string" ||
      typeof parsed.filename !== "string" ||
      typeof parsed.sizeBytes !== "number" ||
      !["en", "de", "tr"].includes(parsed.language ?? "")
    ) {
      throw new Error("Invalid pending upload");
    }
    return {
      clientRequestId: parsed.clientRequestId,
      uploadSessionId: parsed.uploadSessionId ?? null,
      recordingId: parsed.recordingId ?? null,
      fileSignature: parsed.fileSignature,
      filename: parsed.filename,
      sizeBytes: parsed.sizeBytes,
      language: parsed.language as Language,
    };
  } catch {
    localStorage.removeItem(key);
    return null;
  }
}

export function savePendingUpload(username: string, upload: PendingUpload): void {
  localStorage.setItem(storageKey(username), JSON.stringify(upload));
}

export function clearPendingUpload(username: string): void {
  localStorage.removeItem(storageKey(username));
}
