"""
실제 모델 서버 클라이언트.

팀원의 Tailscale 서버에 추론 요청을 보냅니다.

설정 방법:
  backend/.env 파일에 아래 항목 추가:
    MODEL_SERVER_URL=http://<팀원-Tailscale-IP>:8888

  연결 불가 시 자동으로 Mock 모드로 fallback됩니다.
  fallback을 끄려면: MODEL_MOCK_FALLBACK=false

입력:  preprocessed["volume"] -- np.ndarray (4, 240, 240, D)  (D 슬라이스 전체 전달)
출력:  {
         "mask":            np.ndarray (240, 240, D) uint8  0=배경,1=부종,2=괴사,3=활성종양
         "probability":     float  0~1
         "tumor_volume_cc": float  (cc)
         "tumor_centroid":  {"x": float, "y": float, "z": float}
       }
"""

import base64
import io
import logging
import os

import httpx
import numpy as np
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# -- 환경변수 -----------------------------------------------------------------
MODEL_SERVER_URL  = os.getenv("MODEL_SERVER_URL", "").rstrip("/")
TIMEOUT_SEC       = int(os.getenv("MODEL_TIMEOUT", "300"))
USE_MOCK_FALLBACK = os.getenv("MODEL_MOCK_FALLBACK", "true").lower() == "true"


# -- fallback -----------------------------------------------------------------
def _fallback(volume: np.ndarray) -> dict:
    from mock_model import run_inference as _mock
    logger.warning("[ModelClient] Mock 모드로 실행 중 (실제 모델 미연결)")
    return _mock({"volume": volume})


# -- 메인 ---------------------------------------------------------------------
def run_inference(preprocessed: dict) -> dict:
    volume: np.ndarray = preprocessed["volume"]   # (4, H, W, D)

    if not MODEL_SERVER_URL:
        logger.warning("[ModelClient] MODEL_SERVER_URL 미설정 -> Mock 모드")
        return _fallback(volume)

    buf = io.BytesIO()
    np.save(buf, volume.astype(np.float32))
    buf.seek(0)

    try:
        logger.info(
            "[ModelClient] 추론 요청 -> %s/predict  shape=%s",
            MODEL_SERVER_URL, volume.shape,
        )

        with httpx.Client(timeout=httpx.Timeout(TIMEOUT_SEC)) as client:
            resp = client.post(
                f"{MODEL_SERVER_URL}/predict",
                files={"volume": ("volume.npy", buf, "application/octet-stream")},
            )
        resp.raise_for_status()
        data = resp.json()

        mask_bytes = base64.b64decode(data["mask_b64"])
        mask_shape = tuple(data["mask_shape"])
        mask_dtype = data.get("mask_dtype", "uint8")
        mask = np.frombuffer(mask_bytes, dtype=mask_dtype).reshape(mask_shape).copy()

        centroid = dict(data["tumor_centroid"])

        logger.info(
            "[ModelClient] 추론 완료 -- prob=%.3f  vol=%.2f cc",
            data["probability"], data["tumor_volume_cc"],
        )

        return {
            "mask":            mask,
            "probability":     float(data["probability"]),
            "tumor_volume_cc": float(data["tumor_volume_cc"]),
            "tumor_centroid":  centroid,
        }

    except httpx.ConnectError as e:
        logger.error("[ModelClient] 서버 연결 실패: %s", e)
        if USE_MOCK_FALLBACK:
            return _fallback(volume)
        raise RuntimeError(f"모델 서버에 연결할 수 없습니다 ({MODEL_SERVER_URL}): {e}") from e

    except httpx.TimeoutException:
        logger.error("[ModelClient] 추론 타임아웃 (%d초)", TIMEOUT_SEC)
        if USE_MOCK_FALLBACK:
            return _fallback(volume)
        raise RuntimeError(f"모델 추론 타임아웃 ({TIMEOUT_SEC}초)")

    except httpx.HTTPStatusError as e:
        logger.error("[ModelClient] 서버 오류 %s: %s", e.response.status_code, e.response.text)
        if USE_MOCK_FALLBACK:
            return _fallback(volume)
        raise RuntimeError(f"모델 서버 오류 ({e.response.status_code}): {e.response.text}") from e

    except Exception as e:
        logger.error("[ModelClient] 예외 발생: %s", e, exc_info=True)
        if USE_MOCK_FALLBACK:
            return _fallback(volume)
        raise
