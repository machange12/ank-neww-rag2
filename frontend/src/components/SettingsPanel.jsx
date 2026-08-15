import { LogOut } from "lucide-react";
import { Panel } from "./common";

export function SettingsPanel({ compactMode, setCompactMode, signOut }) {
  return (
    <Panel title="Settings" subtitle="Dashboard preferences.">
      <div className="space-y-3">
        <label className="flex items-center justify-between rounded-lg border border-gray-200 bg-white p-3 text-sm">
          Compact display
          <input type="checkbox" checked={compactMode} onChange={(event) => setCompactMode(event.target.checked)} />
        </label>
        {/* Wrapped in an arrow function: onClick={signOut} would pass the
            click SyntheticEvent as signOut's `message` argument, which then
            got rendered as the error banner and crashed the app. */}
        <button onClick={() => signOut()} className="flex items-center gap-2 rounded-lg border border-red-200 bg-white px-3 py-2 text-sm text-red-700 hover:bg-red-50">
          <LogOut size={15} /> Sign out
        </button>
      </div>
    </Panel>
  );
}
