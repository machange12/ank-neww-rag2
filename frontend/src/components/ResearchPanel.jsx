import { Clipboard, Download, RefreshCw, Save, ShieldCheck } from "lucide-react";

export function ResearchPanel({ answer, ask, copyAnswer, error, exportAnswer, isAsking, lastLatency, notice, query, saveAnswer, sources, status }) {
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
