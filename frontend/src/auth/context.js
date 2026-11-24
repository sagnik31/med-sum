// src/auth/context.js
import { createContext, useContext } from "react";

// { status, user, login, logout, refresh }. status is "loading", "in", "out",
// "leaving" (a logout was sent) or "leave_failed" (it failed: the session
// may still be live).
export const AuthContext = createContext(null);

export function useAuth() {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth must be used inside <AuthProvider>");
  return auth;
}
