import { useEffect, useState } from "react";
import { API_BASE, apiFetch as apiFetchRaw, decodeJwtExpiry, parseJsonResponse } from "../lib/api";

// Login/session/token state, shared apiFetch wrapper. `setStatus`/`setActiveNav`
// are injected so login/sign-out can drive the page-level status line and nav
// tab without this hook owning UI state that other features also touch.
export function useAuth({ setStatus, setActiveNav, setError, setNotice }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState(() => localStorage.getItem("ank_rag_token") || "");
  const [userLabel, setUserLabel] = useState(() => localStorage.getItem("ank_rag_user") || "Not signed in");
  const [accessLevel, setAccessLevel] = useState(() => {
    const stored = localStorage.getItem("ank_rag_access_level");
    return stored ? Number(stored) : null;
  });
  const [isLoggingIn, setIsLoggingIn] = useState(false);

  function signOut(message = "") {
    localStorage.removeItem("ank_rag_token");
    localStorage.removeItem("ank_rag_user");
    localStorage.removeItem("ank_rag_access_level");
    setToken("");
    setUserLabel("Not signed in");
    setAccessLevel(null);
    setStatus("Signed out");
    setError(message);
    setNotice("");
  }

  // Proactively sign out once the JWT expires, instead of leaving a dead
  // token in localStorage that only surfaces as a failed request later.
  useEffect(() => {
    if (!token) return undefined;
    const expiresAt = decodeJwtExpiry(token);
    if (!expiresAt) return undefined;
    const msRemaining = expiresAt - Date.now();
    if (msRemaining <= 0) {
      signOut("Your session expired. Please sign in again.");
      return undefined;
    }
    const timer = setTimeout(() => signOut("Your session expired. Please sign in again."), msRemaining);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  function authHeaders(extra = {}) {
    return {
      ...extra,
      Authorization: `Bearer ${token}`,
    };
  }

  async function apiFetch(path, options = {}) {
    return apiFetchRaw(path, options, () =>
      signOut("Your session has expired or is no longer valid. Please sign in again.")
    );
  }

  async function login(event) {
    event.preventDefault();
    setError("");
    setNotice("");
    setIsLoggingIn(true);
    try {
      const response = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = await parseJsonResponse(response, "Login failed");
      if (!response.ok) throw new Error(data.error || data.detail || "Login failed");
      setToken(data.access_token);
      localStorage.setItem("ank_rag_token", data.access_token);
      setUserLabel(email);
      localStorage.setItem("ank_rag_user", email);
      setAccessLevel(data.access_level ?? null);
      if (data.access_level != null) {
        localStorage.setItem("ank_rag_access_level", String(data.access_level));
      }
      setStatus("Signed in");
      setActiveNav("Research");
    } catch (err) {
      const message = err.message || "Login failed";
      const cleaned = message
        .replace(/login_failed:/i, "")
        .replace(/login failed/i, "")
        .replace(/invalid login credentials/i, "Invalid email or password")
        .replace(/invalid credentials/i, "Invalid email or password")
        .trim();
      setError(cleaned || "Login failed");
    } finally {
      setIsLoggingIn(false);
    }
  }

  return {
    email,
    setEmail,
    password,
    setPassword,
    token,
    userLabel,
    accessLevel,
    isLoggingIn,
    login,
    signOut,
    apiFetch,
    authHeaders,
  };
}
