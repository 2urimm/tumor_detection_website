"""
★ 팀원용 모델 서버 템플릿 ★

이 파일을 팀원 PC에 복사하고 your_model_inference() 함수에
실제 모델 추론 코드를 채워넣은 뒤 실행하면 됩니다.

실행:
    pip install fastapi uvicorn numpy httpx
    python model_server_template.py

접속 확인 (우리 쪽에서):
    http://<팀원-Tailscale-IP>:5000/health

─────────────────────────────────────────────────────────────
프로토콜 요약
─────────────────────────────────────────────────────────────
  요청: POST /predict
        multipart/form-data, field name="volume"
        파일: numpy .npy  shape=(4, 240, 240, 155) dtype=float32
              채널 순서: [flair, t1, t1ce, t2]  (Z-score 정규화 완료)

  응답: JSON
        {
          "mask_b64":        str,           # base64(mask.tobytes())
          "mask_shape":      [240, 240, 155],
          "mask_dtype":      "uint8",
          "probability":     float,         # 종양 존재 확률 0.0~1.0
          "tumor_volume_cc": float,         # 종양 체적 (cc)
          "tumor_centroid":  {"x": float, "y": float, "z": float}
        }

  마스크 레이블 (BraTS 규격):
        0 = 배경
        1 = 부종 (edema)
        2 = 괴사 (necrotic core)
        3 = 활성 종양 (enhancing tumor)
─────────────────────────────────────────────────────────────
"""

import base64
import io
import logging
import socket

import numpy as np
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

# ══════════════════════════════════════════════════════════════
# ★ 팀원이 수정할 부분 1: 모델 로드
# ══════════════════════════════════════════════════════════════
# 예시 (PyTorch):
#
# import torch
# from your_model_module import YourSegModel
#
# DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
# model  = YourSegModel(in_channels=4, num_classes=4)
# model.load_state_dict(torch.load("weights/best.pth", map_location=DEVICE))
# model.eval()
# print(f"모델 로드 완료 — device: {DEVICE}")
# ══════════════════════════════════════════════════════════════

PORT = 5000
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Brain Tumor Segmentation Server", version="1.0")


def your_model_inference(volume: np.ndarray) -> dict:
    """
    ══════════════════════════════════════════════════════════════
    ★ 팀원이 수정할 부분 2: 실제 추론 로직
    ══════════════════════════════════════════════════════════════

    입력:
        volume: np.ndarray
                shape = (4, 240, 240, 155)   dtype = float32
                채널   = [flair, t1, t1ce, t2]
                값     = Z-score 정규화 완료 (뇌 마스크 내부만)

    반환:
        dict with keys:
            "mask"            np.ndarray (240, 240, 155) uint8  레이블 0~3
            "probability"     float  0.0 ~ 1.0
            "tumor_volume_cc" float  (cc 단위)
            "tumor_centroid"  {"x": float, "y": float, "z": float}

    ──────────────────────────────────────────────────────────────
    예시 코드 (PyTorch U-Net 계열):

        with torch.no_grad():
            inp    = torch.from_numpy(volume).unsqueeze(0).float().to(DEVICE)
                     # shape: (1, 4, 240, 240, 155)
            logits = model(inp)
                     # shape: (1, 4, 240, 240, 155) — 클래스 수에 따라 다름
            pred   = logits.argmax(dim=1).squeeze(0).cpu().numpy()
                     # shape: (240, 240, 155)
            mask   = pred.astype(np.uint8)

        # 확률: enhancing tumor softmax 최댓값 또는 sigmoid 출력
        probs       = torch.softmax(logits, dim=1)
        probability = float(probs[0, 3].max().cpu())   # 클래스 3 (활성종양)

    ──────────────────────────────────────────────────────────────
    """
    # TODO: 아래 raise를 지우고 실제 코드를 작성하세요
    raise NotImplementedError("your_model_inference()에 실제 모델 코드를 작성하세요")


def _compute_stats(mask: np.ndarray) -> tuple[float, float, dict]:
    """mask에서 확률·체적·중심좌표 계산 (보조 함수, 필요 시 사용)"""
    tumor_voxels   = int(np.sum(mask > 0))
    volume_cc      = round(tumor_voxels / 1000.0, 2)  # 1 voxel = 1mm³ → /1000 = cc

    if tumor_voxels > 0:
        coords   = np.where(mask > 0)
        centroid = {
            "x": round(float(np.mean(coords[0])), 1),
            "y": round(float(np.mean(coords[1])), 1),
            "z": round(float(np.mean(coords[2])), 1),
        }
    else:
        centroid = {"x": 0.0, "y": 0.0, "z": 0.0}

    return volume_cc, centroid


# ── API 엔드포인트 ────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """연결 확인용"""
    return {"status": "ok"}


@app.post("/predict")
async def predict(
    volume: UploadFile = File(..., description=".npy 파일 (4, 240, 240, 155) float32"),
):
    """MRI 볼륨을 받아 종양 세그멘테이션 결과를 반환합니다."""
    try:
        raw = await volume.read()
        arr = np.load(io.BytesIO(raw))
        logger.info("수신 volume — shape=%s  dtype=%s", arr.shape, arr.dtype)

        if arr.shape != (4, 240, 240, 155):
            raise HTTPException(
                400,
                f"잘못된 shape: {arr.shape}  (기대값: (4, 240, 240, 155))",
            )

        result = your_model_inference(arr.astype(np.float32))

        mask: np.ndarray = result["mask"].astype(np.uint8)
        if mask.shape != (240, 240, 155):
            raise ValueError(f"모델이 반환한 mask shape 오류: {mask.shape}")

        mask_b64 = base64.b64encode(mask.tobytes()).decode()

        logger.info(
            "추론 완료 — prob=%.3f  vol=%.2f cc",
            result["probability"], result["tumor_volume_cc"],
        )

        return JSONResponse({
            "mask_b64":        mask_b64,
            "mask_shape":      list(mask.shape),
            "mask_dtype":      "uint8",
            "probability":     float(result["probability"]),
            "tumor_volume_cc": float(result["tumor_volume_cc"]),
            "tumor_centroid":  result["tumor_centroid"],
        })

    except NotImplementedError as e:
        raise HTTPException(501, f"모델 미구현: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("추론 중 오류")
        raise HTTPException(500, f"추론 실패: {e}")


# ── 실행 ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            addr = info[4][0]
            if ":" not in addr and addr != "127.0.0.1":
                ips.append(addr)
    except Exception:
        pass
    ips = list(set(ips))

    print(f"\n{'='*55}")
    print("  뇌종양 모델 서버 실행 중")
    print(f"{'='*55}")
    for ip in ips:
        print(f"  접속: http://{ip}:{PORT}")
    print(f"\n  상대방 .env 설정:")
    tailscale_ips = [ip for ip in ips if ip.startswith("100.")]
    if tailscale_ips:
        print(f"  MODEL_SERVER_URL=http://{tailscale_ips[0]}:{PORT}")
    else:
        print(f"  MODEL_SERVER_URL=http://<Tailscale-IP>:{PORT}")
    print(f"{'='*55}\n")

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
