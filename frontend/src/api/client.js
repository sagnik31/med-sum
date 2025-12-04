// src/api/client.js

// Base URLs
export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8080";

export const INSIGHTS_API_BASE_URL =
  import.meta.env.VITE_INSIGHTS_API_BASE_URL || "http://localhost:9000";

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

export async function getInsight(documentId) {
  const token = getAuthToken();
  const headers = new Headers();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(
    `${API_BASE_URL}/documents/${documentId}/insight`,
    {
      method: "GET",
      headers,
    }
  );

  // If Go explicitly says "not found"
  if (response.status === 404 || response.status === 204) {
    return { exists: false, data: null };
  }

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    if (response.status === 401) {
      throw new Error("Unauthorized: Please log in to view insights.");
    }
    throw new Error(
      `Failed to fetch insight ${response.status}: ${
        text || response.statusText
      }`
    );
  }

  // Try to parse JSON, but handle empty body safely
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }

  // If no data at all, treat as "no insight yet"
  if (!data) {
    return { exists: false, data: null };
  }

  return { exists: true, data };
}

// 2) Trigger Python FastAPI to generate insights
export async function triggerInsightGeneration(documentId) {
  const token = getAuthToken();
  const headers = new Headers({
    "Content-Type": "application/json",
  });
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(
    `${INSIGHTS_API_BASE_URL}/internal/generate-insights`,
    {
      method: "POST",
      headers,
      body: JSON.stringify({
        document_id: documentId,
      }),
    }
  );

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(
      `Failed to trigger insight generation ${response.status}: ${
        text || response.statusText
      }`
    );
  }

  // We don't really care about the body here; it's fire-and-forget
  return true;
}