"""
BraTS2020 전처리 파이프라인

처리 순서:
  1. DICOM → NIfTI 변환 (필요 시)
  2. 음수 클리핑: clip(0, None)
  3. 이상치 제거: clip(0, 99th percentile)
  4. Z-score 정규화: 뇌 영역(>0)만 적용
  5. 4채널 스택: (4, H, W, D) — 순서: flair, t1, t1ce, t2

슬라이싱 없이 원본 전체 z범위를 사용합니다.

반환:
  nii_paths : 뷰어용 NIfTI 경로 dict
  volume    : 모델 입력용 numpy (4, H, W, D)
"""

import os
import numpy as np
import nibabel as nib
import pydicom
from pathlib import Path
from typing import Dict

MODALITIES = ["flair", "t1", "t1ce", "t2"]


def dicom_to_nii(dicom_path: str, output_path: str) -> str:
    """단일 DICOM 파일 → NIfTI 변환"""
    ds = pydicom.dcmread(dicom_path)
    pixel_array = ds.pixel_array.astype(np.float32)
    if pixel_array.ndim == 2:
        pixel_array = pixel_array[:, :, np.newaxis]

    affine = np.eye(4)
    if hasattr(ds, "PixelSpacing"):
        affine[0, 0] = float(ds.PixelSpacing[0])
        affine[1, 1] = float(ds.PixelSpacing[1])
    if hasattr(ds, "SliceThickness"):
        affine[2, 2] = float(ds.SliceThickness)

    nib.save(nib.Nifti1Image(pixel_array, affine), output_path)
    return output_path


def _preprocess_volume(data: np.ndarray) -> np.ndarray:
    """단일 모달리티 전처리 — 전체 z범위, 슬라이싱 없음"""
    # 1. 음수 제거
    data = np.clip(data, 0, None)

    # 2. 이상치 제거 (상위 1% 클리핑)
    p99 = np.percentile(data, 99)
    data = np.clip(data, 0, p99)

    # 3. Z-score 정규화 — 뇌 영역(>0)만
    brain = data > 0
    if brain.sum() > 0:
        mean = data[brain].mean()
        std  = data[brain].std()
        data[brain] = (data[brain] - mean) / (std + 1e-8)

    return data.astype(np.float32)


def preprocess_files(file_paths: Dict[str, str], output_dir: str) -> dict:
    """
    Args:
        file_paths: {"t1": path, "t1ce": path, "t2": path, "flair": path}
                    .nii / .nii.gz / .dcm 모두 지원
        output_dir: 전처리 결과 저장 폴더

    Returns:
        {
            "nii_paths": {"flair": ..., "t1": ..., "t1ce": ..., "t2": ...},
            "volume":    np.ndarray shape (4, H, W, D)  ← 모델 입력
        }
    """
    volumes   = []
    nii_paths = {}

    for mod in MODALITIES:
        path   = file_paths[mod]
        suffix = Path(path).suffix.lower()

        # DICOM 변환
        if suffix == ".dcm":
            converted = os.path.join(output_dir, f"{mod}_converted.nii.gz")
            path = dicom_to_nii(path, converted)

        img  = nib.load(path)
        data = img.get_fdata(dtype=np.float32)

        # 4D NIfTI → 첫 번째 볼륨만
        if data.ndim == 4:
            data = data[..., 0]

        data = _preprocess_volume(data)

        # 뷰어 + 모델 모두 동일한 볼륨 사용
        nii_path = os.path.join(output_dir, f"{mod}_processed.nii.gz")
        nib.save(nib.Nifti1Image(data, img.affine), nii_path)
        nii_paths[mod] = nii_path

        volumes.append(data)

    volume_4ch = np.stack(volumes, axis=0).astype(np.float32)

    return {
        "nii_paths": nii_paths,
        "volume":    volume_4ch,   # (4, H, W, D)
    }
