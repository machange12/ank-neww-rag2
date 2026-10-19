import { useState } from "react";
import { parseJsonResponse } from "../lib/api";

// Draft Studio: server-side drafts, per-section grounded generation (SSE),
// manual edits and inline refine. Every request runs under the caller's own
// RLS via apiFetch/authHeaders — the hook owns all network + draft state, so
// the panel stays presentational like the other panels in this app.
export function useDraft({ apiFetch, authHeaders, setStatus, setError, setNotice }) {
  const [drafts, setDrafts] = useState([]);
  const [activeDraft, setActiveDraft] = useState(null);
  const [templates, setTemplates] = useState([]);
  const [isLoadingDrafts, setIsLoadingDrafts] = useState(false);
  const [isLoadingTemplates, setIsLoadingTemplates] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [generatingIndex, setGeneratingIndex] = useState(null);
  const [streamingText, setStreamingText] = useState("");
  const [refiningIndex, setRefiningIndex] = useState(null);
  const [savingIndex, setSavingIndex] = useState(null);
  const [isSavingVersion, setIsSavingVersion] = useState(false);
  const [restoringVersion, setRestoringVersion] = useState(null);
  const [playbooks, setPlaybooks] = useState([]);
  const [checkResult, setCheckResult] = useState(null);
  const [isChecking, setIsChecking] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [isSharing, setIsSharing] = useState(false);

  // Patch one section in place, matched by its stable order_index.
  function patchSection(orderIndex, patch) {
    setActiveDraft((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        sections: (prev.sections || []).map((section) =>
          Number(section.order_index) === Number(orderIndex) ? { ...section, ...patch } : section
        ),
      };
    });
  }

  async function loadDrafts() {
    setIsLoadingDrafts(true);
    try {
      const response = await apiFetch("/drafts", { headers: authHeaders() });
      const data = await parseJsonResponse(response, "Could not load drafts");
      if (!response.ok) throw new Error(data.error || data.detail || "Could not load drafts");
      setDrafts(data.drafts || []);
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") setError(err.message);
    } finally {
      setIsLoadingDrafts(false);
    }
  }

  async function loadTemplates(docType = "") {
    setIsLoadingTemplates(true);
    try {
      const query = docType ? `?doc_type=${encodeURIComponent(docType)}` : "";
      const response = await apiFetch(`/draft/templates${query}`, { headers: authHeaders() });
      const data = await parseJsonResponse(response, "Could not load templates");
      if (!response.ok) throw new Error(data.error || data.detail || "Could not load templates");
      setTemplates(data.templates || []);
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") setError(err.message);
    } finally {
      setIsLoadingTemplates(false);
    }
  }

  async function createDraft(payload) {
    setError("");
    setNotice("");
    setIsCreating(true);
    setStatus("Proposing section outline");
    try {
      const response = await apiFetch("/drafts", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(payload),
      });
      const data = await parseJsonResponse(response, "Could not create draft");
      if (!response.ok) throw new Error(data.error || data.detail || "Could not create draft");
      setActiveDraft(data.draft);
      setCheckResult(null);
      loadPlaybooks(data.draft?.doc_type || "");
      setNotice(`Draft created with ${(data.draft?.sections || []).length} sections.`);
      setStatus("Draft ready");
      await loadDrafts();
      return data.draft;
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") {
        setError(err.message);
        setStatus("Draft creation failed");
      }
      return null;
    } finally {
      setIsCreating(false);
    }
  }

  async function openDraft(draftId) {
    setError("");
    setStatus("Loading draft");
    try {
      const response = await apiFetch(`/drafts/${draftId}`, { headers: authHeaders() });
      const data = await parseJsonResponse(response, "Could not load draft");
      if (!response.ok) throw new Error(data.error || data.detail || "Could not load draft");
      setActiveDraft(data.draft);
      setCheckResult(data.draft?.last_check || null);
      loadPlaybooks(data.draft?.doc_type || "");
      setStatus("Draft ready");
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") setError(err.message);
    }
  }

  function closeDraft() {
    setActiveDraft(null);
    setStreamingText("");
    setCheckResult(null);
    loadDrafts();
  }

  async function loadPlaybooks(docType = "") {
    try {
      const query = docType ? `?doc_type=${encodeURIComponent(docType)}` : "";
      const response = await apiFetch(`/draft/playbooks${query}`, { headers: authHeaders() });
      const data = await parseJsonResponse(response, "Could not load playbooks");
      if (!response.ok) throw new Error(data.error || data.detail || "Could not load playbooks");
      setPlaybooks(data.playbooks || []);
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") setError(err.message);
    }
  }

  async function runCheck(playbookId = "") {
    if (!activeDraft) return;
    setError("");
    setNotice("");
    setIsChecking(true);
    setStatus("Checking draft against playbook");
    try {
      const response = await apiFetch(`/drafts/${activeDraft.id}/check`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(playbookId ? { playbook_id: playbookId } : {}),
      });
      const data = await parseJsonResponse(response, "Playbook check failed");
      if (!response.ok) throw new Error(data.error || data.detail || "Playbook check failed");
      setCheckResult(data);
      setNotice(
        data.message ||
          `Playbook check: ${data.summary?.breach ?? 0} breach(es), ${data.summary?.unclear ?? 0} unclear.`
      );
      setStatus("Playbook check complete");
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") {
        setError(err.message);
        setStatus("Playbook check failed");
      }
    } finally {
      setIsChecking(false);
    }
  }

  async function generateSection(orderIndex) {
    if (!activeDraft) return;
    setError("");
    setNotice("");
    setGeneratingIndex(orderIndex);
    setStreamingText("");
    setStatus("Drafting section (grounded)");
    try {
      const response = await apiFetch(
        `/drafts/${activeDraft.id}/sections/${orderIndex}/generate`,
        { method: "POST", headers: authHeaders() }
      );
      if (!response.ok) {
        const data = await parseJsonResponse(response, "Section generation failed");
        throw new Error(data.error || data.detail || "Section generation failed");
      }
      // Server-Sent Events: token events build the section as it is written,
      // then a single result event carries the citation-cleaned text + sources
      // that were already persisted server-side.
      let buffer = "";
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pending = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true });
        const events = pending.split("\n\n");
        pending = events.pop();
        for (const raw of events) {
          if (!raw.startsWith("data: ") || raw === "data: [DONE]") continue;
          const event = JSON.parse(raw.slice(6));
          if (event.type === "token") {
            buffer += event.text;
            setStreamingText(buffer);
          } else if (event.type === "result") {
            buffer = event.content || "";
            setStreamingText("");
            patchSection(orderIndex, {
              content: event.content || "",
              sources: event.sources || [],
              status: event.status || "generated",
            });
          }
        }
      }
      setStatus("Section ready");
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") {
        setError(err.message);
        setStatus("Section generation failed");
      }
    } finally {
      setGeneratingIndex(null);
      setStreamingText("");
    }
  }

  async function saveSection(orderIndex, patch) {
    if (!activeDraft) return;
    setError("");
    setNotice("");
    setSavingIndex(orderIndex);
    try {
      const response = await apiFetch(`/drafts/${activeDraft.id}/sections/${orderIndex}`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(patch),
      });
      const data = await parseJsonResponse(response, "Could not save section");
      if (!response.ok) throw new Error(data.error || data.detail || "Could not save section");
      if (data.section) patchSection(orderIndex, data.section);
      setNotice("Section saved.");
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") setError(err.message);
    } finally {
      setSavingIndex(null);
    }
  }

  async function refineSection(orderIndex, instruction) {
    if (!activeDraft || !instruction.trim()) return;
    setError("");
    setNotice("");
    setRefiningIndex(orderIndex);
    setStatus("Refining section");
    try {
      const response = await apiFetch(`/drafts/${activeDraft.id}/sections/${orderIndex}/refine`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ instruction }),
      });
      const data = await parseJsonResponse(response, "Could not refine section");
      if (!response.ok) throw new Error(data.error || data.detail || "Could not refine section");
      if (data.section) patchSection(orderIndex, data.section);
      setStatus("Section refined");
      setNotice("Section refined by instruction.");
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") {
        setError(err.message);
        setStatus("Refine failed");
      }
    } finally {
      setRefiningIndex(null);
    }
  }

  async function saveVersion(label = "") {
    if (!activeDraft) return;
    setError("");
    setNotice("");
    setIsSavingVersion(true);
    setStatus("Saving version");
    try {
      const response = await apiFetch(`/drafts/${activeDraft.id}/versions`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ label }),
      });
      const data = await parseJsonResponse(response, "Could not save version");
      if (!response.ok) throw new Error(data.error || data.detail || "Could not save version");
      if (data.draft) setActiveDraft(data.draft);
      setNotice(`Saved version ${data.version?.version ?? ""}.`);
      setStatus("Version saved");
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") {
        setError(err.message);
        setStatus("Save version failed");
      }
    } finally {
      setIsSavingVersion(false);
    }
  }

  async function restoreVersion(version) {
    if (!activeDraft) return;
    setError("");
    setNotice("");
    setRestoringVersion(version);
    setStatus("Restoring version");
    try {
      const response = await apiFetch(`/drafts/${activeDraft.id}/versions/${version}/restore`, {
        method: "POST",
        headers: authHeaders(),
      });
      const data = await parseJsonResponse(response, "Could not restore version");
      if (!response.ok) throw new Error(data.error || data.detail || "Could not restore version");
      if (data.draft) setActiveDraft(data.draft);
      setNotice(`Restored version ${version}.`);
      setStatus("Version restored");
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") {
        setError(err.message);
        setStatus("Restore failed");
      }
    } finally {
      setRestoringVersion(null);
    }
  }

  async function shareDraft(matterId) {
    if (!activeDraft || !matterId.trim()) return;
    setError("");
    setNotice("");
    setIsSharing(true);
    setStatus("Sharing draft");
    try {
      const response = await apiFetch(`/drafts/${activeDraft.id}/share`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ matter_id: matterId.trim() }),
      });
      const data = await parseJsonResponse(response, "Could not share draft");
      if (!response.ok) throw new Error(data.error || data.detail || "Could not share draft");
      if (data.draft) setActiveDraft(data.draft);
      setNotice(`Shared read-only with matter ${data.draft?.matter_id || matterId.trim()}.`);
      setStatus("Draft shared");
      await loadDrafts();
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") {
        setError(err.message);
        setStatus("Share failed");
      }
    } finally {
      setIsSharing(false);
    }
  }

  async function unshareDraft() {
    if (!activeDraft) return;
    setError("");
    setNotice("");
    setIsSharing(true);
    setStatus("Making draft private");
    try {
      const response = await apiFetch(`/drafts/${activeDraft.id}/unshare`, {
        method: "POST",
        headers: authHeaders(),
      });
      const data = await parseJsonResponse(response, "Could not unshare draft");
      if (!response.ok) throw new Error(data.error || data.detail || "Could not unshare draft");
      if (data.draft) setActiveDraft(data.draft);
      setNotice("Draft is private again.");
      setStatus("Draft is private");
      await loadDrafts();
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") {
        setError(err.message);
        setStatus("Unshare failed");
      }
    } finally {
      setIsSharing(false);
    }
  }

  async function exportDraft(fmt = "docx") {
    if (!activeDraft) return;
    setError("");
    setNotice("");
    setIsExporting(true);
    setStatus("Exporting draft");
    try {
      const response = await apiFetch(`/drafts/${activeDraft.id}/export?fmt=${fmt}`, {
        method: "POST",
        headers: authHeaders(),
      });
      if (!response.ok) {
        const data = await parseJsonResponse(response, "Export failed");
        throw new Error(data.error || data.detail || "Export failed");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${(activeDraft.title || "draft").replace(/[^\w.-]+/g, "-")}.${fmt}`;
      link.click();
      URL.revokeObjectURL(url);
      setNotice(`Draft exported as ${fmt.toUpperCase()}.`);
      setStatus("Export ready");
    } catch (err) {
      if (err.message !== "UNAUTHORIZED") {
        setError(err.message);
        setStatus("Export failed");
      }
    } finally {
      setIsExporting(false);
    }
  }

  return {
    drafts,
    activeDraft,
    templates,
    isLoadingDrafts,
    isLoadingTemplates,
    isCreating,
    generatingIndex,
    streamingText,
    refiningIndex,
    savingIndex,
    isSavingVersion,
    restoringVersion,
    playbooks,
    checkResult,
    isChecking,
    isExporting,
    isSharing,
    loadDrafts,
    loadTemplates,
    createDraft,
    openDraft,
    closeDraft,
    generateSection,
    saveSection,
    refineSection,
    saveVersion,
    restoreVersion,
    loadPlaybooks,
    runCheck,
    exportDraft,
    shareDraft,
    unshareDraft,
  };
}
