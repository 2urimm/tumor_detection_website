"""
테스트용 경량 모의 모델.
팀원 모델 서버 연결 시 run_inference() 내부만 교체하면 됩니다.

실제 모델 연결 예시:
    import httpx
    resp = httpx.post(MODEL_SERVER_URL + "/predict",
                      data={"volume": preprocessed["volume"].tobytes()},
                      timeout=300)
    return resp.json()

입력:  preprocessed["volume"] — np.ndarray (4, 240, 240, 80)
출력:  {
         "mask":           np.ndarray (240, 240, 80) uint8, 레이블 0~3
                           0=배경, 1=부종, 2=괴사, 3=활성종양  (BraTS 규격)
         "probability":    float  0~1
         "tumor_volume_cc": float
         "tumor_centroid": {"x": float, "y": float, "z": float}
       }
"""

import numpy as np


def run_inference(preprocessed: dict) -> dict:
    volume = preprocessed["volume"]          # (4, 240, 240, 80)
    shape  = volume.shape[1:]                # (240, 240, 80)

    # ── 가상 구형 종양 마스크 (BraTS 레이블 규격) ─────────────────────────────
    mask = np.zeros(shape, dtype=np.uint8)
    cx   = shape[0] // 2 + 10   # 240 기준 중심 근처
    cy   = shape[1] // 2 - 8
    cz   = shape[2] // 2 + 3    # 80 기준

    x, y, z = np.ogrid[: shape[0], : shape[1], : shape[2]]
    dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2)

    mask[dist <= 6]                       = 3   # 활성종양 (enhancing tumor)
    mask[(dist > 6)  & (dist <= 11)]      = 2   # 괴사 (necrotic core)
    mask[(dist > 11) & (dist <= 18)]      = 1   # 부종 (edema)

    # 약간의 노이즈로 경계를 자연스럽게
    rng      = np.random.default_rng(seed=42)
    boundary = (dist > 9) & (dist <= 20)
    flip     = rng.random(shape) > 0.88
    mask[boundary & flip & (mask > 0)] = 0

    # ── 정량 지표 ────────────────────────────────────────────────────────────
    tumor_voxels   = int(np.sum(mask > 0))
    # BraTS 원본 voxel 크기 1×1×1 mm³ 가정 → cc = voxels / 1000
    tumor_volume_cc = round(tumor_voxels / 1000.0, 2)

    coords   = np.where(mask > 0)
    centroid = {
        "x": round(float(np.mean(coords[0])), 1),
        "y": round(float(np.mean(coords[1])), 1),
        "z": round(float(np.mean(coords[2])), 1),
    }

    probability = float(rng.uniform(0.83, 0.97))

    return {
        "mask":            mask,
        "probability":     probability,
        "tumor_volume_cc": tumor_volume_cc,
        "tumor_centroid":  centroid,
    }
