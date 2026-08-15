import { initials } from "../lib/format";
import { Panel } from "./common";

export function TeamPanel({ userLabel }) {
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
