"""LLM concept labeler — names feature clusters with product-level concepts.

Pipeline: cluster + file data → prompt → cached LLM call → {concept, description, sub_features}.

Used by feature_map.build_feature_map to lift mechanical labels (e.g. "SideNavbar,
TopNavbar") into product-level concepts ("Navigation"). The LLM call is cached on
disk by sha256(prompt) so re-runs against the same workspace skip the API entirely.

Default model: claude-haiku-4-5-20251001. Override via the `model` argument to
label_all_clusters or by passing your own `llm` callable.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SYSTEM_PROMPT = (
    "You name code feature clusters with product-level concepts. "
    "Respond with a single JSON object: {concept, description, sub_features}. "
    "concept is 1-3 words, title-cased. description is one short sentence. "
    "sub_features is a list of 3-6 short human-readable items naming the "
    "user-visible capabilities the cluster delivers."
)


def build_prompt(cluster: dict[str, Any], file_data: dict[str, dict],
                 current_label: str) -> str:
    """Build the user message naming a cluster from its files + symbols + first sentences."""
    lines = [f"<cluster current_label='{current_label}'>", "<files>"]
    for path in cluster.get('nodes', [])[:25]:  # cap context for big clusters
        info = file_data.get(path, {})
        symbols = ', '.join(info.get('symbols', [])[:8])
        first = info.get('first_sentence', '').strip()
        lines.append(f"- {path} :: {symbols} :: {first}")
    lines.append("</files>")
    lines.append("Name this cluster as a JSON object with concept, description, "
                 "sub-features (3-6 items).")
    return '\n'.join(lines)


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.json"


def _fallback(current_label: str) -> dict[str, Any]:
    return {'concept': current_label or 'Unnamed',
            'description': '',
            'sub_features': []}


def label_cluster(cluster: dict[str, Any], file_data: dict[str, dict], *,
                  llm: Callable[[str], str] | None = None,
                  cache_dir: Path | None = None,
                  current_label: str = '',
                  model: str = '') -> dict[str, Any]:
    """Label one cluster. Returns {concept, description, sub_features}.

    On any failure (empty cluster, LLM exception, malformed JSON) returns the
    safe fallback derived from current_label so the caller never sees a None.

    `model` is folded into the cache key so swapping the labeling model (or
    bumping SYSTEM_PROMPT) does not silently reuse stale labels from a
    previous run.
    """
    if not cluster.get('nodes'):
        return _fallback(current_label)

    prompt = build_prompt(cluster, file_data, current_label)
    cache_seed = f'{model}\n{SYSTEM_PROMPT}\n{prompt}'
    key = hashlib.sha256(cache_seed.encode('utf-8')).hexdigest()[:32]

    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = _cache_path(cache_dir, key)
        if cached.exists():
            try:
                data = json.loads(cached.read_text(encoding='utf-8'))
                return {
                    'concept': data.get('concept', current_label),
                    'description': data.get('description', ''),
                    'sub_features': data.get('sub_features', []),
                }
            except (OSError, json.JSONDecodeError):
                pass  # cache corrupt — re-run

    if llm is None:
        return _fallback(current_label)

    try:
        raw = llm(prompt)
    except Exception as exc:  # noqa: BLE001
        print(f'[concept_labeler] LLM error for {current_label!r}: {exc}',
              file=sys.stderr)
        return _fallback(current_label)

    try:
        parsed = json.loads(raw)
        result = {
            'concept': str(parsed.get('concept', current_label)).strip() or current_label,
            'description': str(parsed.get('description', '')).strip(),
            'sub_features': [str(x) for x in parsed.get('sub_features', [])][:6],
        }
    except (json.JSONDecodeError, TypeError):
        return _fallback(current_label)

    if cache_dir is not None:
        try:
            payload = {**result,
                       'model': model or 'unknown',
                       'timestamp': datetime.now(timezone.utc).isoformat()}
            _cache_path(cache_dir, key).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError as exc:
            print(f'[concept_labeler] cache write failed: {exc}', file=sys.stderr)

    return result


def _build_anthropic_llm(model: str) -> Callable[[str], str]:
    """Lazy-build an Anthropic SDK callable. Imports anthropic at call time so
    the module remains usable without the SDK installed (tests use stub LLMs).
    """
    try:
        import anthropic  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            'concept_labeler requires `pip install anthropic>=0.40` for live LLM '
            'calls. Pass a stub `llm` callable to skip this dependency.'
        ) from exc

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError('ANTHROPIC_API_KEY is not set')

    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    def call(prompt: str) -> str:
        # Cache the system prompt — it's identical across every cluster call.
        msg = client.messages.create(
            model=model,
            max_tokens=400,
            system=[{'type': 'text', 'text': SYSTEM_PROMPT,
                     'cache_control': {'type': 'ephemeral'}}],
            messages=[{'role': 'user', 'content': prompt}],
        )
        return ''.join(block.text for block in msg.content
                       if getattr(block, 'type', None) == 'text')

    return call


def label_all_clusters(clusters: dict[Any, dict[str, Any]],
                       file_data: dict[str, dict],
                       cluster_labels: dict[Any, str], *,
                       llm: Callable[[str], str] | None = None,
                       cache_dir: Path | None = None,
                       model: str = 'claude-haiku-4-5-20251001',
                       max_workers: int = 4) -> dict[Any, dict[str, Any]]:
    """Label every cluster concurrently. Returns {cluster_id: {concept, ...}}."""
    if not clusters:
        return {}
    if llm is None:
        llm = _build_anthropic_llm(model)

    def _one(cid_cluster: tuple) -> tuple:
        cid, cluster = cid_cluster
        result = label_cluster(
            cluster, file_data,
            llm=llm, cache_dir=cache_dir,
            current_label=str(cluster_labels.get(cid, '')),
            model=model,
        )
        return cid, result

    out: dict[Any, dict[str, Any]] = {}
    if not clusters:
        return out

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        for cid, result in pool.map(_one, clusters.items()):
            out[cid] = result

    return out
