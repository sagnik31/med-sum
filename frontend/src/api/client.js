// src/api/client.js

// Base URLs
export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8080";

// Helper to get auth token (stored after login)
function getAuthToken() {
  return localStorage.getItem("authToken");
}

async function apiRequest(path, options = {}) {
  const token = getAuthToken();

  const headers = new Headers(options.headers || {});
  if (!(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    if (response.status === 401) {
      throw new Error("Unauthorized: Please log in to continue.");
    }
    throw new Error(
      `API error ${response.status}: ${text || response.statusText}`
    );
  }

  try {
    return await response.json();
  } catch {
    return null;
  }
}

// ---------- Documents API ----------

export function fetchDocuments() {
  return apiRequest("/documents", {
    method: "GET",
  });
}

export function uploadDocument(file) {
  const token = getAuthToken();
  const formData = new FormData();
  formData.append("file", file);

  const headers = new Headers();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  return fetch(`${API_BASE_URL}/documents`, {
    method: "POST",
    body: formData,
    headers,
  }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      if (response.status === 401) {
        throw new Error("Unauthorized: Please log in to upload documents.");
      }
      throw new Error(
        `Upload failed ${response.status}: ${text || response.statusText}`
      );
    }
    try {
      return await response.json();
    } catch {
      return null;
    }
  });
}

export function deleteDocument(id) {
  return apiRequest(`/documents/${id}`, {
    method: "DELETE",
  });
}

export function getInsight(documentId) {
  return apiRequest(`/documents/${documentId}/insight`, {
    method: "GET",
  });
}

// Ask the backend to start insight generation (it checks ownership and
// forwards to the AI service). Fire-and-forget: poll getInsight for the result.
export function triggerInsightGeneration(documentId) {
  return apiRequest(`/documents/${documentId}/insight`, {
    method: "POST",
  });
}

export function fetchUserInsights() {
  return apiRequest("/user/insights", {
    method: "GET",
  });
}

// Start (re)generating the patient summary in the background; poll
// fetchUserInsights for the result.
export function triggerUserInsights() {
  return apiRequest("/user/insights", {
    method: "POST",
  });
}