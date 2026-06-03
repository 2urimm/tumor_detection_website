/** API 기본 설정 */
const API_BASE = `${location.protocol}//${location.host}/api`;

function getToken() {
  return sessionStorage.getItem("access_token");
}

function saveAuth(data) {
  sessionStorage.setItem("access_token", data.access_token);
  sessionStorage.setItem("doctor", JSON.stringify(data.doctor));
  sessionStorage.setItem("token_expires", Date.now() + data.expires_in * 1000);
}

function clearAuth() {
  sessionStorage.removeItem("access_token");
  sessionStorage.removeItem("doctor");
  sessionStorage.removeItem("token_expires");
}

function isTokenExpired() {
  const exp = sessionStorage.getItem("token_expires");
  return !exp || Date.now() > parseInt(exp);
}

function requireAuth() {
  if (!getToken() || isTokenExpired()) {
    clearAuth();
    window.location.href = "/app/index.html";
    return false;
  }
  return true;
}

async function apiFetch(path, options = {}) {
  const token = getToken();
  const headers = { "Content-Type": "application/json", ...options.headers };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (options.body instanceof FormData) delete headers["Content-Type"];

  const resp = await fetch(API_BASE + path, { ...options, headers });

  if (resp.status === 401) {
    clearAuth();
    window.location.href = "/app/index.html";
    throw new Error("세션이 만료되었습니다. 다시 로그인해주세요.");
  }

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
    throw new Error(err.detail || "서버 오류가 발생했습니다");
  }

  if (resp.status === 204) return null;
  return resp.json();
}

async function apiFetchBlob(path) {
  const token = getToken();
  const resp = await fetch(API_BASE + path, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error("파일을 불러올 수 없습니다");
  return resp.blob();
}

// ─── Auth ─────────────────────────────────────────────────────────────────────
const Auth = {
  async login(email, password) {
    const data = await apiFetch("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    saveAuth(data);
    return data;
  },
  logout() {
    clearAuth();
    window.location.href = "/app/index.html";
  },
  getDoctor() {
    const d = sessionStorage.getItem("doctor");
    return d ? JSON.parse(d) : null;
  },
};

// ─── Patients ─────────────────────────────────────────────────────────────────
const Patients = {
  list: () => apiFetch("/patients"),
  get: (id) => apiFetch(`/patients/${id}`),
  create: (data) => apiFetch("/patients", { method: "POST", body: JSON.stringify(data) }),
  delete: (id) => apiFetch(`/patients/${id}`, { method: "DELETE" }),
};

// ─── Analysis ─────────────────────────────────────────────────────────────────
const Analysis = {
  run(patientId, formData) {
    return apiFetch(`/patients/${patientId}/analyze`, {
      method: "POST",
      headers: { Authorization: `Bearer ${getToken()}` },
      body: formData,
    });
  },
  get: (id) => apiFetch(`/analysis/${id}`),
  delete: (id) => apiFetch(`/analysis/${id}`, { method: "DELETE" }),
  getAiExplain: (id) => apiFetch(`/analysis/${id}/ai-explain`),
  async getNiiBlob(analysisId, modality) {
    const blob = await apiFetchBlob(`/analysis/${analysisId}/file/${modality}`);
    return URL.createObjectURL(blob);
  },
  async getRawNiiBlob(analysisId, modality) {
    const blob = await apiFetchBlob(`/analysis/${analysisId}/raw/${modality}`);
    return URL.createObjectURL(blob);
  },
};
