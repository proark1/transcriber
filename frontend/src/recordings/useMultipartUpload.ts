import { useCallback, useRef, useState } from "react";

import type { ApiClient } from "../api/client.ts";
import type {
  AuthorizedPartsResponse,
  CompleteUploadResponse,
  Language,
  UploadStateResponse,
} from "../api/contracts.ts";
import {
  clearPendingUpload,
  loadPendingUpload,
  type PendingUpload,
  savePendingUpload,
  signatureForFile,
} from "./uploadPersistence.ts";

const CONCURRENCY = 3;
const MAX_STALLED_ATTEMPTS = 3;

type UploadPhase = "idle" | "waiting-for-file" | "uploading" | "completing" | "error";

export interface MultipartUploadState {
  phase: UploadPhase;
  progress: number;
  pending: PendingUpload | null;
  error: string | null;
}

interface UseMultipartUploadOptions {
  api: Pick<ApiClient, "request">;
  onQueued: (recordingId: string) => void | Promise<void>;
}

export function useMultipartUpload({ api, onQueued }: UseMultipartUploadOptions) {
  const initialPending = loadPendingUpload();
  const [state, setState] = useState<MultipartUploadState>({
    phase: initialPending ? "waiting-for-file" : "idle",
    progress: 0,
    pending: initialPending,
    error: null,
  });
  const abortController = useRef<AbortController | null>(null);

  const upload = useCallback(
    async (file: File, language: Language, existing?: PendingUpload) => {
      const signature = signatureForFile(file);
      if (existing && existing.fileSignature !== signature) {
        setState((current) => ({
          ...current,
          error: "Choose the same file to continue this upload.",
        }));
        return;
      }
      const pending: PendingUpload =
        existing ??
        ({
          clientRequestId: crypto.randomUUID(),
          uploadSessionId: null,
          recordingId: null,
          fileSignature: signature,
          filename: file.name,
          sizeBytes: file.size,
          language,
        } satisfies PendingUpload);
      savePendingUpload(pending);
      abortController.current = new AbortController();
      setState({ phase: "uploading", progress: 0, pending, error: null });
      try {
        let uploadState: UploadStateResponse;
        if (pending.uploadSessionId) {
          uploadState = await api.request<UploadStateResponse>(
            `/api/uploads/${pending.uploadSessionId}`,
            { signal: abortController.current.signal },
          );
        } else {
          uploadState = await api.request<UploadStateResponse>("/api/uploads", {
            method: "POST",
            signal: abortController.current.signal,
            body: JSON.stringify({
              clientRequestId: pending.clientRequestId,
              filename: file.name,
              contentType: file.type || "application/octet-stream",
              sizeBytes: file.size,
              language,
            }),
          });
          pending.uploadSessionId = uploadState.uploadSessionId;
          pending.recordingId = uploadState.recordingId;
          savePendingUpload(pending);
        }

        let confirmed = uploadState.confirmedParts;
        let stalledAttempts = 0;
        while (confirmed.length < uploadState.partCount) {
          const confirmedNumbers = new Set(confirmed.map((part) => part.partNumber));
          const missing = Array.from({ length: uploadState.partCount }, (_, index) => index + 1)
            .filter((partNumber) => !confirmedNumbers.has(partNumber))
            .slice(0, CONCURRENCY);
          const authorized = await api.request<AuthorizedPartsResponse>(
            `/api/uploads/${uploadState.uploadSessionId}/parts/authorize`,
            {
              method: "POST",
              signal: abortController.current.signal,
              body: JSON.stringify({ partNumbers: missing }),
            },
          );
          confirmed = authorized.confirmedParts;
          await Promise.all(
            authorized.authorizedParts.map(async (part) => {
              const start = (part.partNumber - 1) * uploadState.partSizeBytes;
              const end = Math.min(file.size, start + uploadState.partSizeBytes);
              const response = await fetch(part.url, {
                method: "PUT",
                body: file.slice(start, end),
                headers: { "Content-Type": file.type || "application/octet-stream" },
                signal: abortController.current?.signal,
              });
              if (!response.ok) throw new Error("Part upload failed");
            }),
          );
          const reconciled = await api.request<UploadStateResponse>(
            `/api/uploads/${uploadState.uploadSessionId}`,
            { signal: abortController.current.signal },
          );
          if (reconciled.confirmedParts.length <= confirmed.length) stalledAttempts += 1;
          else stalledAttempts = 0;
          confirmed = reconciled.confirmedParts;
          const confirmedBytes = confirmed.reduce((total, part) => total + part.sizeBytes, 0);
          setState((current) => ({
            ...current,
            progress: Math.min(1, confirmedBytes / file.size),
          }));
          if (stalledAttempts >= MAX_STALLED_ATTEMPTS) {
            throw new Error("Upload confirmation stalled");
          }
        }

        setState((current) => ({ ...current, phase: "completing", progress: 1 }));
        const completed = await api.request<CompleteUploadResponse>(
          `/api/uploads/${uploadState.uploadSessionId}/complete`,
          { method: "POST", signal: abortController.current.signal },
        );
        clearPendingUpload();
        setState({ phase: "idle", progress: 1, pending: null, error: null });
        await onQueued(completed.recordingId);
      } catch (error: unknown) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setState((current) => ({
          ...current,
          phase: "error",
          error: "The upload paused. Your saved parts are safe - choose the file to continue.",
        }));
      } finally {
        abortController.current = null;
      }
    },
    [api, onQueued],
  );

  const start = useCallback(
    (file: File, language: Language) => upload(file, language),
    [upload],
  );
  const resume = useCallback(
    (file: File) => {
      if (!state.pending) return Promise.resolve();
      return upload(file, state.pending.language, state.pending);
    },
    [state.pending, upload],
  );
  const cancel = useCallback(async () => {
    abortController.current?.abort();
    if (state.pending?.uploadSessionId) {
      try {
        await api.request<void>(`/api/uploads/${state.pending.uploadSessionId}/abort`, {
          method: "POST",
        });
      } catch {
        // The server remains the source of truth and can reconcile the session later.
      }
    }
    clearPendingUpload();
    setState({ phase: "idle", progress: 0, pending: null, error: null });
  }, [api, state.pending]);

  return { state, start, resume, cancel };
}
