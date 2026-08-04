import type { Language } from "../api/contracts.ts";

const STORAGE_KEY = "transcriber.pending-upload.v1";

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

export function loadPendingUpload(): PendingUpload | null {
  const value = localStorage.getItem(STORAGE_KEY);
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
    localStorage.removeItem(STORAGE_KEY);
    return null;
  }
}

export function savePendingUpload(upload: PendingUpload): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(upload));
}

export function clearPendingUpload(): void {
  localStorage.removeItem(STORAGE_KEY);
}
