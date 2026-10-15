export function initials(label) {
  if (!label || label === "Not signed in") return "AN";
  return label
    .split("@")[0]
    .split(/[.\s_-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

export function downloadText(filename, text) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString();
}

export function safeParseJSON(raw, fallback) {
  if (!raw) return fallback;
  try {
    const parsed = JSON.parse(raw);
    return parsed ?? fallback;
  } catch {
    return fallback;
  }
}

const NO_ANSWER_RE = /^\s*I could not find an answer in the firm'?s document repository/i;

export function isNoAnswer(answer) {
  return NO_ANSWER_RE.test(answer || "");
}

export function stripNoAnswerCitations(answer) {
  return (answer || "").replace(/\[\d+\]/g, "").replace(/[ \t]{2,}/g, " ").trim();
}

export function stripMarkdown(answer) {
  if (!answer) return answer;
  return answer
    .replace(/^[ \t]*#{1,6}[ \t]*/gm, "")
    .replace(/^[ \t]*[-*\u2022][ \t]+/gm, "")
    .replace(/(\*\*|__)(.+?)\1/gs, "$2")
    .replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/gs, "$1")
    .replace(/\*/g, "");
}
