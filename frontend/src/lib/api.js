export const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

export function decodeJwtExpiry(token) {
  try {
    const payload = token.split(".")[1];
    const json = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
    return typeof json.exp === "number" ? json.exp * 1000 : null;
  } catch {
    return null;
  }
}

// The JWT subject is the account's user id (auth.users.id). Used to tell a
// user's own uploads from a colleague's — the backend also enforces this, this
// only decides whether to offer a Share control.
export function decodeJwtSubject(token) {
  try {
    const payload = token.split(".")[1];
    const json = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
    return typeof json.sub === "string" ? json.sub : "";
  } catch {
    return "";
  }
}

export function sourcesFromResponse(rawSources) {
  // Uses the backend's real retrieved-document sources (title/url/section) —
  // never re-derived from the answer text, which previously produced
  // fabricated relevance bars unrelated to actual retrieval.
  if (!Array.isArray(rawSources) || !rawSources.length) {
    return [];
  }
  return rawSources.map((source, index) => ({
    id: source.file_id || source.url || `source-${index}`,
    n: source.n || index + 1, // the [n] the answer cites
    title: source.title || "Unknown document",
    meta: source.section_heading || (source.file_id ? `File: ${source.file_id}` : "Retrieved document"),
    url: source.url || "",
    // Exact location of the retrieved passage: "p. 12", "pp. 12–13", "¶ 4"
    // or "" when the document has no reliable page/paragraph (old ingests,
    // or a plain-text file). Rendered from the backend, never guessed.
    label: source.label || "",
    quote: source.quote || "",
    locations: Array.isArray(source.locations) ? source.locations : [],
    locatorKind: source.locator_kind || "",
    // Set by the backend when a draft is shared and this source is another
    // account's private upload: title/quote/link are withheld.
    restricted: Boolean(source.restricted),
  }));
}

// Parses a fetch Response as JSON, falling back to a {detail} shape when the
// body isn't valid JSON — every endpoint's error envelope is {error} or
// {detail}, so callers can read either uniformly.
export async function parseJsonResponse(response, fallbackMessage) {
  const responseText = await response.text();
  try {
    return JSON.parse(responseText);
  } catch {
    return { detail: responseText || fallbackMessage };
  }
}

// Central fetch wrapper for authenticated API calls: on a 401 (expired or
// invalid token) it calls onUnauthorized (the caller signs out) instead of
// letting every call site fail separately with a raw error.
export async function apiFetch(path, options = {}, onUnauthorized) {
  const response = await fetch(`${API_BASE}${path}`, options);
  if (response.status === 401) {
    onUnauthorized?.();
    throw new Error("UNAUTHORIZED");
  }
  return response;
}
