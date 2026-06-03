if (!requireAuth()) throw new Error("Redirecting to login");

const params     = new URLSearchParams(location.search);
const analysisId = params.get("analysisId");
if (!analysisId) { location.href = "dashboard.html"; }

let nv              = null;
let currentModality = "t1";
let maskVisible     = true;
let currentView     = "doctor";
let currentMaskUrl  = null;   // 마스크 blob URL 고정 참조
let baseCal         = null;   // NiiVue 자동 감지 cal_min/cal_max 저장

const DIM    = { x: 239, y: 239, z: 154 };
const blobUrls = [];

// ─── 초기화 ───────────────────────────────────────────────────────────────────
(async () => {
  try {
    const data = await Analysis.get(analysisId);
    if (data.status !== "done") {
      alert("분석이 완료되지 않았습니다.");
      location.href = "dashboard.html";
      return;
    }
    renderResults(data);
    loadAiExplanation(analysisId);   // AI 설명은 비동기로 따로 로드
    await initViewer(data);
  } catch (err) {
    document.getElementById("viewer-status").textContent = "오류: " + err.message;
    document.getElementById("loading-overlay").innerHTML =
      `<div class="alert alert-error">${err.message}</div>`;
  }
})();

// ─── 뷰 모드 전환 ─────────────────────────────────────────────────────────────
function setView(view) {
  currentView = view;
  document.getElementById("view-doctor").style.display  = view === "doctor"  ? "" : "none";
  document.getElementById("view-patient").style.display = view === "patient" ? "" : "none";
  document.getElementById("btn-doctor").classList.toggle("active", view === "doctor");
  document.getElementById("btn-patient").classList.toggle("active", view === "patient");
}

// ─── 결과 패널 렌더링 ─────────────────────────────────────────────────────────
function renderResults(data) {
  const prob   = data.probability;
  const pct    = (prob * 100).toFixed(1);
  const isHigh = prob >= 0.7;
  const isMid  = prob >= 0.3 && prob < 0.7;
  const colorCls = isHigh ? "prob-high-color" : isMid ? "prob-mid-color" : "prob-low-color";
  const verdict  = isHigh ? "⚠ 종양 의심 — 정밀 검사 권고"
                 : isMid  ? "△ 추가 모니터링 필요"
                 :          "✓ 종양 가능성 낮음";

  // 의사용 확률 원
  document.getElementById("prob-number").textContent = pct + "%";
  document.getElementById("prob-circle").className   = `prob-circle ${colorCls}`;
  document.getElementById("prob-verdict").textContent = verdict;
  document.getElementById("prob-verdict").style.color =
    isHigh ? "var(--danger)" : isMid ? "var(--warning)" : "var(--success)";

  document.getElementById("r-volume").textContent =
    data.tumor_volume_cc != null ? `${data.tumor_volume_cc} cc` : "—";

  const c = data.tumor_centroid;
  document.getElementById("r-centroid").textContent =
    c ? `x:${c.x.toFixed(0)} y:${c.y.toFixed(0)} z:${c.z.toFixed(0)}` : "—";

  const dateStr = new Date(data.created_at).toLocaleString("ko-KR");
  document.getElementById("r-date").textContent         = dateStr;
  document.getElementById("r-date-patient").textContent = dateStr;

  // 환자용 상태 카드 (수치 없이 상태만)
  renderPatientStatus(prob);

  // XAI 리포트
  if (data.xai) renderXai(data.xai);
}

function renderPatientStatus(prob) {
  const card  = document.getElementById("patient-status-card");
  const icon  = document.getElementById("patient-status-icon");
  const label = document.getElementById("patient-status-label");
  const msg   = document.getElementById("patient-status-msg");

  if (prob >= 0.7) {
    card.className  = "patient-status-card status-attention";
    icon.textContent  = "🔍";
    label.textContent = "추가 확인 필요";
    msg.textContent   = "검사에서 추가적인 확인이 필요한 부분이 발견되었습니다.";
  } else if (prob >= 0.4) {
    card.className  = "patient-status-card status-monitor";
    icon.textContent  = "📊";
    label.textContent = "관찰 중";
    msg.textContent   = "검사 결과를 검토하였으며 일부 관찰이 필요한 부분이 있습니다.";
  } else {
    card.className  = "patient-status-card status-stable";
    icon.textContent  = "✅";
    label.textContent = "안정적";
    msg.textContent   = "전반적으로 안정적인 검사 결과가 확인되었습니다.";
  }
}

function renderXai(xai) {
  document.getElementById("xai-section").style.display      = "";
  document.getElementById("xai-desc-section").style.display = "";

  document.getElementById("xai-location").textContent = xai.location.full;

  const riskEl = document.getElementById("xai-risk");
  riskEl.textContent = xai.risk.level;
  riskEl.className   = `xai-risk-badge xai-risk-${xai.risk.color}`;
  riskEl.title       = xai.risk.reason;

  const total = xai.subtypes.total.volume_cc || 1;
  [
    { key: "enhancing", id: "enhancing" },
    { key: "necrotic",  id: "necrotic"  },
    { key: "edema",     id: "edema"     },
  ].forEach(({ key, id }) => {
    const s = xai.subtypes[key];
    document.getElementById(`bar-${id}`).style.width  = s.ratio_pct + "%";
    document.getElementById(`val-${id}`).textContent  = `${s.volume_cc} cc (${s.ratio_pct}%)`;
  });

  document.getElementById("xai-description").textContent = xai.description;
}

// ─── AI 설명 비동기 로드 ──────────────────────────────────────────────────────
async function loadAiExplanation(id) {
  try {
    const data = await Analysis.getAiExplain(id);

    // 의사용
    const doctorContent = document.getElementById("ai-doctor-content");
    doctorContent.innerHTML = formatDoctorText(data.doctor);
    document.getElementById("ai-doctor-loading").style.display = "none";
    doctorContent.style.display = "";
    document.getElementById("ai-doctor-note").style.display = "";

    // 환자용
    const patientContent = document.getElementById("ai-patient-content");
    patientContent.innerHTML = `<p>${escapeHtml(data.patient).replace(/\n/g, "<br>")}</p>`;
    document.getElementById("ai-patient-loading").style.display = "none";
    patientContent.style.display = "";

  } catch (err) {
    const errHtml = `<p class="muted" style="font-size:0.8rem">생성 실패: ${escapeHtml(err.message)}</p>`;
    document.getElementById("ai-doctor-loading").innerHTML  = errHtml;
    document.getElementById("ai-patient-loading").innerHTML =
      `<p class="muted" style="font-size:0.8rem">안내 내용을 준비할 수 없습니다. 담당 의사에게 문의하세요.</p>`;
  }
}

// **[헤더]** 형태를 섹션으로 파싱
function formatDoctorText(text) {
  const parts = text.split(/\*\*\[([^\]]+)\]\*\*/g);
  if (parts.length <= 1) {
    return `<p class="ai-section-body">${escapeHtml(text).replace(/\n/g, "<br>")}</p>`;
  }
  let html = "";
  for (let i = 0; i < parts.length; i++) {
    const content = parts[i].trim();
    if (!content) continue;
    if (i % 2 === 1) {
      html += `<div class="ai-section-header">${escapeHtml(content)}</div>`;
    } else {
      html += `<p class="ai-section-body">${escapeHtml(content).replace(/\n/g, "<br>")}</p>`;
    }
  }
  return html;
}

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ─── NiiVue 초기화 ─────────────────────────────────────────────────────────────
async function initViewer(data) {
  const canvas = document.getElementById("niivue-canvas");
  fitCanvas(canvas);
  window.addEventListener("resize", () => fitCanvas(canvas));

  nv = new Niivue({
    backColor:      [0, 0, 0, 1],
    crosshairColor: [0.3, 0.7, 1, 0.8],
    crosshairWidth: 1,
    isColorbar:     false,
  });
  await nv.attachToCanvas(canvas);

  setStatus("베이스 볼륨 다운로드 중...");
  const baseUrl = await Analysis.getRawNiiBlob(analysisId, currentModality);
  blobUrls.push(baseUrl);

  setStatus("마스크 다운로드 중...");
  currentMaskUrl = await Analysis.getNiiBlob(analysisId, "mask");
  blobUrls.push(currentMaskUrl);

  await nv.loadVolumes([
    { url: baseUrl,         name: `${analysisId}_${currentModality}.nii.gz`, colormap: "gray", opacity: 1 },
    { url: currentMaskUrl, name: `${analysisId}_mask.nii.gz`,               colormap: "warm", opacity: 0.5 },
  ]);

  if (nv.volumes.length >= 2) {
    baseCal = { min: nv.volumes[0].cal_min, max: nv.volumes[0].cal_max };
    // 마스크: 레이블 0~3 중 1~3만 표시
    nv.volumes[1].cal_min = 0.5;
    nv.volumes[1].cal_max = 3.5;
    nv.updateGLVolume();
  }

  setSliceTo(data.tumor_centroid);
  hideLoading();
  setStatus("로딩 완료");
}

// ─── 모달리티 전환 ─────────────────────────────────────────────────────────────
document.getElementById("modality-group").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-mod]");
  if (!btn || btn.dataset.mod === currentModality || !nv) return;

  currentModality = btn.dataset.mod;
  document.querySelectorAll("[data-mod]").forEach(b =>
    b.classList.toggle("active", b === btn));

  setStatus(`${currentModality.toUpperCase()} 로딩 중...`);
  try {
    const url = await Analysis.getRawNiiBlob(analysisId, currentModality);
    blobUrls.push(url);
    await nv.loadVolumes([
      { url, name: `${analysisId}_${currentModality}.nii.gz`, colormap: "gray", opacity: 1 },
      { url: currentMaskUrl, name: `${analysisId}_mask.nii.gz`, colormap: "warm", opacity: maskVisible ? getMaskOpacity() : 0 },
    ]);
    if (nv.volumes.length >= 2) {
      baseCal = { min: nv.volumes[0].cal_min, max: nv.volumes[0].cal_max };
      nv.volumes[1].cal_min = 0.5;
      nv.volumes[1].cal_max = 3.5;
      nv.updateGLVolume();
    }
    setStatus("완료");
  } catch (err) {
    setStatus("모달리티 로드 실패: " + err.message);
  }
});

// ─── 뷰 모드 전환 ─────────────────────────────────────────────────────────────
const VIEW_MAP = {
  axial:       2,
  coronal:     1,
  sagittal:    0,
  render:      4,
  multiplanar: 3,
};

document.getElementById("view-group").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-view]");
  if (!btn || !nv) return;
  document.querySelectorAll("[data-view]").forEach(b =>
    b.classList.toggle("active", b === btn));

  const viewType = btn.dataset.view;
  nv.setSliceType(VIEW_MAP[viewType] ?? 2);

  if (nv.volumes.length > 0) {
    if (viewType === "render" && baseCal) {
      // 3D: 배경(0)을 투명하게 — cal_min을 살짝 올림
      nv.volumes[0].cal_min = Math.max(baseCal.min, 0.1);
      nv.volumes[0].cal_max = baseCal.max;
      nv.updateGLVolume();
    } else if (viewType !== "render" && baseCal) {
      // 2D 복귀: 원래 cal 복원
      nv.volumes[0].cal_min = baseCal.min;
      nv.volumes[0].cal_max = baseCal.max;
      nv.updateGLVolume();
    }
    nv.drawScene();
  }
});

// ─── 마스크 토글 & 불투명도 ───────────────────────────────────────────────────
document.getElementById("toggle-mask").addEventListener("change", (e) => {
  maskVisible = e.target.checked;
  if (!nv || nv.volumes.length < 2) return;
  nv.setOpacity(1, maskVisible ? getMaskOpacity() : 0);
  nv.drawScene();
});

document.getElementById("mask-opacity").addEventListener("input", (e) => {
  const val = parseInt(e.target.value);
  document.getElementById("opacity-val").textContent = val + "%";
  if (!nv || nv.volumes.length < 2 || !maskVisible) return;
  nv.setOpacity(1, val / 100);
  nv.drawScene();
});

// ─── 슬라이스 탐색 ────────────────────────────────────────────────────────────
["axial", "coronal", "sagittal"].forEach(plane => {
  const slider = document.getElementById(`slice-${plane}`);
  const label  = document.getElementById(`val-${plane}`);
  const maxDim = { axial: DIM.z, coronal: DIM.y, sagittal: DIM.x };
  slider.addEventListener("input", () => {
    label.textContent = slider.value;
    if (!nv) return;
    const frac = parseInt(slider.value) / maxDim[plane];
    if (plane === "axial")    nv.scene.crosshairPos[2] = Math.min(1, frac);
    if (plane === "coronal")  nv.scene.crosshairPos[1] = Math.min(1, frac);
    if (plane === "sagittal") nv.scene.crosshairPos[0] = Math.min(1, frac);
    nv.drawScene();
  });
});

// ─── 유틸 ────────────────────────────────────────────────────────────────────
function fitCanvas(canvas) {
  const wrap = canvas.parentElement;
  canvas.width  = wrap.clientWidth;
  canvas.height = wrap.clientHeight;
}

function getMaskOpacity() {
  return parseInt(document.getElementById("mask-opacity").value) / 100;
}

function setStatus(msg) {
  document.getElementById("viewer-status").textContent = msg;
}

function hideLoading() {
  document.getElementById("loading-overlay").classList.add("hidden");
}

function setSliceTo(centroid) {
  if (!nv || !centroid) return;
  nv.scene.crosshairPos = [
    centroid.x / DIM.x,
    centroid.y / DIM.y,
    centroid.z / DIM.z,
  ];
  ["axial", "coronal", "sagittal"].forEach(p => {
    const val = Math.round({ axial: centroid.z, coronal: centroid.y, sagittal: centroid.x }[p]);
    document.getElementById(`slice-${p}`).value       = val;
    document.getElementById(`val-${p}`).textContent   = val;
  });
  nv.drawScene();
}

window.addEventListener("unload", () => blobUrls.forEach(URL.revokeObjectURL));
