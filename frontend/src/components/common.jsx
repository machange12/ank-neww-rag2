export function Panel({ title, subtitle, children }) {
  return (
    <div>
      <h1 className="font-serif text-lg font-semibold text-gray-900">{title}</h1>
      <p className="mb-4 mt-1 text-xs text-gray-500">{subtitle}</p>
      {children}
    </div>
  );
}

export function EmptyState({ text }) {
  return <div className="rounded-lg border border-dashed border-gray-300 bg-white px-4 py-8 text-center text-sm text-gray-500">{text}</div>;
}
