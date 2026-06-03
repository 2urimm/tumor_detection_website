if (!requireAuth()) throw new Error("Redirecting to login");

let selectedPatientId = null;
let allPatients = [];

// ─── 초기화 ────────────────────────────────────────────────────────────────────
(async () => {
  const doctor = Auth.getDoctor();
  if (doctor) {
    document.getElementById("doctor-name").textContent = doctor.name;
    document.getElementById("doctor-dept").textContent = doctor.department || "";
    document.getElementById("doctor-avatar").textContent = doctor.name[0];
  }
  await loadPatients();
})();

document.getElementById("btn-logout").addEventListener("click", () => Auth.logout());

// ─── 환자 목록 ─────────────────────────────────────────────────────────────────
async function loadPatients() {
  try {
    allPatients = await Patients.list();
    renderPatientList(allPatients);
  } catch (err) {
    document.getElementById("patient-list").innerHTML =
      `<div class="alert alert-error">${err.message}</div>`;
  }
}

function renderPatientList(patients) {
  const list = document.getElementById("patient-list");
  if (!patients.length) {
    list.innerHTML = `<div style="text-align:center;padding:30px 0" class="muted">등록된 환자가 없습니다</div>`;
    return;
  }
  list.innerHTML = patients.map(p => {
    const prob = p.latest_analysis?.probability;
    const probHtml = prob != null
      ? `<span style="color:${probColor(prob)};font-weight:700">${(prob * 100).toFixed(1)}%</span>`
      : `<span>${statusBadge(p.latest_analysis?.status ?? "없음")}</span>`;
    return `
      <div class="patient-item ${p.id === selectedPatientId ? "active" : ""}"
           data-id="${p.id}" onclick="selectPatient(${p.id})">
        <div class="patient-item-name">${escape(p.name)}</div>
        <div class="patient-item-meta">
          <span>${escape(p.patient_code)}</span>
          ${p.age ? `<span>· ${p.age}세</span>` : ""}
          ${probHtml}
        </div>
      </div>`;
  }).join("");
}

document.getElementById("search-input").addEventListener("input", (e) => {
  const q = e.target.value.toLowerCase();
  renderPatientList(allPatients.filter(p =>
    p.name.toLowerCase().includes(q) || p.patient_code.toLowerCase().includes(q)
  ));
});

// ─── 환자 선택 ─────────────────────────────────────────────────────────────────
async function selectPatient(id) {
  selectedPatientId = id;
  document.querySelectorAll(".patient-item").forEach(el =>
    el.classList.toggle("active", parseInt(el.dataset.id) === id)
  );
  document.getElementById("empty-state").style.display = "none";
  document.getElementById("patient-detail").style.display = "flex";

  try {
    const p = await Patients.get(id);
    document.getElementById("main-title").textContent = p.name;
    document.getElementById("pd-name").textContent = p.name;
    document.getElementById("pd-code").textContent = `환자 ID: ${p.patient_code}`;
    document.getElementById("pd-age").textContent = p.age ? `${p.age}세` : "—";
    document.getElementById("pd-gender").textContent = p.gender || "—";
    document.getElementById("pd-date").textContent = formatDate(p.created_at);

    const notesEl = document.getElementById("pd-notes");
    if (p.notes) { notesEl.textContent = p.notes; notesEl.style.display = "block"; }
    else notesEl.style.display = "none";

    renderAnalyses(p.analyses);

    document.getElementById("btn-new-analysis").onclick = () => openAnalysisModal(id);
    document.getElementById("btn-delete-patient").onclick = () => deletePatient(id, p.name);
  } catch (err) {
    showToast(err.message, "error");
  }
}

function renderAnalyses(analyses) {
  const el = document.getElementById("analysis-list");
  document.getElementById("analysis-count").textContent = `${analyses.length}건`;
  if (!analyses.length) {
    el.innerHTML = `<div class="muted" style="text-align:center;padding:20px">분석 이력 없음</div>`;
    return;
  }
  el.innerHTML = analyses.map(a => {
    const prob = a.probability;
    const cls = prob >= 0.7 ? "prob-high" : prob >= 0.3 ? "prob-mid" : "prob-low";
    const pct = prob != null ? (prob * 100).toFixed(1) : null;
    return `
      <div class="analysis-row"
           onclick="${a.status === "done" ? `goViewer('${a.id}')` : ""}">
        <div>
          <div style="font-weight:600;font-size:0.88rem">${formatDate(a.created_at)}</div>
          <div class="muted" style="font-size:0.78rem">
            ${a.tumor_volume_cc != null ? `종양 체적: ${a.tumor_volume_cc} cc` : ""}
          </div>
        </div>
        ${pct != null ? `
        <div class="prob-bar-wrap ${cls}">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px">
            <span class="muted" style="font-size:0.75rem">종양 확률</span>
            <span style="font-weight:700;color:${probColor(prob)}">${pct}%</span>
          </div>
          <div class="prob-bar-track"><div class="prob-bar-fill" style="width:${pct}%"></div></div>
        </div>` : `<span>${statusBadge(a.status)}</span>`}
        ${a.status === "done" ? `<span style="color:var(--accent);font-size:0.82rem">뷰어 →</span>` : ""}
        <button class="btn-analysis-delete" title="분석 삭제"
                onclick="deleteAnalysis(event, '${a.id}')">🗑</button>
      </div>`;
  }).join("");
}

function goViewer(analysisId) {
  window.location.href = `viewer.html?analysisId=${analysisId}`;
}

// ─── 분석 삭제 ─────────────────────────────────────────────────────────────────
async function deleteAnalysis(event, analysisId) {
  event.stopPropagation(); // 뷰어 이동 방지
  if (!confirm("이 분석 결과를 삭제하시겠습니까?\nMRI 파일과 AI 소견이 모두 삭제됩니다.")) return;
  try {
    await Analysis.delete(analysisId);
    await selectPatient(selectedPatientId); // 목록 새로고침
    showToast("분석 결과가 삭제되었습니다", "success");
  } catch (err) {
    showToast(err.message, "error");
  }
}

// ─── 환자 삭제 ─────────────────────────────────────────────────────────────────
async function deletePatient(id, name) {
  if (!confirm(`"${name}" 환자를 삭제하시겠습니까?\n모든 분석 데이터도 함께 삭제됩니다.`)) return;
  try {
    await Patients.delete(id);
    selectedPatientId = null;
    document.getElementById("patient-detail").style.display = "none";
    document.getElementById("empty-state").style.display = "flex";
    document.getElementById("main-title").textContent = "환자를 선택하세요";
    await loadPatients();
    showToast("환자가 삭제되었습니다", "success");
  } catch (err) {
    showToast(err.message, "error");
  }
}

// ─── 새 환자 모달 ──────────────────────────────────────────────────────────────
document.getElementById("btn-new-patient").addEventListener("click", () => {
  document.getElementById("modal-patient").classList.remove("hidden");
});
["close-patient-modal", "cancel-patient"].forEach(id =>
  document.getElementById(id).addEventListener("click", closePatientModal)
);
function closePatientModal() {
  document.getElementById("modal-patient").classList.add("hidden");
  document.getElementById("patient-form").reset();
  document.getElementById("patient-alert").style.display = "none";
}

document.getElementById("patient-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = document.getElementById("submit-patient");
  btn.disabled = true; btn.textContent = "등록 중...";
  const alert = document.getElementById("patient-alert");
  alert.style.display = "none";
  try {
    const p = await Patients.create({
      patient_code: document.getElementById("f-code").value,
      name: document.getElementById("f-name").value,
      age: parseInt(document.getElementById("f-age").value) || null,
      gender: document.getElementById("f-gender").value || null,
      notes: document.getElementById("f-notes").value || null,
    });
    closePatientModal();
    await loadPatients();
    selectPatient(p.id);
    showToast("환자가 등록되었습니다", "success");
  } catch (err) {
    alert.textContent = err.message;
    alert.style.display = "block";
  } finally {
    btn.disabled = false; btn.textContent = "등록";
  }
});

// ─── 분석 업로드 모달 ──────────────────────────────────────────────────────────
function openAnalysisModal(patientId) {
  document.getElementById("modal-analysis").classList.remove("hidden");
  document.getElementById("analysis-form").dataset.pid = patientId;
}
["close-analysis-modal", "cancel-analysis"].forEach(id =>
  document.getElementById(id).addEventListener("click", closeAnalysisModal)
);
function closeAnalysisModal() {
  document.getElementById("modal-analysis").classList.add("hidden");
  document.getElementById("analysis-form").reset();
  ["t1", "t1ce", "t2", "flair"].forEach(m => {
    document.getElementById(`name-${m}`).textContent = "";
    document.getElementById(`drop-${m}`).classList.remove("has-file");
  });
  document.getElementById("analysis-alert").style.display = "none";
}

// 파일 선택 시 파일명 표시
["t1", "t1ce", "t2", "flair"].forEach(m => {
  document.getElementById(`file-${m}`).addEventListener("change", (e) => {
    const name = e.target.files[0]?.name || "";
    const nameEl = document.getElementById(`name-${m}`);
    const dropEl = document.getElementById(`drop-${m}`);
    nameEl.textContent = name;
    dropEl.classList.toggle("has-file", !!name);
  });
});

document.getElementById("analysis-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const patientId = e.target.dataset.pid;
  const btn = document.getElementById("submit-analysis");
  const alert = document.getElementById("analysis-alert");
  alert.style.display = "none";
  btn.disabled = true; btn.textContent = "분석 중... (잠시 기다려주세요)";

  const fd = new FormData();
  fd.append("t1",    document.getElementById("file-t1").files[0]);
  fd.append("t1ce",  document.getElementById("file-t1ce").files[0]);
  fd.append("t2",    document.getElementById("file-t2").files[0]);
  fd.append("flair", document.getElementById("file-flair").files[0]);

  try {
    const result = await Analysis.run(patientId, fd);
    closeAnalysisModal();
    await selectPatient(parseInt(patientId));
    await loadPatients();
    showToast(`분석 완료 — 종양 확률 ${(result.probability * 100).toFixed(1)}%`, "success");
  } catch (err) {
    alert.textContent = err.message;
    alert.style.display = "block";
  } finally {
    btn.disabled = false; btn.textContent = "분석 시작";
  }
});

// ─── 유틸 ──────────────────────────────────────────────────────────────────────
function formatDate(iso) {
  return new Date(iso).toLocaleString("ko-KR", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
function probColor(p) {
  return p >= 0.7 ? "var(--danger)" : p >= 0.3 ? "var(--warning)" : "var(--success)";
}
function statusBadge(s) {
  const map = { done: ["done", "완료"], processing: ["processing", "분석중"], failed: ["failed", "실패"], pending: ["pending", "대기"] };
  const [cls, label] = map[s] || ["pending", s];
  return `<span class="badge badge-${cls}">${label}</span>`;
}
function escape(s) {
  return (s || "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

let toastTimer;
function showToast(msg, type = "success") {
  let toast = document.getElementById("toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "toast";
    toast.style.cssText = "position:fixed;bottom:24px;right:24px;padding:12px 20px;border-radius:8px;font-weight:600;z-index:999;transition:opacity 0.3s;font-size:0.88rem;max-width:320px";
    document.body.appendChild(toast);
  }
  toast.textContent = msg;
  toast.style.background = type === "success" ? "#16a34a" : "#dc2626";
  toast.style.color = "#fff";
  toast.style.opacity = "1";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.style.opacity = "0", 3000);
}
