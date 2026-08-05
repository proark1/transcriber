import {
  clearPendingUpload,
  loadPendingUpload,
  savePendingUpload,
  signatureForFile,
} from "../src/recordings/uploadPersistence.ts";

describe("pending upload persistence", () => {
  it("stores only the identifiers and file description needed to resume", () => {
    const file = new File(["audio"], "voice.m4a", {
      type: "audio/mp4",
      lastModified: 42,
    });
    savePendingUpload("assad", {
      clientRequestId: "request-1",
      uploadSessionId: "upload-1",
      recordingId: "recording-1",
      fileSignature: signatureForFile(file),
      filename: file.name,
      sizeBytes: file.size,
      language: "tr",
    });

    const raw = localStorage.getItem("transcriber.pending-upload.v2.assad") ?? "";
    expect(raw).not.toMatch(/https?:|audioData|transcript|credential|csrf|pin/i);
    expect(loadPendingUpload("assad")).toEqual(
      expect.objectContaining({
        uploadSessionId: "upload-1",
        filename: "voice.m4a",
        language: "tr",
      }),
    );

    clearPendingUpload("assad");
    expect(loadPendingUpload("assad")).toBeNull();
  });

  it("keeps pending uploads separate for each account", () => {
    savePendingUpload("assad", {
      clientRequestId: "request-1",
      uploadSessionId: "upload-1",
      recordingId: "recording-1",
      fileSignature: "one:1:1",
      filename: "one.m4a",
      sizeBytes: 1,
      language: "en",
    });

    expect(loadPendingUpload("other-user")).toBeNull();
    expect(loadPendingUpload("assad")?.filename).toBe("one.m4a");
  });

  it("uses name, size, and last-modified time to identify the same file", () => {
    const first = new File(["same"], "recording.wav", { lastModified: 123 });
    const same = new File(["same"], "recording.wav", { lastModified: 123 });
    const newer = new File(["same"], "recording.wav", { lastModified: 124 });

    expect(signatureForFile(first)).toBe(signatureForFile(same));
    expect(signatureForFile(first)).not.toBe(signatureForFile(newer));
  });
});
