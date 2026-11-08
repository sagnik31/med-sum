// src/auth/context.js
import { createContext, useContext } from "react";

// { status: "loading" | "in" | "out", user, login, logout, refresh }
export const AuthContext = createContext(null);

export function useAuth() {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth must be used inside <AuthProvider>");
  return auth;
}
