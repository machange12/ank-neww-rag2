import { FolderKanban, Loader2, RefreshCw, UploadCloud } from "lucide-react";
import { formatDate } from "../lib/format";
import { Panel, EmptyState } from "./common";

export function DocumentsPanel({
  documentStatus,
  documents,
  driveFiles,
  ingestDriveFile,
  ingestDriveFolder,
  isIngestingDrive,
  isLoadingDocuments,
  isLoadingDrive,
  loadDocuments,
  loadDriveFiles,
  setUploadAccessLevel,
  setUploadFiles,
  setUploadMatterId,
  uploadAccessLevel,
  uploadDocument,
  uploadFiles,
  uploadProgress,
  uploadMatterId,
  isUploading,
}) {
  return (
    <Panel title="Documents" subtitle="Indexed files and ingestion controls.">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <button onClick={loadDocuments} disabled={isLoadingDocuments} className="action-button">
          {isLoadingDocuments ? <Loader2 className="animate-spin" size={13} /> : <RefreshCw size={13} />}
          Refresh
        </button>
        <button onClick={loadDriveFiles} disabled={isLoadingDrive} className="action-button">
          {isLoadingDrive ? <Loader2 className="animate-spin" size={13} /> : <FolderKanban size={13} />}
          Drive files
        </button>
        <button onClick={ingestDriveFolder} disabled={isIngestingDrive} className="action-button">
          {isIngestingDrive ? <Loader2 className="animate-spin" size={13} /> : <UploadCloud size={13} />}
          Ingest folder
        </button>
        <span className="text-xs text-gray-500">{documentStatus}</span>
      </div>

      <form onSubmit={uploadDocument} className="mb-5 rounded-lg border border-gray-200 bg-white p-3">
        <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_150px_150px_auto]">
          <label className="block">
            <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-gray-500">
              Files or folder
            </span>
            <div className="flex flex-wrap items-center gap-2">
              <input
                type="file"
                multiple
                onChange={(event) => setUploadFiles(Array.from(event.target.files || []))}
                className="block text-xs text-gray-600 file:mr-3 file:rounded-md file:border-0 file:bg-gray-100 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-gray-700 hover:file:bg-gray-200"
              />
              <label className="cursor-pointer rounded-md border border-gray-300 px-2.5 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50">
                Choose folder
                <input
                  type="file"
                  multiple
                  // Chrome/Edge only: opens a directory picker and returns
                  // every file inside it (with webkitRelativePath set).
                  // Firefox/Safari without support just ignore the attribute
                  // and behave like the plain multi-file input above.
                  webkitdirectory=""
                  onChange={(event) => setUploadFiles(Array.from(event.target.files || []))}
                  className="hidden"
                />
              </label>
            </div>
            {uploadFiles.length > 0 && (
              <span className="mt-1 block text-[11px] text-gray-500">
                {uploadFiles.length} file{uploadFiles.length === 1 ? "" : "s"} selected
              </span>
            )}
          </label>
          <label className="block">
            <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-gray-500">Matter</span>
            <input
              value={uploadMatterId}
              onChange={(event) => setUploadMatterId(event.target.value)}
              placeholder="M-2024-118"
              className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-xs focus:border-blue-400 focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-gray-500">Access</span>
            <select
              value={uploadAccessLevel}
              onChange={(event) => setUploadAccessLevel(event.target.value)}
              className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-xs focus:border-blue-400 focus:outline-none"
            >
              {[1, 2, 3, 4, 5].map((level) => (
                <option key={level} value={level}>{level}</option>
              ))}
            </select>
          </label>
          <button
            disabled={!uploadFiles.length || isUploading}
            className="self-end rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300"
          >
            {isUploading
              ? uploadProgress
                ? `Indexing ${uploadProgress.done}/${uploadProgress.total}`
                : "Indexing"
              : uploadFiles.length > 1
                ? `Upload ${uploadFiles.length} files`
                : "Upload"}
          </button>
        </div>
      </form>

      <div className="grid gap-4 xl:grid-cols-2">
        <section>
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-500">Indexed documents</h2>
            <span className="text-xs text-gray-400">{documents.length}</span>
          </div>
          {documents.length === 0 ? (
            <EmptyState text="No indexed documents visible to this user." />
          ) : (
            <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
              {documents.map((doc) => (
                <div key={doc.file_id || doc.id} className="border-b border-gray-100 p-3 last:border-b-0">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-gray-900">{doc.file_title || doc.file_id}</div>
                      <div className="mt-1 text-xs text-gray-500">
                        {doc.matter_id || "No matter"} - Level {doc.access_level || 1} - {formatDate(doc.ingested_at)}
                      </div>
                    </div>
                    {doc.url && (
                      <a href={doc.url} target="_blank" rel="noreferrer" className="text-xs font-medium text-blue-600 hover:text-blue-700">
                        Open
                      </a>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section>
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-500">Drive files</h2>
            <span className="text-xs text-gray-400">{driveFiles.length}</span>
          </div>
          {driveFiles.length === 0 ? (
            <EmptyState text="Load Drive files to ingest from Google Drive." />
          ) : (
            <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
              {driveFiles.map((file) => (
                <div key={file.id} className="flex items-center justify-between gap-3 border-b border-gray-100 p-3 last:border-b-0">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-gray-900">{file.name}</div>
                    <div className="mt-1 truncate text-xs text-gray-500">{file.mimeType}</div>
                  </div>
                  <button
                    onClick={() => ingestDriveFile(file.id)}
                    disabled={isIngestingDrive}
                    className="rounded-md border border-blue-200 px-2.5 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Ingest
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </Panel>
  );
}
