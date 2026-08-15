import { useState } from "react";

// Single shared error/notice banner. Several hooks (auth, chat, documents)
// report into the same banner, matching the app's one-message-area design —
// only the Research panel and the login screen render it.
export function useBanner() {
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  return { error, setError, notice, setNotice };
}
