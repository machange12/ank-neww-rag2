import { FileText, FolderKanban, History, Search, Settings, Users } from "lucide-react";

export const NAV = [
  { icon: Search, label: "Research" },
  { icon: History, label: "History" },
  { icon: FileText, label: "Documents" },
  { icon: FolderKanban, label: "Matters" },
  { icon: Users, label: "Team" },
  { icon: Settings, label: "Settings" },
];

export const SAMPLE_MATTERS = [
  { id: "M-2024-118", name: "Data breach advisory", access: "Partner" },
  { id: "M-2025-001", name: "Employment privacy review", access: "Associate" },
  { id: "M-2024-001", name: "Commercial litigation", access: "Matter team" },
];
