import { act, renderHook, waitFor } from "@testing-library/react";

import type { ApiClient } from "../src/api/client.ts";
import { useMultipartUpload } from "../src/recordings/useMultipartUpload.ts";

describe("useMultipartUpload", () => {
  it("uploads missing parts, reconciles them, completes, and clears the resume marker", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "request-1" });
    const directUpload = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 200 }));
    const request = vi.fn(async (path: string) => {
      if (path === "/api/uploads") {
        return {
          recordingId: "recording-1",
          uploadSessionId: "upload-1",
          partSizeBytes: 32,
          partCount: 2,
          expiresAt: "2026-08-05T12:00:00Z",
          status: "uploading",
          confirmedParts: [],
        };
      }
      if (path.endsWith("/parts/authorize")) {
        return {
          authorizedParts: [
            { partNumber: 1, url: "https://bucket.test/one" },
            { partNumber: 2, url: "https://bucket.test/two" },
          ],
          confirmedParts: [],
          expiresAt: "2026-08-05T12:00:00Z",
        };
      }
      if (path === "/api/uploads/upload-1") {
        return {
          recordingId: "recording-1",
          uploadSessionId: "upload-1",
          partSizeBytes: 32,
          partCount: 2,
          expiresAt: "2026-08-05T12:00:00Z",
          status: "uploading",
          confirmedParts: [
            { partNumber: 1, sizeBytes: 32 },
            { partNumber: 2, sizeBytes: 8 },
          ],
        };
      }
      if (path.endsWith("/complete")) {
        return { recordingId: "recording-1", status: "queued" };
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    const onQueued = vi.fn();
    const { result } = renderHook(() =>
      useMultipartUpload({
        api: { request } as unknown as Pick<ApiClient, "request">,
        username: "assad",
        onQueued,
      }),
    );
    const file = new File(["x".repeat(40)], "interview.m4a", {
      type: "audio/mp4",
      lastModified: 10,
    });

    await act(async () => {
      await result.current.start(file, "de");
    });

    await waitFor(() => expect(onQueued).toHaveBeenCalledWith("recording-1"));
    expect(directUpload).toHaveBeenCalledTimes(2);
    expect(request).toHaveBeenCalledWith(
      "/api/uploads/upload-1/complete",
      expect.objectContaining({ method: "POST" }),
    );
    expect(result.current.state.progress).toBe(1);
    expect(localStorage.getItem("transcriber.pending-upload.v2.assad")).toBeNull();
  });
});
