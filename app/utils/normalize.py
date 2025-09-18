from typing import Any, Dict, List

def as_list(v: Any) -> List[Any]:

    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, tuple):
        return list(v)
    if isinstance(v, dict):
        return [v]
    return []

def normalize_results(results: Dict[str, Any], known_keys: List[str] | None = None) -> Dict[str, List[Dict]]:

    out: Dict[str, List[Dict]] = {}
    if known_keys:
        for k in known_keys:
            out[k] = as_list(results.get(k))
    else:
        for k, v in (results or {}).items():
            out[k] = as_list(v)
    return out
