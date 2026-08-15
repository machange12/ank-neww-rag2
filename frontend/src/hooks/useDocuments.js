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
  const [uploadFile, setUploadFile] = useState(null);
  const [uploadMatterId, setUploadMatterId] = useState("");
  const [uploadAccessLevel, setUploadAccessLevel] = useState("1");

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

  async function uploadDocument(event) {
    event.preventDefault();
    if (!uploadFile) return;
    setError("");
    setNotice("");
    setIsUploading(true);
    setDocumentStatus("Uploading document");
    try {
      const formData = new FormData();
      formData.append("file", uploadFile);
      formData.append("matter_id", uploadMatterId);
      formData.append("access_level", uploadAccessLevel);

      const response = await apiFetch("/documents/upload", {
        method: "POST",
        headers: authHeaders(),
        body: formData,
      });
      const data = await parseJsonResponse(response, "Upload failed");
      if (!response.ok) throw new Error(data.error || data.detail || "Upload failed");
      setUploadFile(null);
      setNotice(`Indexed ${data.chunks || 0} chunks from ${data.file_id || uploadFile.name}.`);
      setDocumentStatus("Upload indexed");
      await loadDocuments();
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") {
        setError(err.message);
        setDocumentStatus("Upload failed");
      }
    } finally {
      setIsUploading(false);
    }
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
    uploadFile,
    setUploadFile,
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
