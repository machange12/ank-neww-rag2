import { Panel } from "./common";

export function SimpleList({ title, rows }) {
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
