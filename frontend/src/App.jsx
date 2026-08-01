import { useMemo, useState } from "react";
import {
  BarChart3,
  BookOpenText,
  CheckCircle2,
  Clipboard,
  Download,
  FileText,
  FolderKanban,
  History,
  Loader2,
  LogIn,
  LogOut,
  RefreshCw,
  Save,
  Search,
  Send,
  Settings,
  ShieldCheck,
  Sparkles,
  UploadCloud,
  Users,
} from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "";
const DEFAULT_QUERY =
  "What are the notice requirements under Kenya's DPA for data breach disclosure?";

const PROMPT_STARTERS = [
  "Summarise the key obligations in",
  "Compare the risks between",
  "Find authorities that support",
  "Draft a client-ready memo about",
  "Extract deadlines, notice periods, and responsible parties from",
  "Identify privileged or confidential issues in",
  "What does the indexed material say about",
  "Create a matter status brief for",
];

const PREBAKED_PROMPTS = [
  "Summarise the latest documents for matter M-2024-118 and list open risks.",
  "What are the notice requirements under Kenya's DPA for a personal data breach?",
  "Find cases or guidance that support a conservative disclosure timeline.",
  "Draft a client-ready memo with facts, law, analysis, and next steps.",
  "Compare the firm's internal memo against the statutory position.",
  "List documents that mention privileged advice and explain access limits.",
];

const NAV = [
  { icon: Search, label: "Research" },
  { icon: History, label: "History" },
  { icon: FileText, label: "Documents" },
  { icon: FolderKanban, label: "Matters" },
  { icon: Users, label: "Team" },
  { icon: Settings, label: "Settings" },
];

const SAMPLE_MATTERS = [
  { id: "M-2024-118", name: "Data breach advisory", access: "Partner" },
  { id: "M-2025-001", name: "Employment privacy review", access: "Associate" },
  { id: "M-2024-001", name: "Commercial litigation", access: "Matter team" },
];

const SAMPLE_DOCS = [
  { id: "doc-001", title: "NDA Review - M-2024-118", type: "Contract", status: "Indexed" },
  { id: "doc-002", title: "Privacy incident checklist", type: "Policy", status: "Indexed" },
  { id: "doc-003", title: "Disclosure memo draft", type: "Memo", status: "Indexed" },
];

function sourceTitleFromCitation(citation) {
  return citation.replace(/^\[|\]$/g, "").trim() || "Retrieved source";
}

function extractSources(answer) {
  const citations = Array.from(answer.matchAll(/\[[^\]]+\]/g)).map((match) => match[0]);
  const unique = [...new Set(citations)].slice(0, 8);
  if (!unique.length) {
    return [{ id: "rag", title: "RAG response", meta: "Generated answer", relevance: 78 }];
  }
  return unique.map((citation, index) => ({
    id: citation,
    title: sourceTitleFromCitation(citation),
    meta: "Citation found in answer",
    relevance: Math.max(48, 94 - index * 9),
  }));
}

function initials(label) {
  if (!label || label === "Not signed in") return "AN";
  return label
    .split("@")[0]
    .split(/[.\s_-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

function downloadText(filename, text) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function buildSuggestions(query) {
  const text = query.trim();
  if (text.length < 3) return [];
  const lower = text.toLowerCase();
  const matches = PROMPT_STARTERS.filter((starter) => starter.toLowerCase().includes(lower) || lower.includes(starter.toLowerCase().slice(0, 8)));
  const base = matches.length ? matches : PROMPT_STARTERS;
  return base.slice(0, 4).map((starter) => {
    if (lower.startsWith(starter.toLowerCase())) return text;
    return `${starter} ${text}`;
  });
}

function LoginLanding({ email, password, error, isLoggingIn, setEmail, setPassword, login }) {
  return (
    <main className="min-h-screen bg-gray-50 text-gray-900">
      <div className="grid min-h-screen lg:grid-cols-[1fr_420px]">
        <section className="flex flex-col justify-between border-r border-gray-200 bg-white px-8 py-8 lg:px-12">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-600 text-sm font-semibold text-white">
              AN
            </div>
            <div>
              <div className="text-sm font-semibold uppercase tracking-wide">ANK RAG</div>
              <div className="text-xs text-gray-500">Secure legal research workspace</div>
            </div>
          </div>

          <div className="max-w-2xl py-16">
            <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700">
              <ShieldCheck size={14} /> Role-aware document retrieval
            </div>
            <h1 className="font-serif text-4xl font-semibold leading-tight text-gray-950 md:text-5xl">
              Ask your firm's indexed knowledge base with access controls intact.
            </h1>
            <p className="mt-5 max-w-xl text-sm leading-6 text-gray-600">
              Sign in with your Supabase user account to open the RAG dashboard, search secure matter files, and save answers for later review.
            </p>
          </div>

          <div className="grid gap-3 text-xs text-gray-500 md:grid-cols-3">
            <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
              <div className="mb-1 font-medium text-gray-900">JWT auth</div>
              User sessions go through the backend login endpoint.
            </div>
            <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
              <div className="mb-1 font-medium text-gray-900">RLS-aware</div>
              Chat requests use the signed-in user's token.
            </div>
            <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
              <div className="mb-1 font-medium text-gray-900">Workspace tools</div>
              History, sources, copy, export, and save controls are active.
            </div>
          </div>
        </section>

        <section className="flex items-center justify-center px-6 py-10">
          <form onSubmit={login} className="w-full max-w-sm rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
            <div className="mb-5">
              <h2 className="text-lg font-semibold">Sign in</h2>
              <p className="mt-1 text-xs text-gray-500">Use a Supabase Auth user for this law firm RAG.</p>
            </div>

            <label className="mb-1 block text-xs font-medium text-gray-600">Email</label>
            <input
              className="mb-3 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="partner@ak.law"
              required
            />

            <label className="mb-1 block text-xs font-medium text-gray-600">Password</label>
            <input
              className="mb-4 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Password"
              required
            />

            {error && <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}

            <button
              disabled={isLoggingIn}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300"
            >
              {isLoggingIn ? <Loader2 className="animate-spin" size={16} /> : <LogIn size={16} />}
              Continue to dashboard
            </button>
          </form>
        </section>
      </div>
    </main>
  );
}

export default function ANKRagDashboard() {
  const [activeSource, setActiveSource] = useState("rag");
  const [query, setQuery] = useState(DEFAULT_QUERY);
  const [activeNav, setActiveNav] = useState("Research");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState(() => localStorage.getItem("ank_rag_token") || "");
  const [userLabel, setUserLabel] = useState(() => localStorage.getItem("ank_rag_user") || "Not signed in");
  const [answer, setAnswer] = useState("");
  const [status, setStatus] = useState("Ready");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [isAsking, setIsAsking] = useState(false);
  const [lastLatency, setLastLatency] = useState(null);
  const [history, setHistory] = useState(() => JSON.parse(localStorage.getItem("ank_rag_history") || "[]"));
  const [saved, setSaved] = useState(() => JSON.parse(localStorage.getItem("ank_rag_saved") || "[]"));
  const [compactMode, setCompactMode] = useState(false);

  const sources = useMemo(() => extractSources(answer), [answer]);
  const queryCount = history.length;

  async function login(event) {
    event.preventDefault();
    setError("");
    setNotice("");
    setIsLoggingIn(true);
    try {
      const response = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const responseText = await response.text();
      let data = {};
      try {
        data = JSON.parse(responseText);
      } catch {
        data = { detail: responseText || "Login failed" };
      }
      if (!response.ok) throw new Error(data.error || data.detail || "Login failed");
      setToken(data.access_token);
      localStorage.setItem("ank_rag_token", data.access_token);
      setUserLabel(email);
      localStorage.setItem("ank_rag_user", email);
      setStatus("Signed in");
      setActiveNav("Research");
    } catch (err) {
      const message = err.message || "Login failed";
      const cleaned = message
        .replace(/login_failed:/i, "")
        .replace(/login failed/i, "")
        .replace(/invalid login credentials/i, "Invalid email or password")
        .replace(/invalid credentials/i, "Invalid email or password")
        .trim();
      setError(cleaned || "Login failed");
    } finally {
      setIsLoggingIn(false);
    }
  }

  async function ask(event) {
    event?.preventDefault();
    if (!query.trim()) return;
    setError("");
    setNotice("");
    setIsAsking(true);
    setStatus("Searching secured documents");
    const started = performance.now();
    try {
      const response = await fetch(`${API_BASE}/lawfirm-chat-trigger-006`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ chatInput: query, sessionId: "ank-dashboard" }),
      });
      const responseText = await response.text();
      let data = {};
      try {
        data = JSON.parse(responseText);
      } catch {
        data = { detail: responseText || "RAG request failed" };
      }
      if (!response.ok) throw new Error(data.error || data.detail || "RAG request failed");
      const output = data.output || "";
      const latency = ((performance.now() - started) / 1000).toFixed(1);
      const item = { id: crypto.randomUUID(), query, answer: output, latency, createdAt: new Date().toISOString() };
      const nextHistory = [item, ...history].slice(0, 25);
      localStorage.setItem("ank_rag_history", JSON.stringify(nextHistory));
      setHistory(nextHistory);
      setAnswer(output);
      setActiveSource("rag");
      setLastLatency(latency);
      setStatus("Answer ready");
      setActiveNav("Research");
    } catch (err) {
      setError(err.message);
      setStatus("Request failed");
    } finally {
      setIsAsking(false);
    }
  }

  function signOut() {
    localStorage.removeItem("ank_rag_token");
    localStorage.removeItem("ank_rag_user");
    setToken("");
    setUserLabel("Not signed in");
    setStatus("Signed out");
    setError("");
    setNotice("");
  }

  function restoreHistory(item) {
    setQuery(item.query);
    setAnswer(item.answer);
    setLastLatency(item.latency);
    setActiveNav("Research");
    setStatus("Restored from history");
  }

  function saveAnswer() {
    if (!answer) return;
    const item = { id: crypto.randomUUID(), query, answer, createdAt: new Date().toISOString() };
    const nextSaved = [item, ...saved].slice(0, 25);
    localStorage.setItem("ank_rag_saved", JSON.stringify(nextSaved));
    setSaved(nextSaved);
    setNotice("Answer saved to History.");
  }

  function copyAnswer() {
    if (!answer) return;
    navigator.clipboard?.writeText(answer);
    setNotice("Answer copied to clipboard.");
  }

  function exportAnswer() {
    if (!answer) return;
    downloadText("ank-rag-answer.txt", `Question:\n${query}\n\nAnswer:\n${answer}\n`);
    setNotice("Memo exported.");
  }

  if (!token) {
    return (
      <LoginLanding
        email={email}
        password={password}
        error={error}
        isLoggingIn={isLoggingIn}
        setEmail={setEmail}
        setPassword={setPassword}
        login={login}
      />
    );
  }

  const panels = {
    Research: (
      <ResearchPanel
        answer={answer}
        ask={ask}
        copyAnswer={copyAnswer}
        error={error}
        exportAnswer={exportAnswer}
        isAsking={isAsking}
        lastLatency={lastLatency}
        notice={notice}
        query={query}
        saveAnswer={saveAnswer}
        sources={sources}
        status={status}
      />
    ),
    History: <HistoryPanel history={history} restoreHistory={restoreHistory} />,
    Documents: <SimpleList title="Indexed documents" rows={SAMPLE_DOCS} />,
    Matters: <SimpleList title="Matter access" rows={SAMPLE_MATTERS} />,
    Team: <TeamPanel userLabel={userLabel} />,
    Settings: <SettingsPanel compactMode={compactMode} setCompactMode={setCompactMode} signOut={signOut} />,
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
              {initials(userLabel)}
            </div>
            <span className="truncate text-xs text-gray-500">{userLabel}</span>
          </div>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <form onSubmit={ask} className="flex items-center gap-3 border-b border-gray-200 bg-white px-4 py-3 md:px-5">
          <div className="relative flex flex-1 items-center">
            <Search className="absolute left-2.5 text-gray-400" size={16} />
            <input
              className="w-full rounded-lg border border-gray-300 bg-white py-1.5 pl-8 pr-3 text-sm text-gray-800 placeholder-gray-400 focus:border-blue-400 focus:outline-none"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Ask a question about your indexed legal documents"
            />
          </div>
          <button
            disabled={isAsking}
            className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300"
          >
            {isAsking ? <Loader2 className="animate-spin" size={14} /> : <Send size={14} />}
            Ask
          </button>
        </form>

        <div className="flex min-h-0 flex-1 flex-col overflow-hidden lg:flex-row">
          <section className="w-full flex-shrink-0 border-b border-gray-200 bg-white p-3 lg:w-64 lg:border-b-0 lg:border-r">
            <div className="mb-2 text-[10px] uppercase tracking-widest text-gray-400">Sources retrieved</div>
            <div className="max-h-56 overflow-y-auto lg:max-h-none">
              {sources.map((source) => (
                <button
                  key={source.id}
                  onClick={() => setActiveSource(source.id)}
                  className={`mb-2 w-full rounded-lg border p-2.5 text-left transition-colors ${
                    activeSource === source.id
                      ? "border-blue-300 bg-blue-50"
                      : "border-gray-200 bg-white hover:border-blue-200"
                  }`}
                >
                  <div className={`truncate text-xs font-medium ${activeSource === source.id ? "text-blue-700" : "text-gray-800"}`}>
                    {source.title}
                  </div>
                  <div className={`mt-0.5 text-[11px] ${activeSource === source.id ? "text-blue-500" : "text-gray-400"}`}>
                    {source.meta}
                  </div>
                  <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-gray-200">
                    <div className="h-full rounded-full bg-blue-500" style={{ width: `${source.relevance}%` }} />
                  </div>
                </button>
              ))}
            </div>
          </section>

          <section className="min-w-0 flex-1 overflow-y-auto bg-gray-50 p-5">{panels[activeNav]}</section>
        </div>

        <footer className="grid grid-cols-2 gap-3 border-t border-gray-200 bg-white px-5 py-3 md:grid-cols-4">
          {[
            { label: "Queries today", value: String(queryCount), accent: "text-blue-600", icon: BarChart3 },
            { label: "Docs indexed", value: "RAG", accent: "text-gray-800", icon: BookOpenText },
            { label: "Last response", value: lastLatency ? `${lastLatency} s` : "-", accent: "text-green-600", icon: CheckCircle2 },
            { label: "Active user", value: "1", accent: "text-gray-800", icon: Users },
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

function ResearchPanel({ answer, ask, copyAnswer, error, exportAnswer, isAsking, lastLatency, notice, query, saveAnswer, sources, status }) {
  return (
    <>
      <div className="mb-3 flex items-center gap-1.5 text-[11px] text-gray-400">
        <ShieldCheck size={13} />
        {status} - {sources.length} source view{sources.length === 1 ? "" : "s"} - {lastLatency ? `${lastLatency} s` : "not run yet"}
      </div>
      <h1 className="mb-4 border-b border-gray-200 pb-3 font-serif text-base font-semibold text-gray-900">
        Secure legal research workspace
      </h1>

      {error && <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}
      {notice && <div className="mb-4 rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-xs text-green-700">{notice}</div>}

      <div className="min-h-64 whitespace-pre-wrap text-[13.5px] leading-relaxed text-gray-800">
        {answer || (
          <div className="rounded-lg border border-dashed border-gray-300 bg-white px-4 py-8 text-center text-sm text-gray-500">
            Ask a question and the secured RAG answer will appear here.
          </div>
        )}
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <button onClick={copyAnswer} disabled={!answer} className="action-button">
          <Clipboard size={13} /> Copy answer
        </button>
        <button onClick={exportAnswer} disabled={!answer} className="action-button">
          <Download size={13} /> Export memo
        </button>
        <button onClick={saveAnswer} disabled={!answer} className="action-button">
          <Save size={13} /> Save
        </button>
        <button onClick={ask} disabled={isAsking || !query.trim()} className="action-button">
          <RefreshCw size={13} /> Regenerate
        </button>
      </div>
    </>
  );
}

function HistoryPanel({ history, restoreHistory }) {
  return (
    <Panel title="History" subtitle="Previous RAG questions from this browser.">
      {history.length === 0 ? (
        <EmptyState text="No questions yet." />
      ) : (
        <div className="space-y-2">
          {history.map((item) => (
            <button key={item.id} onClick={() => restoreHistory(item)} className="w-full rounded-lg border border-gray-200 bg-white p-3 text-left hover:border-blue-300">
              <div className="text-sm font-medium text-gray-900">{item.query}</div>
              <div className="mt-1 text-xs text-gray-500">{new Date(item.createdAt).toLocaleString()} - {item.latency}s</div>
            </button>
          ))}
        </div>
      )}
    </Panel>
  );
}

function SimpleList({ title, rows }) {
  return (
    <Panel title={title} subtitle="Local dashboard view for the connected RAG workspace.">
      <div className="grid gap-2">
        {rows.map((row) => (
          <div key={row.title || row.id} className="rounded-lg border border-gray-200 bg-white p-3">
            <div className="text-sm font-medium text-gray-900">{row.title || row.name}</div>
            <div className="mt-1 text-xs text-gray-500">{row.type || row.id} - {row.status || row.access}</div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function TeamPanel({ userLabel }) {
  return (
    <Panel title="Team" subtitle="Current authenticated workspace user.">
      <div className="rounded-lg border border-gray-200 bg-white p-4">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-full bg-blue-100 text-sm font-semibold text-blue-700">
            {initials(userLabel)}
          </div>
          <div>
            <div className="text-sm font-medium text-gray-900">{userLabel}</div>
            <div className="text-xs text-gray-500">Signed in through Supabase Auth</div>
          </div>
        </div>
      </div>
    </Panel>
  );
}

function SettingsPanel({ compactMode, setCompactMode, signOut }) {
  return (
    <Panel title="Settings" subtitle="Dashboard preferences.">
      <div className="space-y-3">
        <label className="flex items-center justify-between rounded-lg border border-gray-200 bg-white p-3 text-sm">
          Compact display
          <input type="checkbox" checked={compactMode} onChange={(event) => setCompactMode(event.target.checked)} />
        </label>
        <button onClick={signOut} className="flex items-center gap-2 rounded-lg border border-red-200 bg-white px-3 py-2 text-sm text-red-700 hover:bg-red-50">
          <LogOut size={15} /> Sign out
        </button>
      </div>
    </Panel>
  );
}

function Panel({ title, subtitle, children }) {
  return (
    <div>
      <h1 className="font-serif text-lg font-semibold text-gray-900">{title}</h1>
      <p className="mb-4 mt-1 text-xs text-gray-500">{subtitle}</p>
      {children}
    </div>
  );
}

function EmptyState({ text }) {
  return <div className="rounded-lg border border-dashed border-gray-300 bg-white px-4 py-8 text-center text-sm text-gray-500">{text}</div>;
}
