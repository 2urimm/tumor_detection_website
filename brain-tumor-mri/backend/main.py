import os
import uuid
import json
import shutil
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from pathlib import Path

from fastapi import (
    FastAPI, Depends, HTTPException, status,
    UploadFile, File, Request, BackgroundTasks,
)
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sqlalchemy import (
    create_engine, Column, Integer, String, Float,
    DateTime, ForeignKey, Text, Boolean, JSON,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
import bcrypt as _bcrypt
from jose import JWTError, jwt
from pydantic import BaseModel
import numpy as np
import nibabel as nib

from preprocessing import preprocess_files
from model_client import run_inference   # 실제 모델 서버 클라이언트 (mock fallback 포함)
from explainability import compute_xai_report, generate_ai_explanations

# ─── 설정 ─────────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "CHANGE_THIS_IN_PRODUCTION_USE_256BIT_RANDOM")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
RESULTS_DIR = BASE_DIR / "results"
UPLOAD_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".nii", ".gz", ".dcm"}

# BraTS 데이터 경로 (환경변수로 오버라이드 가능)
BRATS_DATA_PATH = Path(os.getenv(
    "BRATS_DATA_PATH",
    r"F:\Bio_health\kagglehub\datasets\awsaf49\brats20-dataset-training-validation\versions\1\BraTS2020_TrainingData\MICCAI_BraTS2020_TrainingData",
))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(BASE_DIR / "audit.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ─── 데이터베이스 ──────────────────────────────────────────────────────────────
engine = create_engine(
    f"sqlite:///{BASE_DIR}/brain_tumor.db",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Doctor(Base):
    __tablename__ = "doctors"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(200), unique=True, nullable=False, index=True)
    hashed_password = Column(String(200), nullable=False)
    department = Column(String(100))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    patients = relationship("Patient", back_populates="doctor", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="doctor")


class Patient(Base):
    __tablename__ = "patients"
    id = Column(Integer, primary_key=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    patient_code = Column(String(50), nullable=False)
    name = Column(String(100), nullable=False)
    age = Column(Integer)
    gender = Column(String(10))
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    doctor = relationship("Doctor", back_populates="patients")
    analyses = relationship(
        "Analysis", back_populates="patient",
        cascade="all, delete-orphan", order_by="Analysis.created_at.desc()",
    )


class Analysis(Base):
    __tablename__ = "analyses"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    status = Column(String(20), default="pending")  # pending | processing | done | failed
    probability = Column(Float)
    tumor_volume_cc = Column(Float)
    tumor_centroid = Column(JSON)
    nii_t1_path = Column(String(500))
    nii_t1ce_path = Column(String(500))
    nii_t2_path = Column(String(500))
    nii_flair_path = Column(String(500))
    mask_path = Column(String(500))
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    patient = relationship("Patient", back_populates="analyses")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=True)
    action = Column(String(100))
    resource_type = Column(String(50))
    resource_id = Column(String(200))
    ip_address = Column(String(50))
    timestamp = Column(DateTime, default=datetime.utcnow)
    doctor = relationship("Doctor", back_populates="audit_logs")


class BratsDataPath(Base):
    __tablename__ = "brats_data_paths"
    id = Column(Integer, primary_key=True)
    path = Column(String(500), unique=True, nullable=False)
    label = Column(String(100))
    added_by = Column(Integer, ForeignKey("doctors.id"), nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)

# ─── 인증 ──────────────────────────────────────────────────────────────────────
security = HTTPBearer()


def verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode(), hashed.encode())


def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    payload = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    payload["exp"] = expire
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_doctor(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> Doctor:
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="인증 토큰이 유효하지 않습니다",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        doctor_id = payload.get("sub")
        if doctor_id is None:
            raise exc
    except JWTError:
        raise exc
    doctor = db.query(Doctor).filter(
        Doctor.id == int(doctor_id), Doctor.is_active == True
    ).first()
    if not doctor:
        raise exc
    return doctor


def _audit(db: Session, action: str, resource_type: str, resource_id: str,
           ip: str, doctor_id: Optional[int] = None):
    db.add(AuditLog(
        doctor_id=doctor_id, action=action,
        resource_type=resource_type, resource_id=str(resource_id), ip_address=ip,
    ))
    db.commit()
    logger.info("AUDIT doctor=%s action=%s %s:%s ip=%s", doctor_id, action, resource_type, resource_id, ip)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ─── Pydantic 스키마 ────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    email: str
    password: str


class PatientCreate(BaseModel):
    patient_code: str
    name: str
    age: Optional[int] = None
    gender: Optional[str] = None
    notes: Optional[str] = None


class DoctorCreate(BaseModel):
    name: str
    email: str
    password: str
    department: Optional[str] = None


# ─── FastAPI 앱 ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="뇌종양 MRI 분석 시스템",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# 프론트엔드 정적 파일 제공
FRONTEND_DIR = BASE_DIR.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


# ─── 시작 시 데모 계정 생성 ───────────────────────────────────────────────────
@app.on_event("startup")
def seed_demo_account():
    db = SessionLocal()
    try:
        if not db.query(Doctor).filter(Doctor.email == "demo@hospital.kr").first():
            demo = Doctor(
                name="테스트 의사",
                email="demo@hospital.kr",
                hashed_password=hash_password("demo1234"),
                department="신경외과",
            )
            db.add(demo)
            db.commit()
            logger.info("Demo account created: demo@hospital.kr / demo1234")
    finally:
        db.close()


# ─── 인증 라우트 ───────────────────────────────────────────────────────────────
@app.post("/api/auth/register", status_code=201)
def register(data: DoctorCreate, request: Request, db: Session = Depends(get_db)):
    if db.query(Doctor).filter(Doctor.email == data.email).first():
        raise HTTPException(400, "이미 등록된 이메일입니다")
    if len(data.password) < 8:
        raise HTTPException(400, "비밀번호는 8자 이상이어야 합니다")
    doctor = Doctor(
        name=data.name,
        email=data.email,
        hashed_password=hash_password(data.password),
        department=data.department,
    )
    db.add(doctor)
    db.commit()
    db.refresh(doctor)
    _audit(db, "register", "doctor", doctor.id, _client_ip(request), doctor.id)
    return {"message": "계정이 생성되었습니다", "id": doctor.id}


@app.post("/api/auth/login")
def login(data: LoginRequest, request: Request, db: Session = Depends(get_db)):
    doctor = db.query(Doctor).filter(Doctor.email == data.email).first()
    ip = _client_ip(request)
    if not doctor or not verify_password(data.password, doctor.hashed_password):
        _audit(db, "login_failed", "auth", data.email, ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "이메일 또는 비밀번호가 올바르지 않습니다")
    if not doctor.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "비활성화된 계정입니다")
    token = create_access_token({"sub": str(doctor.id)})
    _audit(db, "login_success", "auth", doctor.id, ip, doctor.id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "doctor": {
            "id": doctor.id, "name": doctor.name,
            "email": doctor.email, "department": doctor.department,
        },
    }


@app.get("/api/auth/me")
def get_me(current_doctor: Doctor = Depends(get_current_doctor)):
    return {
        "id": current_doctor.id,
        "name": current_doctor.name,
        "email": current_doctor.email,
        "department": current_doctor.department,
    }


# ─── 환자 라우트 ───────────────────────────────────────────────────────────────
@app.get("/api/patients")
def list_patients(
    current_doctor: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    patients = (
        db.query(Patient)
        .filter(Patient.doctor_id == current_doctor.id)
        .order_by(Patient.created_at.desc())
        .all()
    )
    result = []
    for p in patients:
        latest = (
            db.query(Analysis)
            .filter(Analysis.patient_id == p.id)
            .order_by(Analysis.created_at.desc())
            .first()
        )
        result.append({
            "id": p.id,
            "patient_code": p.patient_code,
            "name": p.name,
            "age": p.age,
            "gender": p.gender,
            "notes": p.notes,
            "created_at": p.created_at.isoformat(),
            "latest_analysis": {
                "id": latest.id,
                "status": latest.status,
                "probability": latest.probability,
                "created_at": latest.created_at.isoformat(),
            } if latest else None,
        })
    return result


@app.post("/api/patients", status_code=201)
def create_patient(
    data: PatientCreate,
    request: Request,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    patient = Patient(doctor_id=current_doctor.id, **data.model_dump())
    db.add(patient)
    db.commit()
    db.refresh(patient)
    _audit(db, "create_patient", "patient", patient.id, _client_ip(request), current_doctor.id)
    return {"id": patient.id, "patient_code": patient.patient_code, "name": patient.name}


@app.get("/api/patients/{patient_id}")
def get_patient(
    patient_id: int,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    patient = db.query(Patient).filter(
        Patient.id == patient_id,
        Patient.doctor_id == current_doctor.id,
    ).first()
    if not patient:
        raise HTTPException(404, "환자를 찾을 수 없습니다")

    return {
        "id": patient.id,
        "patient_code": patient.patient_code,
        "name": patient.name,
        "age": patient.age,
        "gender": patient.gender,
        "notes": patient.notes,
        "created_at": patient.created_at.isoformat(),
        "analyses": [
            {
                "id": a.id,
                "status": a.status,
                "probability": a.probability,
                "tumor_volume_cc": a.tumor_volume_cc,
                "tumor_centroid": a.tumor_centroid,
                "created_at": a.created_at.isoformat(),
            }
            for a in patient.analyses
        ],
    }


@app.delete("/api/patients/{patient_id}", status_code=204)
def delete_patient(
    patient_id: int,
    request: Request,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    patient = db.query(Patient).filter(
        Patient.id == patient_id,
        Patient.doctor_id == current_doctor.id,
    ).first()
    if not patient:
        raise HTTPException(404, "환자를 찾을 수 없습니다")
    _audit(db, "delete_patient", "patient", patient_id, _client_ip(request), current_doctor.id)
    db.delete(patient)
    db.commit()


# ─── 분석 라우트 ───────────────────────────────────────────────────────────────
@app.post("/api/patients/{patient_id}/analyze")
async def run_analysis(
    patient_id: int,
    request: Request,
    t1: UploadFile = File(..., description="T1 NII 또는 DICOM"),
    t1ce: UploadFile = File(..., description="T1ce NII 또는 DICOM"),
    t2: UploadFile = File(..., description="T2 NII 또는 DICOM"),
    flair: UploadFile = File(..., description="FLAIR NII 또는 DICOM"),
    current_doctor: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    patient = db.query(Patient).filter(
        Patient.id == patient_id,
        Patient.doctor_id == current_doctor.id,
    ).first()
    if not patient:
        raise HTTPException(404, "환자를 찾을 수 없습니다")

    analysis_id = str(uuid.uuid4())
    upload_path = UPLOAD_DIR / analysis_id
    result_path = RESULTS_DIR / analysis_id
    upload_path.mkdir(parents=True)
    result_path.mkdir(parents=True)

    analysis = Analysis(id=analysis_id, patient_id=patient_id, status="processing")
    db.add(analysis)
    db.commit()

    try:
        file_paths: dict = {}
        for modality, upload in [("t1", t1), ("t1ce", t1ce), ("t2", t2), ("flair", flair)]:
            suffix = Path(upload.filename or f"{modality}.nii.gz").suffix.lower()
            save_path = upload_path / f"{modality}{suffix}"
            with open(save_path, "wb") as f:
                shutil.copyfileobj(upload.file, f)
            file_paths[modality] = str(save_path)

        # preprocess_files → {"nii_paths": {...}, "volume": ndarray (4,240,240,80)}
        preprocessed = preprocess_files(file_paths, str(upload_path))
        inference    = run_inference(preprocessed)

        nii = preprocessed["nii_paths"]

        mask_path   = str(result_path / "tumor_mask.nii.gz")
        base_affine = nib.load(nii["t1"]).affine
        nib.save(nib.Nifti1Image(inference["mask"].astype(np.uint8), base_affine), mask_path)
        analysis.status           = "done"
        analysis.probability      = round(float(inference["probability"]), 4)
        analysis.tumor_volume_cc  = round(float(inference["tumor_volume_cc"]), 2)
        analysis.tumor_centroid   = inference["tumor_centroid"]
        analysis.nii_t1_path      = nii["t1"]
        analysis.nii_t1ce_path    = nii["t1ce"]
        analysis.nii_t2_path      = nii["t2"]
        analysis.nii_flair_path   = nii["flair"]
        analysis.mask_path        = mask_path
        db.commit()

        _audit(db, "run_analysis", "analysis", analysis_id, _client_ip(request), current_doctor.id)
        return {
            "analysis_id": analysis_id,
            "status": "done",
            "probability": analysis.probability,
            "tumor_volume_cc": analysis.tumor_volume_cc,
            "tumor_centroid": analysis.tumor_centroid,
        }

    except Exception as e:
        logger.exception("Analysis %s failed", analysis_id)
        analysis.status = "failed"
        analysis.error_message = str(e)
        db.commit()
        raise HTTPException(500, f"분석 중 오류가 발생했습니다: {e}")


@app.get("/api/analysis/{analysis_id}")
def get_analysis(
    analysis_id: str,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(404, "분석 결과를 찾을 수 없습니다")
    if analysis.patient.doctor_id != current_doctor.id:
        raise HTTPException(403, "접근 권한이 없습니다")

    # XAI 리포트: 마스크 파일이 있으면 즉석 계산
    xai_report = None
    if analysis.status == "done" and analysis.mask_path and os.path.exists(analysis.mask_path):
        try:
            mask = nib.load(analysis.mask_path).get_fdata(dtype=np.float32).astype(np.uint8)
            xai_report = compute_xai_report(
                mask,
                analysis.tumor_centroid,
                float(analysis.probability),
            )
        except Exception:
            logger.warning("XAI 리포트 생성 실패 (analysis=%s)", analysis_id)

    return {
        "id": analysis.id,
        "patient_id": analysis.patient_id,
        "status": analysis.status,
        "probability": analysis.probability,
        "tumor_volume_cc": analysis.tumor_volume_cc,
        "tumor_centroid": analysis.tumor_centroid,
        "created_at": analysis.created_at.isoformat(),
        "has_mask": bool(analysis.mask_path),
        "has_nii": bool(analysis.nii_t1_path),
        "error_message": analysis.error_message,
        "xai": xai_report,
    }


@app.delete("/api/analysis/{analysis_id}", status_code=204)
def delete_analysis(
    analysis_id: str,
    request: Request,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    """분석 결과 및 관련 파일 삭제"""
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(404, "분석 결과를 찾을 수 없습니다")
    if analysis.patient.doctor_id != current_doctor.id:
        raise HTTPException(403, "접근 권한이 없습니다")

    # 업로드·결과 파일 삭제
    for d in [UPLOAD_DIR / analysis_id, RESULTS_DIR / analysis_id]:
        if d.exists():
            shutil.rmtree(d)

    _audit(db, "delete_analysis", "analysis", analysis_id, _client_ip(request), current_doctor.id)
    db.delete(analysis)
    db.commit()


@app.get("/api/analysis/{analysis_id}/ai-explain")
def get_ai_explanation(
    analysis_id: str,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    """
    Claude AI 기반 의사용·환자용 설명 생성 (결과 파일에 캐싱).
    Returns: {doctor: str, patient: str, xai: dict}
    """
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(404, "분석 결과를 찾을 수 없습니다")
    if analysis.patient.doctor_id != current_doctor.id:
        raise HTTPException(403, "접근 권한이 없습니다")
    if analysis.status != "done":
        raise HTTPException(400, "분석이 완료되지 않았습니다")
    if not analysis.mask_path or not os.path.exists(analysis.mask_path):
        raise HTTPException(404, "마스크 파일이 없어 설명을 생성할 수 없습니다")

    cache_path = RESULTS_DIR / analysis_id / "ai_explain.json"

    if cache_path.exists():
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    mask = nib.load(analysis.mask_path).get_fdata(dtype=np.float32).astype(np.uint8)
    xai  = compute_xai_report(mask, analysis.tumor_centroid, float(analysis.probability))
    explanations = generate_ai_explanations(xai, float(analysis.probability))

    result = {**explanations, "xai": xai}

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    _audit(db, "ai_explain", "analysis", analysis_id, "system", current_doctor.id)
    return result


@app.get("/api/analysis/{analysis_id}/raw/{modality}")
def get_analysis_raw_file(
    analysis_id: str,
    modality: str,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    """원본 업로드 파일 반환 (전처리 전) — 시각화용"""
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis or analysis.patient.doctor_id != current_doctor.id:
        raise HTTPException(403, "접근 권한이 없습니다")

    if modality not in {"t1", "t1ce", "t2", "flair"}:
        raise HTTPException(400, "잘못된 모달리티입니다")

    upload_dir = UPLOAD_DIR / analysis_id
    for ext in [".nii.gz", ".nii", ".dcm"]:
        candidate = upload_dir / f"{modality}{ext}"
        if candidate.exists():
            return FileResponse(
                str(candidate),
                media_type="application/octet-stream",
                filename=f"{analysis_id}_{modality}_raw.nii.gz",
            )

    raise HTTPException(404, "원본 파일을 찾을 수 없습니다")


@app.get("/api/analysis/{analysis_id}/file/{modality}")
def get_analysis_file(
    analysis_id: str,
    modality: str,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    """
    modality: t1 | t1ce | t2 | flair | mask
    NIfTI 파일을 직접 반환합니다. NiiVue에서 blob URL로 로드하세요.
    """
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis or analysis.patient.doctor_id != current_doctor.id:
        raise HTTPException(403, "접근 권한이 없습니다")

    path_map = {
        "t1": analysis.nii_t1_path,
        "t1ce": analysis.nii_t1ce_path,
        "t2": analysis.nii_t2_path,
        "flair": analysis.nii_flair_path,
        "mask": analysis.mask_path,
    }
    file_path = path_map.get(modality)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(404, "파일을 찾을 수 없습니다")

    return FileResponse(
        file_path,
        media_type="application/octet-stream",
        filename=f"{analysis_id}_{modality}.nii.gz",
    )


# ─── BraTS 데이터 탐색 라우트 ───────────────────────────────────────────────────
BRATS_MODALITIES = {"t1", "t1ce", "t2", "flair", "seg"}


def _scan_brats_dir(base: Path) -> list:
    """BraTS 폴더 하나를 스캔해서 환자 목록 반환"""
    if not base.exists():
        return []
    patients = []
    for d in sorted(base.iterdir()):
        if not d.is_dir() or not d.name.startswith("BraTS"):
            continue
        has_seg = (d / f"{d.name}_seg.nii").exists() or (d / f"{d.name}_seg.nii.gz").exists()
        patients.append({"id": d.name, "has_seg": has_seg, "source": str(base)})
    return patients


@app.get("/api/brats/patients")
def list_brats_patients(
    current_doctor: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    """기본 경로 + 추가 등록 경로의 BraTS 환자 목록 반환"""
    patients = _scan_brats_dir(BRATS_DATA_PATH)

    extra_paths = db.query(BratsDataPath).all()
    seen_ids = {p["id"] for p in patients}
    for ep in extra_paths:
        for p in _scan_brats_dir(Path(ep.path)):
            if p["id"] not in seen_ids:
                patients.append(p)
                seen_ids.add(p["id"])

    return patients


@app.get("/api/brats/paths")
def list_brats_paths(
    current_doctor: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    """등록된 추가 BraTS 경로 목록"""
    paths = db.query(BratsDataPath).all()
    result = [{"id": p.id, "path": p.path, "label": p.label, "added_at": p.added_at.isoformat()} for p in paths]
    default = {"id": None, "path": str(BRATS_DATA_PATH), "label": "기본 경로", "added_at": None}
    return [default] + result


class BratsPathCreate(BaseModel):
    path: str
    label: Optional[str] = None


@app.post("/api/brats/paths", status_code=201)
def add_brats_path(
    data: BratsPathCreate,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    """새 BraTS 데이터 경로 등록"""
    target = Path(data.path)
    if not target.exists() or not target.is_dir():
        raise HTTPException(400, "존재하지 않는 폴더 경로입니다")
    if any(c in data.path for c in ["..", ";"]):
        raise HTTPException(400, "잘못된 경로입니다")
    if db.query(BratsDataPath).filter(BratsDataPath.path == data.path).first():
        raise HTTPException(400, "이미 등록된 경로입니다")

    entry = BratsDataPath(path=data.path, label=data.label or data.path, added_by=current_doctor.id)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"id": entry.id, "path": entry.path, "label": entry.label}


@app.delete("/api/brats/paths/{path_id}", status_code=204)
def delete_brats_path(
    path_id: int,
    current_doctor: Doctor = Depends(get_current_doctor),
    db: Session = Depends(get_db),
):
    """등록된 BraTS 경로 삭제"""
    entry = db.query(BratsDataPath).filter(BratsDataPath.id == path_id).first()
    if not entry:
        raise HTTPException(404, "경로를 찾을 수 없습니다")
    db.delete(entry)
    db.commit()


@app.get("/api/brats/{patient_id}/file/{modality}")
def get_brats_file(
    patient_id: str,
    modality: str,
    current_doctor: Doctor = Depends(get_current_doctor),
):
    """BraTS 원본 NIfTI 파일 반환 — NiiVue에서 blob URL로 로드"""
    # 경로 탐색 공격 방지
    if any(c in patient_id for c in ["..", "/", "\\"]):
        raise HTTPException(400, "잘못된 환자 ID입니다")
    if modality not in BRATS_MODALITIES:
        raise HTTPException(400, f"지원하지 않는 모달리티: {modality}")

    patient_dir = BRATS_DATA_PATH / patient_id
    if not patient_dir.exists():
        raise HTTPException(404, "환자 폴더를 찾을 수 없습니다")

    for ext in [".nii", ".nii.gz"]:
        fp = patient_dir / f"{patient_id}_{modality}{ext}"
        if fp.exists():
            return FileResponse(str(fp), media_type="application/octet-stream",
                                filename=fp.name)

    raise HTTPException(404, f"{modality} 파일을 찾을 수 없습니다")


@app.get("/api/brats/{patient_id}/info")
def get_brats_patient_info(
    patient_id: str,
    current_doctor: Doctor = Depends(get_current_doctor),
):
    """환자별 보유 모달리티 목록 반환"""
    if any(c in patient_id for c in ["..", "/", "\\"]):
        raise HTTPException(400, "잘못된 환자 ID입니다")

    patient_dir = BRATS_DATA_PATH / patient_id
    if not patient_dir.exists():
        raise HTTPException(404, "환자 폴더를 찾을 수 없습니다")

    available = {}
    for mod in BRATS_MODALITIES:
        for ext in [".nii", ".nii.gz"]:
            if (patient_dir / f"{patient_id}_{mod}{ext}").exists():
                available[mod] = True
                break
        else:
            available[mod] = False

    return {"id": patient_id, "modalities": available}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
