"""Example *external* analysis endpoint.

Stands in for a third-party REST analysis (a thermal solver, a costing tool,
...). It is registered in the analysis registry as kind=external, so a SysML v2
model calls it during execution through the analysis service.

Request:  {"toolName", "uri", "inputs": {"power": {"value": 150000, "unit": "W"}, ...}, "runId"}
Response: {"outputs": {"tMax": {"value": ..., "unit": "K"}, "margin": {...}}}
"""

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Demo external thermal analysis")


class Req(BaseModel):
    toolName: str | None = None
    uri: str | None = None
    inputs: dict
    runId: str | None = None


def _v(x):
    return x["value"] if isinstance(x, dict) else x


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/thermal")
def thermal(req: Req):
    power = float(_v(req.inputs["power"]))       # W of heat to reject
    area = float(_v(req.inputs["area"]))         # m^2 of radiator
    ambient = float(_v(req.inputs["ambient"]))   # K
    h = 25.0                                     # W/(m^2 K) forced convection
    t_max = ambient + power / (h * area)
    limit = 393.15                               # 120 degC
    return {"outputs": {"tMax": {"value": round(t_max, 2), "unit": "K"},
                        "margin": {"value": round(limit - t_max, 2), "unit": "K"}}}
