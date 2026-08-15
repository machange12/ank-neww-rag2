import { Panel, EmptyState } from "./common";

export function HistoryPanel({ history, restoreHistory }) {
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
