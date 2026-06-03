if (!requireAuth()) throw new Error("Redirecting to login");

const doctor = Auth.getDoctor();
if (doctor) document.getElementById("doctor-label").textContent = `${doctor.name} (${doctor.department || ""})`;


let nv = null;
let currentPatientId = null;
let currentMod = "t1";
let hasSeg = false;
let segVisible = true;
let allPatients = [];
const blobCache = {};   // patientId_mod → objectURL

const VIEW_MAP = { axial: 2, coronal: 1, sagittal: 0, render: 4, multiplanar: 3 };

// ─── 환자 목록 로드 ────────────────────────────────────────────────────────────
(async () => {
  try {
    allPatients = await apiFetch("/brats/patients");
    renderList(allPatients);
  } catch (err) {
    document.getElementById("patient-count").textContent = "오류: " + err.message;
  }
})();

function renderList(patients) {
  const count = document.getElementById("patient-count");
  const list  = document.getElementById("patient-list");
  count.textContent = `총 ${patients.length}명`;
  if (!patients.length) {
    list.innerHTML = `<div style="padding:20px;text-align:center" class="muted">데이터 없음</div>`;
    return;
  }
  list.innerHTML = patients.map(p => `
    <div class="browse-patient-item ${p.id === currentPatientId ? "active" : ""}"
         data-id="${p.id}" data-seg="${p.has_seg}">
      <span class="pid">${p.id}</span>
      ${p.has_seg ? `<span class="seg-badge">SEG</span>` : ""}
    </div>`).join("");

  list.querySelectorAll(".browse-patient-item").forEach(el => {
    el.addEventListener("click", () => loadPatient(el.dataset.id, el.dataset.seg === "true"));
  });
}

// ─── 검색 ──────────────────────────────────────────────────────────────────────
document.getElementById("search-input").addEventListener("input", e => {
  const q = e.target.value.toLowerCase();
  renderList(allPatients.filter(p => p.id.toLowerCase().includes(q)));
});

// ─── 환자 로드 ─────────────────────────────────────────────────────────────────
async function loadPatient(patientId, patientHasSeg) {
  if (patientId === currentPatientId) return;
  currentPatientId = patientId;
  hasSeg = patientHasSeg;

  // 사이드바 활성화
  document.querySelectorAll(".browse-patient-item").forEach(el =>
    el.classList.toggle("active", el.dataset.id === patientId));

  // UI 표시
  document.getElementById("empty-state").style.display = "none";
  document.getElementById("toolbar").style.display = "flex";
  document.getElementById("seg-control").style.display = hasSeg ? "flex" : "none";
  document.getElementById("seg-legend").style.display = hasSeg && segVisible ? "block" : "none";
  document.getElementById("patient-overlay").classList.add("visible");
  document.getElementById("ol-pid").textContent = patientId;

  // 모달리티 버튼 초기화
  setActiveMod("t1");
  await loadVolume(patientId, "t1");
}

// ─── 볼륨 로드 (캐시 활용) ─────────────────────────────────────────────────────
async function loadVolume(patientId, mod) {
  showLoading(`${mod.toUpperCase()} 로딩 중...`);
  setStatus("");

  try {
    const baseUrl = await getBlobUrl(patientId, mod);
    // name 속성 필수: blob URL은 확장자가 없어서 NiiVue가 파일 형식을 못 감지함
    const volumes = [{ url: baseUrl, name: `${patientId}_${mod}.nii`, colormap: "gray", opacity: 1 }];

    if (hasSeg) {
      const segUrl = await getBlobUrl(patientId, "seg");
      volumes.push({ url: segUrl, name: `${patientId}_seg.nii`, colormap: "warm", opacity: segVisible ? getSegOpacity() : 0 });
    }

    if (!nv) {
      const canvas = document.getElementById("browse-canvas");
      fitCanvas(canvas);
      window.addEventListener("resize", () => fitCanvas(canvas));
      nv = new Niivue({ backColor: [0,0,0,1], crosshairWidth: 1, isColorbar: false });
      await nv.attachToCanvas(canvas);
    }

    await nv.loadVolumes(volumes);
    document.getElementById("ol-mod").textContent = mod.toUpperCase();
    setStatus("로딩 완료");
  } catch (err) {
    setStatus("오류: " + err.message);
  } finally {
    hideLoading();
  }
}

async function getBlobUrl(patientId, mod) {
  const key = `${patientId}_${mod}`;
  if (blobCache[key]) return blobCache[key];
  const blob = await apiFetchBlob(`/brats/${patientId}/file/${mod}`);
  const url  = URL.createObjectURL(blob);
  blobCache[key] = url;
  return url;
}

// ─── 모달리티 전환 ─────────────────────────────────────────────────────────────
document.getElementById("mod-group").addEventListener("click", async e => {
  const btn = e.target.closest("[data-mod]");
  if (!btn || !currentPatientId || btn.dataset.mod === currentMod) return;
  setActiveMod(btn.dataset.mod);
  await swapBaseVolume(btn.dataset.mod);
});

function setActiveMod(mod) {
  currentMod = mod;
  document.querySelectorAll("[data-mod]").forEach(b =>
    b.classList.toggle("active", b.dataset.mod === mod));
  document.getElementById("ol-mod").textContent = mod.toUpperCase();
}

async function swapBaseVolume(mod) {
  if (!nv) return;
  showLoading(`${mod.toUpperCase()} 전환 중...`);
  try {
    const url = await getBlobUrl(currentPatientId, mod);
    // 볼륨 재로드 (base만 교체)
    const vols = [{ url, name: `${currentPatientId}_${mod}.nii`, colormap: "gray", opacity: 1 }];
    if (hasSeg && blobCache[`${currentPatientId}_seg`]) {
      vols.push({ url: blobCache[`${currentPatientId}_seg`], name: `${currentPatientId}_seg.nii`,
                  colormap: "warm", opacity: segVisible ? getSegOpacity() : 0 });
    }
    await nv.loadVolumes(vols);
    setStatus("완료");
  } catch (err) {
    setStatus("전환 오류: " + err.message);
  } finally {
    hideLoading();
  }
}

// ─── 뷰 모드 ──────────────────────────────────────────────────────────────────
document.getElementById("view-group").addEventListener("click", e => {
  const btn = e.target.closest("[data-view]");
  if (!btn || !nv) return;
  document.querySelectorAll("[data-view]").forEach(b => b.classList.toggle("active", b === btn));
  nv.setSliceType(VIEW_MAP[btn.dataset.view] ?? 2);
});

// ─── SEG 토글 ─────────────────────────────────────────────────────────────────
document.getElementById("toggle-seg").addEventListener("change", e => {
  segVisible = e.target.checked;
  document.getElementById("seg-legend").style.display = segVisible ? "block" : "none";
  if (!nv || nv.volumes.length < 2) return;
  nv.setOpacity(1, segVisible ? getSegOpacity() : 0);
  nv.drawScene();
});

document.getElementById("seg-opacity").addEventListener("input", e => {
  const val = parseInt(e.target.value);
  document.getElementById("opacity-val").textContent = val + "%";
  if (!nv || nv.volumes.length < 2 || !segVisible) return;
  nv.setOpacity(1, val / 100);
  nv.drawScene();
});

// ─── 유틸 ──────────────────────────────────────────────────────────────────────
function getSegOpacity() { return parseInt(document.getElementById("seg-opacity").value) / 100; }
function fitCanvas(c) { c.width = c.parentElement.clientWidth; c.height = c.parentElement.clientHeight; }
function setStatus(msg) { document.getElementById("viewer-status").textContent = msg; }
function showLoading(msg) {
  document.getElementById("loading-msg").textContent = msg;
  document.getElementById("loading-overlay").classList.remove("hidden");
}
function hideLoading() { document.getElementById("loading-overlay").classList.add("hidden"); }

window.addEventListener("unload", () =>
  Object.values(blobCache).forEach(URL.revokeObjectURL));

// ─── 경로 관리 모달 ────────────────────────────────────────────────────────────
document.getElementById("btn-manage-paths").addEventListener("click", () => {
  document.getElementById("modal-paths").classList.remove("hidden");
  loadPathList();
});

document.getElementById("close-paths-modal").addEventListener("click", () => {
  document.getElementById("modal-paths").classList.add("hidden");
});

document.getElementById("modal-paths").addEventListener("click", e => {
  if (e.target === e.currentTarget) e.currentTarget.classList.add("hidden");
});

async function loadPathList() {
  const list = document.getElementById("path-list");
  list.innerHTML = `<div class="muted" style="font-size:0.82rem;padding:8px">불러오는 중...</div>`;
  try {
    const paths = await apiFetch("/brats/paths");
    list.innerHTML = paths.map(p => `
      <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;
                  background:var(--card);border-radius:6px;border:1px solid var(--border)">
        <div style="flex:1;min-width:0">
          <div style="font-size:0.82rem;font-weight:600">${p.label || p.path}</div>
          <div class="muted" style="font-size:0.75rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${p.path}</div>
        </div>
        ${p.id === null
          ? `<span style="font-size:0.72rem;color:var(--muted);padding:2px 8px;border:1px solid var(--border);border-radius:4px">기본</span>`
          : `<button class="btn btn-danger btn-sm" style="font-size:0.75rem;padding:3px 10px"
               onclick="deletePath(${p.id})">삭제</button>`
        }
      </div>`).join("");
  } catch (err) {
    list.innerHTML = `<div class="muted" style="font-size:0.82rem;padding:8px">오류: ${err.message}</div>`;
  }
}

document.getElementById("btn-add-path").addEventListener("click", async () => {
  const pathInput  = document.getElementById("new-path-input");
  const labelInput = document.getElementById("new-path-label");
  const errEl      = document.getElementById("path-add-error");
  errEl.style.display = "none";

  const path  = pathInput.value.trim();
  const label = labelInput.value.trim();
  if (!path) return;

  try {
    await apiFetch("/brats/paths", {
      method: "POST",
      body: JSON.stringify({ path, label: label || null }),
    });
    pathInput.value  = "";
    labelInput.value = "";
    await loadPathList();
    // 환자 목록 새로고침
    allPatients = await apiFetch("/brats/patients");
    renderList(allPatients);
  } catch (err) {
    errEl.textContent = err.message;
    errEl.style.display = "block";
  }
});

async function deletePath(id) {
  if (!confirm("이 경로를 삭제하시겠습니까?")) return;
  try {
    await apiFetch(`/brats/paths/${id}`, { method: "DELETE" });
    await loadPathList();
    allPatients = await apiFetch("/brats/patients");
    renderList(allPatients);
  } catch (err) {
    alert("삭제 실패: " + err.message);
  }
}
