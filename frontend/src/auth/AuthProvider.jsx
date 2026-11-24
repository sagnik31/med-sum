// src/auth/AuthProvider.jsx
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchMe, login as apiLogin, logout as apiLogout, onUnauthorized } from "../api/client.js";
import { AuthContext } from "./context.js";

const SIGNED_OUT = { status: "out", user: null };

// Tells the browser's other tabs, which share the session cookie, that the
// login changed: "in" or "out".
const CHANNEL = "medsum-auth";

// The same account again keeps the same object, so nothing reloads.
function signedIn(prev, user) {
  if (prev.status === "in" && JSON.stringify(prev.user) === JSON.stringify(user)) return prev;
  return { status: "in", user };
}

// Holds the logged-in account. The session itself is an HttpOnly cookie,
// so the only way to know whether one exists is to ask the API.
function AuthProvider({ children }) {
  const [state, setState] = useState({ status: "loading", user: null });
  // Counts logins and logouts: a check sent before one says nothing after it.
  const changes = useRef(0);
  const channel = useRef(null);

  // `background`: a check the app makes on its own, which is not activity.
  // `keepOnError`: an unreachable API leaves things as they are.
  const refresh = useCallback(async ({ background = false, keepOnError = false } = {}) => {
    const asked = changes.current;
    let user = null;
    try {
      user = await fetchMe({ background });
    } catch {
      // The API is unreachable; treat it as logged out so the login page
      // can show the error when the user tries.
      if (keepOnError) return;
    }
    if (asked !== changes.current) return;
    setState((prev) => {
      if (!user) return SIGNED_OUT;
      // A logout that is not finished is not undone by its session still
      // being alive.
      if (prev.status === "leaving" || prev.status === "leave_failed") return prev;
      return signedIn(prev, user);
    });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const signOutHere = useCallback(() => {
    changes.current += 1;
    setState(SIGNED_OUT);
  }, []);

  // Any 401 from the API means the session is gone (expired, logged out
  // elsewhere, account disabled): go back to the login page.
  useEffect(() => onUnauthorized(signOutHere), [signOutHere]);

  // A logout in another tab ends this one's session too, and a login there
  // may be someone else's. A tab that missed it checks when it is shown.
  useEffect(() => {
    const check = () => {
      if (document.visibilityState === "visible") refresh({ background: true, keepOnError: true });
    };
    document.addEventListener("visibilitychange", check);
    window.addEventListener("focus", check);
    if (typeof BroadcastChannel !== "undefined") {
      channel.current = new BroadcastChannel(CHANNEL);
      channel.current.onmessage = (e) => {
        if (e.data === "out") signOutHere();
        else {
          changes.current += 1;
          refresh({ background: true });
        }
      };
    }
    return () => {
      document.removeEventListener("visibilitychange", check);
      window.removeEventListener("focus", check);
      channel.current?.close();
      channel.current = null;
    };
  }, [refresh, signOutHere]);

  const login = useCallback(async (email, password) => {
    const user = await apiLogin(email, password);
    changes.current += 1;
    setState({ status: "in", user });
    channel.current?.postMessage("in");
    return user;
  }, []);

  // Hides everything at once ("leaving"), but is signed out only when the
  // API has ended the session. Until then the cookie still works, so a
  // failure is shown ("leave_failed") with a way to try again. Resolves to
  // whether the session ended.
  const logout = useCallback(async () => {
    changes.current += 1;
    setState({ status: "leaving", user: null });
    try {
      await apiLogout();
    } catch {
      setState({ status: "leave_failed", user: null });
      return false;
    }
    signOutHere();
    channel.current?.postMessage("out");
    return true;
  }, [signOutHere]);

  const value = useMemo(
    () => ({ ...state, login, logout, refresh }),
    [state, login, logout, refresh]
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export default AuthProvider;
