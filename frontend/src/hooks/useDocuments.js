import { useState } from "react";
import { parseJsonResponse } from "../lib/api";

// Indexed-document listing, upload, and Drive ingest controls.
export function useDocuments({ apiFetch, authHeaders, setError, setNotice }) {
  const [documents, setDocuments] = useState([]);
  const [driveFiles, setDriveFiles] = useState([]);
  const [documentStatus, setDocumentStatus] = useState("Ready");
  const [isLoadingDocuments, setIsLoadingDocuments] = useState(false);
  const [isLoadingDrive, setIsLoadingDrive] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isIngestingDrive, setIsIngestingDrive] = useState(false);
  // Array of File objects — either a single file or every file picked from a
  // folder (input has `multiple` + `webkitdirectory`), so the same state
  // shape covers both cases without a separate code path.
  const [uploadFiles, setUploadFiles] = useState([]);
  const [uploadMatterId, setUploadMatterId] = useState("");
  const [uploadAccessLevel, setUploadAccessLevel] = useState("1");
  const [uploadProgress, setUploadProgress] = useState(null); // { done, total } while a batch is running

  async function loadDocuments() {
    setIsLoadingDocuments(true);
    setDocumentStatus("Loading documents");
    try {
      const response = await apiFetch("/documents", { headers: authHeaders() });
      const data = await parseJsonResponse(response, "Could not load documents");
      if (!response.ok) throw new Error(data.error || data.detail || "Could not load documents");
      setDocuments(data.documents || []);
      setDocumentStatus(`Loaded ${(data.documents || []).length} documents`);
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") {
        setError(err.message);
        setDocumentStatus("Document load failed");
      }
    } finally {
      setIsLoadingDocuments(false);
    }
  }

  async function uploadOneFile(file) {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("matter_id", uploadMatterId);
    formData.append("access_level", uploadAccessLevel);

    const response = await apiFetch("/documents/upload", {
      method: "POST",
      headers: authHeaders(),
      body: formData,
    });
    const data = await parseJsonResponse(response, "Upload failed");
    if (!response.ok) throw new Error(data.error || data.detail || "Upload failed");
    return data;
  }

  // Uploads every selected file sequentially against the existing
  // single-file endpoint (one HTTP request per file). Deliberately NOT a
  // single batch request: each file must go through its own server-side
  // classification (authz.policy.classify_upload) and its own audit-trail
  // entry, exactly as a one-at-a-time upload does today — a folder of 50
  // files is not an excuse to weaken or bypass that per-file check.
  async function uploadDocument(event) {
    event.preventDefault();
    if (!uploadFiles.length) return;
    setError("");
    setNotice("");
    setIsUploading(true);
    setUploadProgress({ done: 0, total: uploadFiles.length });

    let indexedChunks = 0;
    let succeeded = 0;
    const failures = [];

    for (const file of uploadFiles) {
      setDocumentStatus(`Uploading ${file.name} (${succeeded + failures.length + 1}/${uploadFiles.length})`);
      try {
        const data = await uploadOneFile(file);
        indexedChunks += data.chunks || 0;
        succeeded += 1;
      } catch (err) {
        if (err.message === "UNAUTHORIZED") {
          setIsUploading(false);
          setUploadProgress(null);
          return;
        }
        failures.push({ name: file.name, message: err.message });
      } finally {
        setUploadProgress((prev) => ({ done: (prev?.done || 0) + 1, total: uploadFiles.length }));
      }
    }

    setUploadFiles([]);
    setUploadProgress(null);
    setIsUploading(false);

    if (uploadFiles.length === 1) {
      if (succeeded === 1) {
        setNotice(`Indexed ${indexedChunks} chunks from ${uploadFiles[0].name}.`);
        setDocumentStatus("Upload indexed");
      } else {
        setError(failures[0]?.message || "Upload failed");
        setDocumentStatus("Upload failed");
      }
    } else {
      setNotice(
        `Folder upload complete: ${succeeded}/${uploadFiles.length} files indexed (${indexedChunks} chunks total).` +
          (failures.length ? ` ${failures.length} failed: ${failures.map((f) => f.name).join(", ")}` : "")
      );
      setDocumentStatus(failures.length ? "Folder upload finished with errors" : "Folder upload indexed");
    }

    await loadDocuments();
  }

  async function loadDriveFiles() {
    setError("");
    setIsLoadingDrive(true);
    setDocumentStatus("Loading Drive files");
    try {
      const response = await apiFetch("/documents/drive-files", { headers: authHeaders() });
      const data = await parseJsonResponse(response, "Could not load Drive files");
      if (!response.ok) throw new Error(data.error || data.detail || "Could not load Drive files");
      setDriveFiles(data.files || []);
      setDocumentStatus(`Loaded ${(data.files || []).length} Drive files`);
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") {
        setError(err.message);
        setDocumentStatus("Drive load failed");
      }
    } finally {
      setIsLoadingDrive(false);
    }
  }

  async function ingestDriveFile(fileId) {
    setError("");
    setNotice("");
    setIsIngestingDrive(true);
    setDocumentStatus("Indexing Drive file");
    try {
      const response = await apiFetch("/documents/ingest-file", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ file_id: fileId, matter_id: uploadMatterId }),
      });
      const data = await parseJsonResponse(response, "Drive ingest failed");
      if (!response.ok) throw new Error(data.error || data.detail || "Drive ingest failed");
      setNotice(`Drive file indexed: ${data.file_id || fileId}.`);
      // Backend returns {"status": "skipped"} for an unchanged file, never a
      // "skipped" boolean.
      setDocumentStatus(data.status === "skipped" ? "Drive file unchanged" : "Drive file indexed");
      await loadDocuments();
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") {
        setError(err.message);
        setDocumentStatus("Drive ingest failed");
      }
    } finally {
      setIsIngestingDrive(false);
    }
  }

  async function ingestDriveFolder() {
    setError("");
    setNotice("");
    setIsIngestingDrive(true);
    setDocumentStatus("Indexing Drive folder");
    try {
      const response = await apiFetch("/documents/ingest-folder", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ matter_id: uploadMatterId }),
      });
      const data = await parseJsonResponse(response, "Folder ingest failed");
      if (!response.ok) throw new Error(data.error || data.detail || "Folder ingest failed");
      setNotice(`Folder ingest complete: ${data.ok || 0} of ${data.total || 0} files indexed.`);
      setDocumentStatus("Folder ingest complete");
      await loadDocuments();
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") {
        setError(err.message);
        setDocumentStatus("Folder ingest failed");
      }
    } finally {
      setIsIngestingDrive(false);
    }
  }

  return {
    documents,
    driveFiles,
    documentStatus,
    isLoadingDocuments,
    isLoadingDrive,
    isUploading,
    isIngestingDrive,
    uploadFiles,
    setUploadFiles,
    uploadProgress,
    uploadMatterId,
    setUploadMatterId,
    uploadAccessLevel,
    setUploadAccessLevel,
    loadDocuments,
    uploadDocument,
    loadDriveFiles,
    ingestDriveFile,
    ingestDriveFolder,
  };
}
