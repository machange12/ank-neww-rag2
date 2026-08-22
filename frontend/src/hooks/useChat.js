import { useEffect, useState } from "react";
import { sourcesFromResponse, parseJsonResponse } from "../lib/api";
import { downloadText, safeParseJSON } from "../lib/format";

const DEFAULT_QUERY =
  "What are the notice requirements under Kenya's DPA for data breach disclosure?";

// Research query/answer/sources, conversation session id, and local
// History/Saved. `token` is only watched to reset the conversation session on
// login/sign-out (both go through a token change) — it's not read directly.
export function useChat({ apiFetch, token, authHeaders, setStatus, setActiveNav, setError, setNotice }) {
  const [query, setQuery] = useState(DEFAULT_QUERY);
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState([]);
  const [activeSource, setActiveSource] = useState("rag");
  const [isAsking, setIsAsking] = useState(false);
  const [lastLatency, setLastLatency] = useState(null);
  // Conversation session id, scoped to this browser tab's app session (not
  // persisted to localStorage) — kept across turns so the backend continues
  // the same conversation instead of minting a fresh one on every message.
  const [sessionId, setSessionId] = useState(null);
  const [history, setHistory] = useState(() => safeParseJSON(localStorage.getItem("ank_rag_history"), []));
  const [saved, setSaved] = useState(() => safeParseJSON(localStorage.getItem("ank_rag_saved"), []));

  useEffect(() => {
    setSessionId(null);
  }, [token]);

  async function ask(event) {
    event?.preventDefault();
    if (!query.trim()) return;
    setError("");
    setNotice("");
    setIsAsking(true);
    setStatus("Searching secured documents");
    const started = performance.now();
    try {
      // Send back the session id the server minted on the previous turn so
      // the backend continues the same conversation (and loads prior turns
      // as chat history) instead of starting a fresh session every request.
      const response = await apiFetch("/lawfirm-chat-trigger-006", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ chatInput: query, ...(sessionId ? { sessionId } : {}) }),
      });
      const data = await parseJsonResponse(response, "RAG request failed");
      if (!response.ok) throw new Error(data.error || data.detail || "RAG request failed");
      const output = data.output || "";
      const responseSources = sourcesFromResponse(data.sources);
      const latency = ((performance.now() - started) / 1000).toFixed(1);
      const item = {
        id: crypto.randomUUID(),
        query,
        answer: output,
        sources: responseSources,
        latency,
        createdAt: new Date().toISOString(),
      };
      const nextHistory = [item, ...history].slice(0, 25);
      localStorage.setItem("ank_rag_history", JSON.stringify(nextHistory));
      setHistory(nextHistory);
      setAnswer(output);
      setSources(responseSources);
      setActiveSource(responseSources[0]?.id || "rag");
      setLastLatency(latency);
      if (data.session_id) setSessionId(data.session_id);
      setStatus("Answer ready");
      setActiveNav("Research");
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") {
        setError(err.message);
        setStatus("Request failed");
      }
    } finally {
      setIsAsking(false);
    }
  }

  function restoreHistory(item) {
    setQuery(item.query);
    setAnswer(item.answer);
    setSources(item.sources || []);
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

  return {
    query,
    setQuery,
    answer,
    sources,
    activeSource,
    setActiveSource,
    isAsking,
    lastLatency,
    history,
    ask,
    restoreHistory,
    saveAnswer,
    copyAnswer,
    exportAnswer,
  };
}
