"""
설명 가능한 AI (XAI) 리포트 생성

마스크 레이블 (BraTS 규격):
  0 = 배경
  1 = 부종       (Peritumoral Edema)
  2 = 괴사       (Necrotic Core)
  3 = 활성 종양  (Enhancing Tumor)
"""

import logging
import os

import numpy as np

logger = logging.getLogger(__name__)


VOXEL_TO_CC = 1 / 1000.0   # 1 voxel = 1 mm³


def _cc(voxels: int) -> float:
    return round(voxels * VOXEL_TO_CC, 2)


def _pct(part: int, total: int) -> float:
    return round(part / total * 100, 1) if total > 0 else 0.0


def _location(centroid: dict, shape: tuple) -> dict:
    """복셀 중심좌표 → 해부학적 위치 해석"""
    H, W, D = shape
    cx, cy, cz = centroid["x"], centroid["y"], centroid["z"]

    hemisphere = "우측" if cx > W / 2 else "좌측"

    if cy < H * 0.35:
        region = "전두부"
    elif cy > H * 0.65:
        region = "후두부"
    else:
        region = "두정·측두부"

    if cz > D * 0.65:
        level = "상부"
    elif cz < D * 0.35:
        level = "하부"
    else:
        level = "중간부"

    return {
        "hemisphere": hemisphere,
        "region":     region,
        "level":      level,
        "full":       f"{hemisphere} {region} {level}",
    }


def _risk(probability: float, enhancing_cc: float) -> dict:
    """확률 + 활성 종양 체적 → 위험도"""
    if probability >= 0.8 and enhancing_cc >= 3.0:
        return {
            "level":  "고위험",
            "color":  "danger",
            "reason": f"종양 확률 {round(probability*100,1)}%, 활성 종양 {enhancing_cc} cc — 즉각 정밀 검사 권고",
        }
    if probability >= 0.5 or enhancing_cc >= 1.0:
        return {
            "level":  "중위험",
            "color":  "warning",
            "reason": "종양 가능성 있음 — 추적 관찰 또는 추가 검사 권고",
        }
    return {
        "level":  "저위험",
        "color":  "success",
        "reason": "현재 종양 가능성 낮음 — 정기 모니터링 유지",
    }


def _describe(probability: float, location: dict, subtypes: dict, risk: dict) -> str:
    """자연어 소견 생성"""
    total_cc     = subtypes["total"]["volume_cc"]
    edema_cc     = subtypes["edema"]["volume_cc"]
    necrotic_cc  = subtypes["necrotic"]["volume_cc"]
    enhancing_cc = subtypes["enhancing"]["volume_cc"]
    edema_pct    = subtypes["edema"]["ratio_pct"]
    necrotic_pct = subtypes["necrotic"]["ratio_pct"]
    enhancing_pct= subtypes["enhancing"]["ratio_pct"]

    if total_cc == 0:
        return "분석 결과 종양 영역이 감지되지 않았습니다."

    lines = [
        f"모델은 {location['full']} 영역에서 종양 가능성을 "
        f"{round(probability * 100, 1)}%로 추정하였습니다.",

        f"감지된 전체 병변 체적은 {total_cc} cc입니다.",
    ]

    if enhancing_cc > 0:
        lines.append(
            f"이 중 현재 활발히 증식 중인 활성 종양(Enhancing Tumor)이 "
            f"{enhancing_cc} cc({enhancing_pct}%)로, 치료의 핵심 타깃 영역입니다."
        )
    if necrotic_cc > 0:
        lines.append(
            f"세포 사멸이 진행된 괴사 영역(Necrotic Core)은 "
            f"{necrotic_cc} cc({necrotic_pct}%)입니다."
        )
    if edema_cc > 0:
        lines.append(
            f"종양 주변 부종(Peritumoral Edema)은 {edema_cc} cc({edema_pct}%)로, "
            f"주변 뇌 조직에 압력을 줄 수 있습니다."
        )

    lines.append(f"종합 판정: [{risk['level']}] — {risk['reason']}.")

    return " ".join(lines)


def compute_xai_report(mask: np.ndarray, centroid: dict, probability: float) -> dict:
    """
    Args:
        mask:        np.ndarray (H, W, D) uint8, 레이블 0~3
        centroid:    {"x": float, "y": float, "z": float}
        probability: float 0~1

    Returns:
        {
            subtypes:    서브타입별 체적·비율
            location:    해부학적 위치
            risk:        위험도
            description: 자연어 소견
        }
    """
    edema_v     = int(np.sum(mask == 1))
    necrotic_v  = int(np.sum(mask == 2))
    enhancing_v = int(np.sum(mask == 3))
    total_v     = edema_v + necrotic_v + enhancing_v

    edema_cc     = _cc(edema_v)
    necrotic_cc  = _cc(necrotic_v)
    enhancing_cc = _cc(enhancing_v)
    total_cc     = _cc(total_v)

    subtypes = {
        "edema":     {"volume_cc": edema_cc,     "ratio_pct": _pct(edema_v,     total_v)},
        "necrotic":  {"volume_cc": necrotic_cc,  "ratio_pct": _pct(necrotic_v,  total_v)},
        "enhancing": {"volume_cc": enhancing_cc, "ratio_pct": _pct(enhancing_v, total_v)},
        "total":     {"volume_cc": total_cc},
    }

    location    = _location(centroid, mask.shape)
    risk        = _risk(probability, enhancing_cc)
    description = _describe(probability, location, subtypes, risk)

    return {
        "subtypes":    subtypes,
        "location":    location,
        "risk":        risk,
        "description": description,
    }


# ── Claude AI 설명 생성 ────────────────────────────────────────────────────────

def generate_ai_explanations(xai: dict, probability: float) -> dict:
    """
    Claude API를 사용해 의사용·환자용 설명 생성.
    ANTHROPIC_API_KEY 미설정 시 규칙 기반 fallback 반환.

    Returns:
        {"doctor": str, "patient": str}
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key or api_key.startswith("sk-ant-$"):
        return _rule_based_explanations(xai, probability)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        loc       = xai["location"]["full"]
        risk_lv   = xai["risk"]["level"]
        risk_rsn  = xai["risk"]["reason"]
        total_cc  = xai["subtypes"]["total"]["volume_cc"]
        enh       = xai["subtypes"]["enhancing"]
        necr      = xai["subtypes"]["necrotic"]
        edema     = xai["subtypes"]["edema"]
        prob_pct  = round(probability * 100, 1)

        doctor_text = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=900,
            messages=[{"role": "user", "content": _doctor_prompt(
                prob_pct, loc, risk_lv, risk_rsn, total_cc, enh, necr, edema
            )}],
        ).content[0].text

        patient_text = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": _patient_prompt(prob_pct, risk_lv, loc)}],
        ).content[0].text

        return {"doctor": doctor_text, "patient": patient_text}

    except Exception as exc:
        logger.warning("AI 설명 생성 실패: %s", exc)
        return _rule_based_explanations(xai, probability)


def _doctor_prompt(prob_pct, loc, risk_lv, risk_rsn, total_cc, enh, necr, edema) -> str:
    return f"""신경방사선과 전문의로서 다음 뇌 MRI AI 분석 결과에 대한 임상 소견을 작성하세요.

[AI 분석 데이터]
종양 가능성: {prob_pct}% | 위치: {loc} | 위험도: {risk_lv} ({risk_rsn})
전체 병변: {total_cc} cc
  · 활성 종양(Enhancing Tumor): {enh['volume_cc']} cc ({enh['ratio_pct']}%)
  · 괴사 핵(Necrotic Core): {necr['volume_cc']} cc ({necr['ratio_pct']}%)
  · 주변 부종(Peritumoral Edema): {edema['volume_cc']} cc ({edema['ratio_pct']}%)

아래 4개 항목을 각 2~3문장으로 작성하세요.

**[판단 근거]**
AI가 이 부위를 이상 영역으로 판단한 영상학적 특성 및 근거를 설명하세요.

**[위치 및 기능적 의미]**
해당 위치의 임상적 의미와 영향받을 수 있는 신경학적 기능을 설명하세요.

**[종양 구성 분석]**
구성 비율(활성 종양/괴사/부종)을 바탕으로 종양의 생물학적 특성을 설명하세요.

**[위험도 및 임상 권고]**
종합 위험도 평가와 즉각적인 임상 권고사항을 작성하세요."""


def _patient_prompt(prob_pct, risk_lv, loc) -> str:
    return f"""환자와 보호자에게 뇌 MRI 검사 결과를 설명하는 안내문을 작성하세요.

[참고 데이터 — 직접 언급 금지]
이상 소견 가능성: {prob_pct}%, 위험도: {risk_lv}, 부위: {loc}

작성 규칙:
- 일반인이 이해하는 쉬운 언어만 사용하세요
- "종양", "암", "괴사", "고위험", "저위험", 구체적 수치/퍼센트 사용 금지
- "이상 소견", "확인이 필요한 부분" 같은 중립적 표현을 사용하세요
- 담당 의사와 함께 다음 단계를 진행한다는 안내를 포함하세요
- 4~5문장, 따뜻하고 공감하는 어조로 작성하세요"""


def _rule_based_explanations(xai: dict, probability: float) -> dict:
    """Claude API 미사용 시 규칙 기반 설명 (fallback)"""
    loc      = xai["location"]["full"]
    risk     = xai["risk"]
    total_cc = xai["subtypes"]["total"]["volume_cc"]
    enh      = xai["subtypes"]["enhancing"]
    prob_pct = round(probability * 100, 1)

    doctor = (
        f"**[판단 근거]**\n"
        f"AI 모델은 {loc} 영역에서 {prob_pct}%의 이상 신호를 감지하였습니다. "
        f"활성 종양(Enhancing Tumor) {enh['volume_cc']} cc의 조영 증강 패턴이 주요 판단 근거입니다.\n\n"
        f"**[위치 및 기능적 의미]**\n"
        f"{loc} 위치의 병변은 인접 뇌 구조물에 영향을 줄 수 있습니다. "
        f"정확한 해부학적 관계 파악을 위해 전문의 판독이 필요합니다.\n\n"
        f"**[종양 구성 분석]**\n"
        f"전체 병변 체적 {total_cc} cc 중 활성 종양이 {enh['ratio_pct']}%를 차지합니다. "
        f"활성 종양 비율은 치료 반응 및 예후 예측의 중요 지표입니다.\n\n"
        f"**[위험도 및 임상 권고]**\n"
        f"종합 위험도: {risk['level']} — {risk['reason']}. "
        f"정밀 영상 검사 및 신경외과적 평가를 권고합니다."
    )

    if probability >= 0.7:
        patient = (
            "MRI 검사에서 추가 확인이 필요한 부분이 발견되었습니다. "
            "담당 의사 선생님께서 결과를 면밀히 검토하였으며, 정확한 진단을 위해 추가 검사가 필요할 수 있습니다. "
            "너무 걱정하지 마시고, 의료팀이 최선의 방법으로 도움을 드릴 것입니다. "
            "가까운 시일 내에 전문의와 상담을 진행하도록 안내해 드리겠습니다."
        )
    elif probability >= 0.4:
        patient = (
            "MRI 검사 결과를 확인하였습니다. "
            "일부 주의 깊게 관찰해야 할 소견이 있어 추가적인 검토가 필요합니다. "
            "담당 의사 선생님께서 결과를 바탕으로 다음 단계를 안내해 드릴 것입니다. "
            "의료팀이 함께 지속적으로 모니터링하겠습니다."
        )
    else:
        patient = (
            "MRI 검사 결과를 확인하였습니다. "
            "전반적으로 안정적인 소견이 나타났습니다. "
            "담당 의사 선생님께서 결과를 검토하신 후 필요한 사항을 안내해 드릴 것입니다. "
            "정기적인 모니터링을 통해 건강 상태를 지속적으로 관리해 드리겠습니다."
        )

    return {"doctor": doctor, "patient": patient}
