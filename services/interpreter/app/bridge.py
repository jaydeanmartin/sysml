#!/usr/bin/env python3
"""OpenSysML external-tool bridge -> REST analysis service.

OpenSysML starts this program once per performance of a calc/action annotated
with ``ToolExecution``. It writes one JSON request on stdin::

    {"toolName": "massRollup", "uri": "...", "inputs": {"m": {"value": 3, "unit": "kg"}}}

and expects one JSON reply on stdout::

    {"outputs": {"total": {"value": 3, "unit": "kg"}}}   or   {"error": "..."}

Standard library only, so it runs wherever the interpreter does.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def main() -> int:
    tool = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        request = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"bridge: malformed request: {exc}"}))
        return 0
    tool = tool or request.get("toolName")
    base = os.environ.get("ANALYSIS_SERVICE_URL", "http://analysis:8003").rstrip("/")
    url = f"{base}/analyses/{urllib.parse.quote(tool, safe='')}/invoke"
    body = dict(request)
    run_id = os.environ.get("SYSML_RUN_ID")
    if run_id:
        body["runId"] = run_id
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=float(os.environ.get("BRIDGE_TIMEOUT", "55"))) as resp:
            reply = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        try:
            detail = json.loads(detail).get("detail", detail)
        except Exception:
            pass
        reply = {"error": f"analysis '{tool}' returned HTTP {exc.code}: {detail}"}
    except Exception as exc:
        reply = {"error": f"analysis '{tool}' unreachable at {url}: {exc}"}
    # OpenSysML accepts exactly {"outputs": ...} or {"error": ...}
    if "error" in reply and reply["error"]:
        out = {"error": str(reply["error"])}
    else:
        out = {"outputs": reply.get("outputs", {})}
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
