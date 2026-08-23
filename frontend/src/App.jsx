import { useEffect, useState } from "react";
import { BarChart3, BookOpenText, CheckCircle2, ExternalLink, Loader2, Search, Send, Users } from "lucide-react";

import { LoginLanding } from "./components/LoginLanding";
import { ResearchPanel } from "./components/ResearchPanel";
import { HistoryPanel } from "./components/HistoryPanel";
import { DocumentsPanel } from "./components/DocumentsPanel";
import { SimpleList } from "./components/SimpleList";
import { TeamPanel } from "./components/TeamPanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { useAuth } from "./hooks/useAuth";
import { useChat } from "./hooks/useChat";
import { useDocuments } from "./hooks/useDocuments";
import { useBanner } from "./hooks/useBanner";
import { initials } from "./lib/format";
import { NAV, SAMPLE_MATTERS } from "./lib/constants";

export default function ANKRagDashboard() {
  const [activeNav, setActiveNav] = useState("Research");
  const [status, setStatus] = useState("Ready");
  const [compactMode, setCompactMode] = useState(false);

  const banner = useBanner();
  const auth = useAuth({ setStatus, setActiveNav, setError: banner.setError, setNotice: banner.setNotice });
  const chat = useChat({
    apiFetch: auth.apiFetch,
    token: auth.token,
    authHeaders: auth.authHeaders,
    setStatus,
    setActiveNav,
    setError: banner.setError,
    setNotice: banner.setNotice,
  });
  const documents = useDocuments({
    apiFetch: auth.apiFetch,
    authHeaders: auth.authHeaders,
    setError: banner.setError,
    setNotice: banner.setNotice,
  });

  const queryCount = chat.history.length;

  useEffect(() => {
    if (auth.token) {
      documents.loadDocuments();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth.token]);

  if (!auth.token) {
    return (
      <LoginLanding
        email={auth.email}
        password={auth.password}
        error={banner.error}
        isLoggingIn={auth.isLoggingIn}
        setEmail={auth.setEmail}
        setPassword={auth.setPassword}
        login={auth.login}
      />
    );
  }

  const panels = {
    Research: (
      <ResearchPanel
        answer={chat.answer}
        ask={chat.ask}
        copyAnswer={chat.copyAnswer}
        error={banner.error}
        exportAnswer={chat.exportAnswer}
        isAsking={chat.isAsking}
        lastLatency={chat.lastLatency}
        notice={banner.notice}
        query={chat.query}
        saveAnswer={chat.saveAnswer}
        sources={chat.sources}
        status={status}
      />
    ),
    History: <HistoryPanel history={chat.history} restoreHistory={chat.restoreHistory} />,
    Documents: (
      <DocumentsPanel
        documentStatus={documents.documentStatus}
        documents={documents.documents}
        driveFiles={documents.driveFiles}
        ingestDriveFile={documents.ingestDriveFile}
        ingestDriveFolder={documents.ingestDriveFolder}
        isIngestingDrive={documents.isIngestingDrive}
        isLoadingDocuments={documents.isLoadingDocuments}
        isLoadingDrive={documents.isLoadingDrive}
        loadDocuments={documents.loadDocuments}
        loadDriveFiles={documents.loadDriveFiles}
        setUploadAccessLevel={documents.setUploadAccessLevel}
        setUploadFiles={documents.setUploadFiles}
        setUploadMatterId={documents.setUploadMatterId}
        uploadAccessLevel={documents.uploadAccessLevel}
        uploadDocument={documents.uploadDocument}
        uploadFiles={documents.uploadFiles}
        uploadProgress={documents.uploadProgress}
        uploadMatterId={documents.uploadMatterId}
        isUploading={documents.isUploading}
      />
    ),
    Matters: <SimpleList title="Matter access" rows={SAMPLE_MATTERS} />,
    Team: <TeamPanel userLabel={auth.userLabel} />,
    Settings: <SettingsPanel compactMode={compactMode} setCompactMode={setCompactMode} signOut={auth.signOut} />,
  };

  return (
    <div className={`flex h-screen bg-gray-50 font-sans text-sm text-gray-900 ${compactMode ? "text-xs" : ""}`}>
      <aside className="hidden w-52 flex-shrink-0 flex-col border-r border-gray-200 bg-white md:flex">
        <div className="border-b border-gray-100 px-4 py-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-900">ANK RAG</div>
          <div className="mt-0.5 text-xs text-gray-400">Just Giving Solutions</div>
        </div>

        <nav className="flex-1 space-y-0.5 px-2 py-3">
          <div className="mb-1 px-2 text-[10px] uppercase tracking-widest text-gray-400">Workspace</div>
          {NAV.map((item) => (
            <button
              key={item.label}
              onClick={() => setActiveNav(item.label)}
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-xs transition-colors ${
                activeNav === item.label
                  ? "bg-blue-50 font-medium text-blue-700"
                  : "text-gray-500 hover:bg-gray-50 hover:text-gray-800"
              }`}
            >
              <item.icon size={14} /> {item.label}
            </button>
          ))}
        </nav>

        <div className="border-t border-gray-100 px-4 py-3">
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-full bg-blue-100 text-xs font-semibold text-blue-700">
              {initials(auth.userLabel)}
            </div>
            <span className="truncate text-xs text-gray-500">{auth.userLabel}</span>
          </div>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <form onSubmit={chat.ask} className="flex items-center gap-3 border-b border-gray-200 bg-white px-4 py-3 md:px-5">
          <div className="relative flex flex-1 items-center">
            <Search className="absolute left-2.5 text-gray-400" size={16} />
            <input
              className="w-full rounded-lg border border-gray-300 bg-white py-1.5 pl-8 pr-3 text-sm text-gray-800 placeholder-gray-400 focus:border-blue-400 focus:outline-none"
              value={chat.query}
              onChange={(event) => chat.setQuery(event.target.value)}
              placeholder="Ask a question about your indexed legal documents"
            />
          </div>
          <button
            disabled={chat.isAsking}
            className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300"
          >
            {chat.isAsking ? <Loader2 className="animate-spin" size={14} /> : <Send size={14} />}
            Ask
          </button>
        </form>

        <div className="flex min-h-0 flex-1 flex-col overflow-hidden lg:flex-row">
          <section className="w-full flex-shrink-0 border-b border-gray-200 bg-white p-3 lg:w-64 lg:border-b-0 lg:border-r">
            <div className="mb-2 text-[10px] uppercase tracking-widest text-gray-400">Sources retrieved</div>
            <div className="max-h-56 overflow-y-auto lg:max-h-none">
              {chat.sources.length === 0 ? (
                <div className="rounded-lg border border-dashed border-gray-300 bg-white px-3 py-4 text-center text-xs text-gray-400">
                  No sources for this answer yet.
                </div>
              ) : (
                chat.sources.map((source) => (
                  // Each card opens the retrieved document itself (the
                  // backend only ever includes sources that have a real
                  // Drive/storage url), not just a citation label.
                  <a
                    key={source.id}
                    href={source.url}
                    target="_blank"
                    rel="noreferrer"
                    title={source.title}
                    onClick={() => chat.setActiveSource(source.id)}
                    className={`mb-2 block w-full rounded-lg border p-2.5 text-left transition-colors ${
                      chat.activeSource === source.id
                        ? "border-blue-300 bg-blue-50"
                        : "border-gray-200 bg-white hover:border-blue-200"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className={`truncate text-xs font-medium ${chat.activeSource === source.id ? "text-blue-700" : "text-gray-800"}`}>
                        {source.title}
                      </div>
                      <ExternalLink
                        size={11}
                        className={`flex-shrink-0 ${chat.activeSource === source.id ? "text-blue-500" : "text-gray-300"}`}
                      />
                    </div>
                    <div className={`mt-0.5 truncate text-[11px] ${chat.activeSource === source.id ? "text-blue-500" : "text-gray-400"}`}>
                      {source.meta}
                    </div>
                  </a>
                ))
              )}
            </div>
          </section>

          <section className="min-w-0 flex-1 overflow-y-auto bg-gray-50 p-5">{panels[activeNav]}</section>
        </div>

        <footer className="grid grid-cols-2 gap-3 border-t border-gray-200 bg-white px-5 py-3 md:grid-cols-4">
          {[
            { label: "Queries today", value: String(queryCount), accent: "text-blue-600", icon: BarChart3 },
            { label: "Docs indexed", value: String(documents.documents.length), accent: "text-gray-800", icon: BookOpenText },
            { label: "Last response", value: chat.lastLatency ? `${chat.lastLatency} s` : "-", accent: "text-green-600", icon: CheckCircle2 },
            { label: "Access level", value: auth.accessLevel != null ? String(auth.accessLevel) : "-", accent: "text-gray-800", icon: Users },
          ].map((metric) => (
            <div key={metric.label} className="rounded-lg bg-gray-50 px-3 py-2">
              <div className="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-widest text-gray-400">
                <metric.icon size={12} /> {metric.label}
              </div>
              <div className={`text-base font-semibold ${metric.accent}`}>{metric.value}</div>
            </div>
          ))}
        </footer>
      </main>
    </div>
  );
}
