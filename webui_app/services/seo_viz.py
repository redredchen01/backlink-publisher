from dataclasses import dataclass
from typing import Dict, Any
import json
import subprocess

@dataclass
class AnchorData:
    main_domain: str
    total_entries: int
    type_stats: Dict[str, Any]
    alarm: Dict[str, Any]

    @classmethod
    def from_report(cls, domain: str):
        # Invoke report-anchors
        cmd = ["report-anchors", "--from-profile", domain, "--json"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        # Note: report-anchors exits with 6 on breach, so we catch it
        data = json.loads(result.stdout)
        return cls(
            main_domain=data["main_domain"],
            total_entries=data["total_entries"],
            type_stats=data["type_stats"],
            alarm=data.get("alarm", {})
        )

    def to_chart_data(self):
        # Transform for e.g. ECharts
        labels = list(self.type_stats.keys())
        counts = [s["count"] for s in self.type_stats.values()]
        return {
            "labels": labels,
            "datasets": [{"label": "锚点分布", "data": counts}]
        }
