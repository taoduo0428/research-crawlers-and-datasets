"""Submit once or resume a job; use the project's requests dependency."""
import argparse
import json
import math
from pathlib import Path
import time

import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url")
    source.add_argument("--job-id")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--output", default="reactors.csv")
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    base = args.base_url.rstrip("/")
    with requests.Session() as session:
        # No API authentication; do not inherit an unrelated system HTTP proxy.
        session.trust_env = False

        def request(method, path, **kwargs):
            response = session.request(method, base + path, timeout=30, **kwargs)
            response.raise_for_status()
            return response

        request("GET", "/health")
        job_id = args.job_id or request("POST", "/v1/jobs", json={
            "post_url": args.url, "collect": ["reactions"],
            "save_artifacts": ["progress", "safe_capture"],
        }).json()["job_id"]
        print(f"job_id={job_id}", flush=True)
        path = f"/v1/jobs/{job_id}"
        deadline = time.monotonic() + args.timeout
        while True:
            job = request("GET", path).json()
            status = job["status"]
            if status == "completed":
                break
            if status in {"failed", "cancelled"}:
                raise RuntimeError(json.dumps(job, ensure_ascii=True))
            if status not in {"queued", "running", "cancelling"}:
                raise RuntimeError(f"Unknown status: {status}")
            print(json.dumps(request("GET", path + "/progress").json(), ensure_ascii=True), flush=True)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Resume with --job-id {job_id}; job was not cancelled")
            time.sleep(min(2, remaining))
        result = request("GET", path + "/result").json()
        data = request("GET", path + "/artifacts/facebook-reactor-users.csv?download=1").content
        Path(args.output).write_bytes(data)
        print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
