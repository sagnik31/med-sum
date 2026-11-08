// src/auth/AuthProvider.jsx
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { fetchMe, login as apiLogin, logout as apiLogout, onUnauthorized } from "../api/client.js";
import { AuthContext } from "./context.js";

// Holds the logged-in account. The session itself is an HttpOnly cookie,
// so the only way to know whether one exists is to ask the API.
function AuthProvider({ children }) {
  const [state, setState] = useState({ status: "loading", user: null });

  const refresh = useCallback(async () => {
    try {
      const user = await fetchMe();
      setState(user ? { status: "in", user } : { status: "out", user: null });
    } catch {
      // The API is unreachable; treat it as logged out so the login page
      // can show the error when the user tries.
      setState({ status: "out", user: null });
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Any 401 from the API means the session is gone (expired, logged out
  // elsewhere, account disabled): go back to the login page.
  useEffect(
    () => onUnauthorized(() => setState({ status: "out", user: null })),
    []
  );

  const login = useCallback(async (email, password) => {
    const user = await apiLogin(email, password);
    setState({ status: "in", user });
    return user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      setState({ status: "out", user: null });
    }
  }, []);

  const value = useMemo(
    () => ({ ...state, login, logout, refresh }),
    [state, login, logout, refresh]
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export default AuthProvider;
