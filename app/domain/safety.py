from app.domain.models import ConditionLevel, ConditionReport


def hard_safety_constraints(report: ConditionReport) -> list[str]:
    """Deterministic constraints applied before and after AI generation."""
    if report.level == ConditionLevel.PAIN:
        return [
            "Do not prescribe running or high-intensity exercise.",
            "Recommend rest and professional medical advice if pain persists or worsens.",
        ]
    if report.level == ConditionLevel.DISCOMFORT:
        return [
            "Do not increase distance or intensity.",
            "Prefer rest or low-impact, low-intensity activity.",
        ]
    if report.level == ConditionLevel.FATIGUED:
        return ["Keep the next session easy and reduce planned load."]
    return ["Do not increase weekly volume by more than the configured safe limit."]
