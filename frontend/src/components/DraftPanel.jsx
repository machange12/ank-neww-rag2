import { useEffect, useState } from "react";
import { ArrowLeft, Download, Eye, FileSignature, Loader2, PenLine, RefreshCw, Save, ShieldCheck, Sparkles, Users, Wand2 } from "lucide-react";
import { sourcesFromResponse } from "../lib/api";
import { formatDate } from "../lib/format";
import { Panel, EmptyState } from "./common";

const DOC_TYPES = [
  { value: "nda", label: "Non-disclosure agreement" },
  { value: "lease", label: "Lease" },
  { value: "engagement_letter", label: "Engagement letter" },
  { value: "memo", label: "Memorandum" },
  { value: "other", label: "Other" },
];

const STATUS_STYLES = {
  empty: "bg-gray-100 text-gray-500",
  generated: "bg-blue-50 text-blue-700",
  edited: "bg-green-50 text-green-700",
  needs_source: "bg-amber-50 text-amber-700",
};

function StatusBadge({ status }) {
  const value = status || "empty";
  const label = value === "needs_source" ? "needs source" : value;
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${STATUS_STYLES[value] || STATUS_STYLES.empty}`}>
      {label}
    </span>
  );
}

function SectionCard({ section, index, orderIndex, isGenerating, streamingText, isRefining, isSaving, onGenerate, onSave, onRefine, readOnly }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(section.content || "");
  const [instruction, setInstruction] = useState("");

  useEffect(() => {
    setText(section.content || "");
  }, [section.content]);

  const issues = section.status === "needs_source";
  const sources = sourcesFromResponse(section.sources || []);
  const shown = isGenerating && streamingText ? streamingText : section.content || "";
  const hasContent = Boolean((section.content || "").trim());

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-semibold text-gray-400">{index + 1}.</span>
            <h3 className="truncate text-sm font-semibold text-gray-900">{section.heading || "Untitled section"}</h3>
            {section.required && <span className="text-[10px] text-gray-300">required</span>}
          </div>
          <div className="mt-1 flex items-center gap-2">
            <StatusBadge status={section.status} />
            {section.clause_type && <span className="text-[10px] uppercase tracking-wide text-gray-400">{section.clause_type}</span>}
          </div>
        </div>
        <button
          onClick={() => onGenerate(orderIndex)}
          disabled={isGenerating || isRefining || readOnly}
          className="flex flex-shrink-0 items-center gap-1.5 rounded-md border border-blue-200 px-2.5 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isGenerating ? <Loader2 className="animate-spin" size={13} /> : <Sparkles size={13} />}
          {hasContent ? "Regenerate" : "Generate"}
        </button>
      </div>

      {section.guidance && !hasContent && !isGenerating && (
        <p className="mb-2 text-xs italic text-gray-400">{section.guidance}</p>
      )}

      {editing ? (
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={Math.min(18, Math.max(6, Math.ceil((text.length || 1) / 80)))}
          className="w-full rounded-md border border-gray-300 px-2.5 py-2 text-[13px] leading-relaxed text-gray-800 focus:border-blue-400 focus:outline-none"
        />
      ) : (
        <div className={`min-h-12 whitespace-pre-wrap text-[13px] leading-relaxed ${issues ? "text-amber-700" : "text-gray-800"}`}>
          {shown || (
            <span className="text-xs italic text-gray-400">
              {isGenerating ? "Drafting from grounded sources..." : "Not generated yet."}
            </span>
          )}
          {isGenerating && streamingText && <span className="ml-0.5 animate-pulse text-blue-500">▍</span>}
        </div>
      )}

      {sources.length > 0 && (
        <div className="mt-2 border-t border-gray-100 pt-2">
          <div className="mb-1 text-[10px] uppercase tracking-widest text-gray-400">Sources</div>
          <ul className="space-y-0.5">
            {sources.map((source) => (
              <li key={source.id} className="flex items-center gap-1.5 text-[11px] text-gray-500">
                <span className="text-gray-400">[{source.n}]</span>
                <span className="truncate" title={source.title}>{source.title}</span>
                {source.meta && <span className="truncate text-gray-300">{source.meta}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {!readOnly && (
        <>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {editing ? (
              <>
                <button
                  onClick={() => {
                    setEditing(false);
                    onSave(orderIndex, { content: text });
                  }}
                  disabled={isSaving || text === (section.content || "")}
                  className="action-button"
                >
                  {isSaving ? <Loader2 className="animate-spin" size={13} /> : <Save size={13} />}
                  Save changes
                </button>
                <button
                  onClick={() => {
                    setEditing(false);
                    setText(section.content || "");
                  }}
                  className="action-button"
                >
                  Cancel
                </button>
              </>
            ) : (
              <button onClick={() => setEditing(true)} className="action-button">
                <PenLine size={13} /> Edit
              </button>
            )}
          </div>

          <div className="mt-2 flex items-center gap-2">
            <input
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
              placeholder="Refine: e.g. tighten the language, keep the citations"
              className="flex-1 rounded-md border border-gray-300 px-2 py-1.5 text-xs focus:border-blue-400 focus:outline-none"
            />
            <button
              onClick={() => {
                onRefine(orderIndex, instruction);
                setInstruction("");
              }}
              disabled={isRefining || !instruction.trim() || !hasContent}
              className="flex items-center gap-1.5 rounded-md border border-gray-300 px-2.5 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isRefining ? <Loader2 className="animate-spin" size={13} /> : <Wand2 size={13} />}
              Refine
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function VersionHistory({ versions, currentVersion, isSavingVersion, restoringVersion, onSave, onRestore, readOnly }) {
  return (
    <div className="mt-4 rounded-lg border border-gray-200 bg-white p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-500">Version history</h2>
        {!readOnly && (
          <button onClick={onSave} disabled={isSavingVersion} className="action-button">
            {isSavingVersion ? <Loader2 className="animate-spin" size={13} /> : <Save size={13} />}
            Save version
          </button>
        )}
      </div>
      {!versions || versions.length === 0 ? (
        <p className="text-xs text-gray-400">No versions saved yet.</p>
      ) : (
        <ul className="space-y-1">
          {versions.map((version) => (
            <li key={version.id || version.version} className="flex items-center justify-between gap-2 text-xs">
              <div className="min-w-0 truncate">
                <span className="font-medium text-gray-800">v{version.version}</span>
                {version.version === currentVersion && (
                  <span className="ml-2 rounded-full bg-green-50 px-2 py-0.5 text-[10px] font-medium text-green-700">current</span>
                )}
                {version.label && <span className="ml-2 text-gray-400">{version.label}</span>}
                <span className="ml-2 text-gray-400">{formatDate(version.created_at)}</span>
              </div>
              <button
                onClick={() => onRestore(version.version)}
                disabled={restoringVersion != null || version.version === currentVersion}
                className="flex flex-shrink-0 items-center gap-1 rounded-md border border-gray-300 px-2 py-1 text-[11px] font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                hidden={readOnly}
              >
                {restoringVersion === version.version ? <Loader2 className="animate-spin" size={11} /> : null}
                Restore
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const FLAG_STATUS_STYLES = {
  ok: "bg-green-50 text-green-700",
  breach: "bg-red-50 text-red-700",
  unclear: "bg-amber-50 text-amber-700",
};

function PlaybookCheck({ playbooks, selectedPlaybookId, setSelectedPlaybookId, checkResult, isChecking, sections, onRun, readOnly }) {
  const flags = checkResult?.flags || [];
  const summary = checkResult?.summary;
  const label = (orderIndex) => {
    if (orderIndex == null) return "General";
    const section = (sections || []).find((s) => Number(s.order_index) === Number(orderIndex));
    return section ? `Section ${Number(orderIndex) + 1}: ${section.heading}` : `Section ${orderIndex}`;
  };
  const grouped = { high: [], medium: [], low: [] };
  for (const flag of flags) {
    (grouped[flag.severity] || grouped.medium).push(flag);
  }

  return (
    <div className="mt-4 rounded-lg border border-gray-200 bg-white p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-500">Playbook check</h2>
        {!readOnly && (
          <div className="flex items-center gap-2">
            <select
              value={selectedPlaybookId}
              onChange={(event) => setSelectedPlaybookId(event.target.value)}
              className="rounded-md border border-gray-300 px-2 py-1.5 text-xs focus:border-blue-400 focus:outline-none"
            >
              <option value="">Auto (by document type)</option>
              {playbooks.map((playbook) => (
                <option key={playbook.id} value={playbook.id}>{playbook.name}</option>
              ))}
            </select>
            <button
              onClick={() => onRun(selectedPlaybookId)}
              disabled={isChecking}
              className="flex items-center gap-1.5 rounded-md border border-blue-200 px-2.5 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isChecking ? <Loader2 className="animate-spin" size={13} /> : <ShieldCheck size={13} />}
              Run check
            </button>
          </div>
        )}
      </div>

      {!checkResult ? (
        <p className="text-xs text-gray-400">No playbook check yet.</p>
      ) : flags.length === 0 ? (
        <p className="text-xs text-gray-500">{checkResult.message || "No rules to check."}</p>
      ) : (
        <>
          <div className="mb-2 text-xs text-gray-500">
            {checkResult.playbook?.name && (
              <span className="font-medium text-gray-700">{checkResult.playbook.name} — </span>
            )}
            {summary?.breach ?? 0} breach / {summary?.unclear ?? 0} unclear / {summary?.ok ?? 0} ok
          </div>
          {["high", "medium", "low"].map((severity) =>
            grouped[severity].length ? (
              <div key={severity} className="mb-3">
                <div className="mb-1 text-[10px] uppercase tracking-widest text-gray-400">{severity} severity</div>
                <ul className="space-y-1">
                  {grouped[severity].map((flag, index) => (
                    <li key={`${flag.rule_id}-${index}`} className="rounded-md border border-gray-100 p-2">
                      <div className="flex items-start justify-between gap-2">
                        <span className="text-xs text-gray-800">{flag.rule_text}</span>
                        <span className={`flex-shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium uppercase ${FLAG_STATUS_STYLES[flag.status] || FLAG_STATUS_STYLES.unclear}`}>
                          {flag.status}
                        </span>
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-gray-400">
                        <span>{flag.position}</span>
                        <span>·</span>
                        <span>{label(flag.order_index)}</span>
                      </div>
                      {flag.note && <div className="mt-1 text-[11px] text-gray-500">{flag.note}</div>}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null
          )}
        </>
      )}
    </div>
  );
}

export function DraftPanel({
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
  runCheck,
  exportDraft,
  shareDraft,
  unshareDraft,
}) {
  const [title, setTitle] = useState("");
  const [docType, setDocType] = useState("nda");
  const [matterId, setMatterId] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [brief, setBrief] = useState("");
  const [selectedPlaybookId, setSelectedPlaybookId] = useState("");
  const [shareMatterId, setShareMatterId] = useState("");

  useEffect(() => {
    loadDrafts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadTemplates(docType);
    setTemplateId("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docType]);

  async function submit(event) {
    event.preventDefault();
    if (!title.trim() || isCreating) return;
    const created = await createDraft({
      title: title.trim(),
      doc_type: docType,
      matter_id: matterId.trim(),
      template_id: templateId || null,
      brief: brief.trim() ? { instructions: brief.trim() } : {},
    });
    if (created) {
      setTitle("");
      setMatterId("");
      setBrief("");
      setTemplateId("");
    }
  }

  if (!activeDraft) {
    return (
      <Panel title="Drafting" subtitle="Grounded section-by-section drafting over the firm's authorized corpus and curated clause bank.">
        <form onSubmit={submit} className="mb-5 rounded-lg border border-gray-200 bg-white p-3">
          <div className="grid gap-3 md:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-gray-500">Title</span>
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Acme / Beta mutual NDA"
                className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-xs focus:border-blue-400 focus:outline-none"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-gray-500">Document type</span>
              <select
                value={docType}
                onChange={(event) => setDocType(event.target.value)}
                className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-xs focus:border-blue-400 focus:outline-none"
              >
                {DOC_TYPES.map((type) => (
                  <option key={type.value} value={type.value}>{type.label}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-gray-500">Matter</span>
              <input
                value={matterId}
                onChange={(event) => setMatterId(event.target.value)}
                placeholder="M-2024-118"
                className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-xs focus:border-blue-400 focus:outline-none"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-gray-500">
                Template {isLoadingTemplates ? "(loading)" : "(optional)"}
              </span>
              <select
                value={templateId}
                onChange={(event) => setTemplateId(event.target.value)}
                className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-xs focus:border-blue-400 focus:outline-none"
              >
                <option value="">No template (propose outline)</option>
                {templates.map((template) => (
                  <option key={template.id} value={template.id}>{template.title}</option>
                ))}
              </select>
            </label>
          </div>
          <label className="mt-3 block">
            <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-gray-500">
              Brief / instructions
            </span>
            <textarea
              value={brief}
              onChange={(event) => setBrief(event.target.value)}
              rows={3}
              placeholder="Parties, commercial deal, key terms and any clauses you want emphasised."
              className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-xs focus:border-blue-400 focus:outline-none"
            />
          </label>
          <button
            disabled={!title.trim() || isCreating}
            className="mt-3 flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300"
          >
            {isCreating ? <Loader2 className="animate-spin" size={13} /> : <FileSignature size={13} />}
            {isCreating ? "Proposing outline" : "Create draft"}
          </button>
        </form>

        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-500">Your drafts</h2>
          <button onClick={loadDrafts} disabled={isLoadingDrafts} className="action-button">
            {isLoadingDrafts ? <Loader2 className="animate-spin" size={13} /> : <RefreshCw size={13} />}
            Refresh
          </button>
        </div>
        {drafts.length === 0 ? (
          <EmptyState text={isLoadingDrafts ? "Loading drafts..." : "No drafts yet. Create one from a brief above."} />
        ) : (
          <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
            {drafts.map((draft) => (
              <button
                key={draft.id}
                onClick={() => openDraft(draft.id)}
                className="flex w-full items-center justify-between gap-3 border-b border-gray-100 p-3 text-left last:border-b-0 hover:bg-gray-50"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-gray-900">{draft.title}</div>
                  <div className="mt-0.5 flex items-center gap-1.5 text-xs text-gray-500">
                    <span>{draft.doc_type} - {draft.status} - v{draft.current_version} - {formatDate(draft.updated_at)}</span>
                    {draft.visibility === "matter" && (
                      <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-medium text-blue-700">
                        {draft.is_owner === false ? "shared with you" : `shared · ${draft.matter_id}`}
                      </span>
                    )}
                  </div>
                </div>
                <span className="flex-shrink-0 text-xs font-medium text-blue-600">Open</span>
              </button>
            ))}
          </div>
        )}
      </Panel>
    );
  }

  const sections = activeDraft.sections || [];
  const readOnly = activeDraft.is_owner === false;

  return (
    <Panel title={activeDraft.title} subtitle={`${activeDraft.doc_type} - ${activeDraft.status} - v${activeDraft.current_version}`}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <button onClick={closeDraft} className="action-button">
          <ArrowLeft size={13} /> All drafts
        </button>
        {activeDraft.matter_id && <span className="text-xs text-gray-500">Matter {activeDraft.matter_id}</span>}
        <div className="flex flex-1 items-center justify-end gap-2">
          {isExporting && <Loader2 className="animate-spin text-gray-400" size={13} />}
          {["docx", "md", "txt"].map((format) => (
            <button
              key={format}
              onClick={() => exportDraft(format)}
              disabled={isExporting}
              className="action-button"
            >
              <Download size={13} /> {format.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {readOnly ? (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800">
          <Eye size={13} /> Shared with you via matter {activeDraft.matter_id} — read-only.
        </div>
      ) : (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-gray-200 bg-white p-2">
          <Users size={13} className="text-gray-400" />
          {activeDraft.visibility === "matter" ? (
            <>
              <span className="text-xs text-gray-600">
                Shared read-only with matter <span className="font-medium">{activeDraft.matter_id}</span>
              </span>
              <button onClick={unshareDraft} disabled={isSharing} className="action-button ml-auto">
                {isSharing ? <Loader2 className="animate-spin" size={13} /> : null} Make private
              </button>
            </>
          ) : (
            <>
              <span className="text-xs text-gray-500">Private. Share read-only with a matter team:</span>
              <input
                value={shareMatterId}
                onChange={(event) => setShareMatterId(event.target.value)}
                placeholder="M-2024-118"
                className="rounded-md border border-gray-300 px-2 py-1 text-xs focus:border-blue-400 focus:outline-none"
              />
              <button
                onClick={() => shareDraft(shareMatterId)}
                disabled={isSharing || !shareMatterId.trim()}
                className="action-button"
              >
                {isSharing ? <Loader2 className="animate-spin" size={13} /> : <Users size={13} />} Share
              </button>
            </>
          )}
        </div>
      )}

      {sections.length === 0 ? (
        <EmptyState text="This draft has no sections." />
      ) : (
        <div className="space-y-3">
          {sections.map((section, index) => {
            const orderIndex = section.order_index ?? index;
            return (
              <SectionCard
                key={orderIndex}
                section={section}
                index={index}
                orderIndex={orderIndex}
                isGenerating={generatingIndex === orderIndex}
                streamingText={generatingIndex === orderIndex ? streamingText : ""}
                isRefining={refiningIndex === orderIndex}
                isSaving={savingIndex === orderIndex}
                onGenerate={generateSection}
                onSave={saveSection}
                onRefine={refineSection}
                readOnly={readOnly}
              />
            );
          })}
        </div>
      )}

      <PlaybookCheck
        playbooks={playbooks}
        selectedPlaybookId={selectedPlaybookId}
        setSelectedPlaybookId={setSelectedPlaybookId}
        checkResult={checkResult}
        isChecking={isChecking}
        sections={sections}
        onRun={runCheck}
        readOnly={readOnly}
      />

      <VersionHistory
        versions={activeDraft.versions || []}
        currentVersion={activeDraft.current_version}
        isSavingVersion={isSavingVersion}
        restoringVersion={restoringVersion}
        onSave={saveVersion}
        onRestore={restoreVersion}
        readOnly={readOnly}
      />
    </Panel>
  );
}
