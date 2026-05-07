"""Recover 404 SOURCE_NOT_FOUND repos by detecting their real default branch.

Reads the index-*.jsonl produced by index_all_repos.py, finds entries whose
HTTP status was 404, queries GitHub for the real default_branch, and re-calls
ce_index_github_repo with corpus_id=gh-{owner}-{name}-main (matching what the
CSB spec.json files expect) but branch=<real default>.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def get_default_branch(owner: str, name: str, gh_token: str | None) -> str | None:
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "csb-bench"}
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"
    req = urllib.request.Request(f"https://api.github.com/repos/{owner}/{name}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data.get("default_branch")
    except urllib.error.HTTPError as e:
        return None
    except Exception:
        return None


def post_index(mcp_url: str, mcp_token: str, repo: str, branch: str, corpus_id: str) -> dict:
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "ce_index_github_repo",
                   "arguments": {"repo": repo, "branch": branch,
                                 "data_classification": "public",
                                 "corpus_id": corpus_id}},
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{mcp_url}/api/mcp", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {mcp_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=320) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode("utf-8", errors="replace")[:300]}
    except Exception as e:
        return {"_transport_error": f"{type(e).__name__}: {e}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index-jsonl", required=True, type=Path)
    ap.add_argument("--mcp-url", default="https://ce-mcp-prod.vercel.app")
    ap.add_argument("--token-file", required=True, type=Path)
    ap.add_argument("--gh-token-env", default="GITHUB_TOKEN")
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()

    mcp_token = args.token_file.read_text(encoding="utf-8-sig").strip()
    gh_token = os.environ.get(args.gh_token_env)
    if not gh_token:
        print(f"WARN: {args.gh_token_env} not set; default-branch lookup will be unauthenticated and may rate limit.", file=sys.stderr)

    candidates = []
    for line in args.index_jsonl.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        raw = r.get("raw", {})
        if raw.get("_http_error") == 404:
            candidates.append((r["repo"], r["branch"]))
    print(f"[+] {len(candidates)} 404 candidates to recover")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fh = args.output.open("w", encoding="utf-8")
    ok = 0
    for i, (repo, original_branch) in enumerate(candidates, 1):
        owner, name = repo.split("/", 1)
        # CSB spec.json bakes branch=main; preserve corpus_id so bench can find it.
        original_cid = re.sub(r"[^a-z0-9-]+", "-", f"gh-{owner}-{name}-{original_branch}".lower()).strip("-")
        real = get_default_branch(owner, name, gh_token)
        if not real:
            rec = {"repo": repo, "branch_was": original_branch, "branch_now": None,
                   "status": "lookup_failed", "corpus_id": original_cid}
            fh.write(json.dumps(rec) + "\n"); fh.flush()
            print(f"[{i:3d}/{len(candidates)}] LOOKUP_FAILED  {repo}", flush=True)
            continue
        if real == original_branch:
            rec = {"repo": repo, "branch_was": original_branch, "branch_now": real,
                   "status": "branch_unchanged", "corpus_id": original_cid}
            fh.write(json.dumps(rec) + "\n"); fh.flush()
            print(f"[{i:3d}/{len(candidates)}] UNCHANGED      {repo} default={real}", flush=True)
            continue
        t0 = time.time()
        out = post_index(args.mcp_url, mcp_token, repo, real, original_cid)
        elapsed = round(time.time() - t0, 1)
        rec = {"repo": repo, "branch_was": original_branch, "branch_now": real,
               "corpus_id": original_cid, "elapsed_s": elapsed}
        if "result" in out and not out["result"].get("isError"):
            sc = out["result"].get("structuredContent", {})
            rec["status"] = "ok"
            rec["file_count"] = sc.get("stats", {}).get("file_count")
            rec["embedded_count"] = sc.get("stats", {}).get("embedded_count")
            ok += 1
        elif "result" in out and out["result"].get("isError"):
            rec["status"] = "tool_error"
            rec["body"] = json.dumps(out["result"].get("structuredContent", {}))[:300]
        else:
            rec["status"] = "transport"
            rec["raw"] = out
        fh.write(json.dumps(rec) + "\n"); fh.flush()
        tag = "OK " if rec["status"] == "ok" else f"ERR({rec['status']})"
        print(f"[{i:3d}/{len(candidates)}] {tag}  {repo}@{real}->{original_cid}  {elapsed}s", flush=True)
    fh.close()
    print(f"[+] Recovered {ok}/{len(candidates)} via branch override.")


if __name__ == "__main__":
    main()
