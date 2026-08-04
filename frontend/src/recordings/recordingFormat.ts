import type { Language, RecordingResponse, RecordingStatus } from "../api/contracts.ts";

const LANGUAGE_NAMES: Record<Language, string> = {
  en: "English",
  de: "German",
  tr: "Turkish",
};

const STATUS_NAMES: Record<RecordingStatus, string> = {
  uploading: "Uploading",
  queued: "Waiting",
  validating: "Checking audio",
  normalizing: "Preparing audio",
  chunking: "Preparing parts",
  transcribing: "Transcribing",
  assembling: "Finishing text",
  completed: "Ready",
  failed: "Needs attention",
  deleting: "Deleting",
};

export function languageName(language: Language): string {
  return LANGUAGE_NAMES[language];
}

export function recordingStatusName(status: RecordingStatus): string {
  return STATUS_NAMES[status];
}

export function formatRecordingDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "Duration pending";
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const remainingSeconds = rounded % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`
    : `${minutes}:${String(remainingSeconds).padStart(2, "0")}`;
}

export function isRecordingActive(recording: RecordingResponse): boolean {
  return !new Set<RecordingStatus>(["completed", "failed"]).has(recording.status);
}
