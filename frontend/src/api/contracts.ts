export type Language = "en" | "de" | "tr";

export type RecordingStatus =
  | "uploading"
  | "queued"
  | "validating"
  | "normalizing"
  | "chunking"
  | "transcribing"
  | "assembling"
  | "completed"
  | "failed"
  | "deleting";

export interface ApiErrorEnvelope {
  error: {
    code: string;
    message: string;
    requestId: string;
  };
}

export interface SessionResponse {
  authenticated: true;
  username: string;
  csrfToken: string;
  expiresAt: string;
}

export interface RecordingResponse {
  id: string;
  filename: string;
  contentType: string;
  language: Language;
  status: RecordingStatus;
  createdAt: string;
  completedAt: string | null;
  durationSeconds: number | null;
  verifiedBytes: number | null;
  completedChunks: number;
  totalChunks: number;
  safeErrorCode: string | null;
  hasPlayback: boolean;
  hasTranscript: boolean;
}

export interface ConfirmedPart {
  partNumber: number;
  sizeBytes: number;
}

export interface UploadStateResponse {
  recordingId: string;
  uploadSessionId: string;
  partSizeBytes: number;
  partCount: number;
  expiresAt: string;
  status: "uploading" | "completed" | "aborted" | "expired";
  confirmedParts: ConfirmedPart[];
}

export interface AuthorizedPartsResponse {
  authorizedParts: Array<{ partNumber: number; url: string }>;
  confirmedParts: ConfirmedPart[];
  expiresAt: string;
}

export interface PlaybackResponse {
  url: string;
  expiresAt: string;
}
