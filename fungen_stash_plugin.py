import json
import os
import shlex
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

import urllib.error
import urllib.request


def _stderr(msg: str) -> None:
    sys.stderr.write(msg.rstrip() + "\n")
    sys.stderr.flush()


def _read_input() -> Dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception as exc:
        _stderr(f"[fungen] Failed to parse input JSON: {exc}")
        return {}


def _get_server_connection(input_data: Dict[str, Any]) -> Dict[str, Any]:
    return input_data.get("server_connection") or {}


def _get_args(input_data: Dict[str, Any]) -> Dict[str, Any]:
    return input_data.get("args") or {}


def _get_settings(input_data: Dict[str, Any]) -> Dict[str, Any]:
    settings = input_data.get("settings") or {}
    if isinstance(settings, dict):
        return settings
    return {}


def _plugin_dir(input_data: Dict[str, Any], args: Dict[str, Any], server_connection: Dict[str, Any]) -> str:
    plugin_dir = (
        input_data.get("pluginDir")
        or args.get("pluginDir")
        or server_connection.get("PluginDir")
        or os.getcwd()
    )
    return os.path.abspath(os.path.expanduser(str(plugin_dir)))


def _expand_plugin_dir(
    value: str, input_data: Dict[str, Any], args: Dict[str, Any], server_connection: Dict[str, Any]
) -> str:
    if not isinstance(value, str):
        return value
    if "{pluginDir}" not in value:
        return value
    return value.replace("{pluginDir}", _plugin_dir(input_data, args, server_connection))


def _get_cookie(server_connection: Dict[str, Any]) -> Optional[Dict[str, str]]:
    cookie = server_connection.get("SessionCookie")
    if not cookie:
        return None
    name = cookie.get("Name")
    value = cookie.get("Value")
    if not name or not value:
        return None
    return {name: value}


def _get_server_url(server_connection: Dict[str, Any], args: Dict[str, Any]) -> str:
    scheme = (server_connection.get("Scheme") or "http").lower()
    host = args.get("host") or server_connection.get("Host") or "localhost"
    port = server_connection.get("Port") or 9999
    return f"{scheme}://{host}:{port}/graphql"


def _gql_request(
    url: str,
    query: str,
    variables: Optional[Dict[str, Any]],
    cookie: Optional[Dict[str, str]],
    timeout: int = 60,
) -> Dict[str, Any]:
    payload = {"query": query, "variables": variables or {}}
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookie.items())
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GraphQL HTTP error: {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GraphQL connection error: {exc.reason}") from exc
    data = json.loads(body or "{}")
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data.get("data") or {}


def _resolve_fungen_main(path_or_dir: str) -> str:
    path = os.path.expanduser(path_or_dir)
    if path.endswith(".py"):
        return path
    return os.path.join(path, "main.py")


def _build_fungen_cmd(
    python_path: str,
    fungen_main: str,
    video_path: str,
    mode: str,
    od_mode: str,
    overwrite: bool,
    no_autotune: bool,
    no_copy: bool,
    extra_args: List[str],
) -> List[str]:
    cmd = [python_path, fungen_main, video_path]
    if mode:
        cmd += ["--mode", mode]
    if od_mode:
        cmd += ["--od-mode", od_mode]
    if overwrite:
        cmd.append("--overwrite")
    if no_autotune:
        cmd.append("--no-autotune")
    if no_copy:
        cmd.append("--no-copy")
    cmd += extra_args
    return cmd


def _scene_query() -> str:
    return (
        "query FindScenes($filter: FindFilterType, $scene_filter: SceneFilterType, $scene_ids: [Int!]) {"
        "  findScenes(filter: $filter, scene_filter: $scene_filter, scene_ids: $scene_ids) {"
        "    count"
        "    scenes { id path }"
        "  }"
        "}"
    )


def _fetch_scenes(
    url: str,
    cookie: Optional[Dict[str, str]],
    scene_ids: Optional[List[int]] = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    variables = {
        "filter": {"per_page": 200, "page": 1},
        "scene_filter": None,
        "scene_ids": scene_ids,
    }
    data = _gql_request(url, _scene_query(), variables, cookie)
    find = data.get("findScenes") or {}
    count = find.get("count") or 0
    scenes = find.get("scenes") or []
    return count, scenes


def _iter_all_scenes(url: str, cookie: Optional[Dict[str, str]]) -> List[Dict[str, Any]]:
    all_scenes: List[Dict[str, Any]] = []
    page = 1
    per_page = 200
    total = None
    while True:
        variables = {
            "filter": {"per_page": per_page, "page": page},
            "scene_filter": None,
            "scene_ids": None,
        }
        data = _gql_request(url, _scene_query(), variables, cookie)
        find = data.get("findScenes") or {}
        if total is None:
            total = find.get("count") or 0
            _stderr(f"[fungen] Total scenes: {total}")
        scenes = find.get("scenes") or []
        if not scenes:
            break
        all_scenes.extend(scenes)
        page += 1
        if total is not None and len(all_scenes) >= total:
            break
    return all_scenes


def _should_skip(video_path: str) -> bool:
    base, _ = os.path.splitext(video_path)
    funscript_path = base + ".funscript"
    return os.path.exists(funscript_path)


def _run_fungen_for_scene(
    scene: Dict[str, Any],
    python_path: str,
    fungen_main: str,
    mode: str,
    od_mode: str,
    overwrite: bool,
    no_autotune: bool,
    no_copy: bool,
    extra_args: List[str],
) -> Tuple[bool, str]:
    scene_id = scene.get("id")
    video_path = scene.get("path") or ""
    if not video_path:
        return False, f"Scene {scene_id} has no path"

    if not overwrite and _should_skip(video_path):
        return False, f"Scene {scene_id} already has funscript"

    cmd = _build_fungen_cmd(
        python_path=python_path,
        fungen_main=fungen_main,
        video_path=video_path,
        mode=mode,
        od_mode=od_mode,
        overwrite=overwrite,
        no_autotune=no_autotune,
        no_copy=no_copy,
        extra_args=extra_args,
    )

    _stderr(f"[fungen] Running: {cmd}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
        return False, f"Scene {scene_id} failed: {err}"
    return True, f"Scene {scene_id} ok"


def _run_install(install_dir: str, repo_url: str, repo_ref: str) -> Tuple[bool, str]:
    if not repo_url:
        return False, "Missing fungen_repo for install (set it in the Install FunGen CLI task)"
    install_dir = os.path.expanduser(install_dir)
    os.makedirs(install_dir, exist_ok=True)
    git_dir = os.path.join(install_dir, ".git")
    if os.path.isdir(git_dir):
        cmd = ["git", "-C", install_dir, "pull", "--ff-only"]
    else:
        cmd = ["git", "clone", repo_url, install_dir]
    cmd_ref = ["git", "-C", install_dir, "checkout", repo_ref] if repo_ref else None
    _stderr(f"[fungen] Running: {cmd}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
        return False, f"Install failed: {err}"
    if cmd_ref:
        _stderr(f"[fungen] Running: {cmd_ref}")
        proc_ref = subprocess.run(cmd_ref, capture_output=True, text=True)
        if proc_ref.returncode != 0:
            err = proc_ref.stderr.strip() or proc_ref.stdout.strip() or "unknown error"
            return False, f"Checkout failed: {err}"
    return True, f"Installed FunGen to {install_dir}"


def main() -> None:
    input_data = _read_input()
    args = _get_args(input_data)
    settings = _get_settings(input_data)
    server_connection = _get_server_connection(input_data)
    cookie = _get_cookie(server_connection)
    url = _get_server_url(server_connection, args)

    scope = args.get("scope") or "all"
    python_path = args.get("python_path") or settings.get("python_path") or "python3"
    fungen_path = args.get("fungen_path") or settings.get("fungen_path") or ""
    fungen_path = _expand_plugin_dir(fungen_path, input_data, args, server_connection)
    if not fungen_path and scope != "install":
        print(json.dumps({"error": "Missing required arg: fungen_path"}))
        return
    fungen_main = _resolve_fungen_main(fungen_path) if fungen_path else ""
    if fungen_main and not os.path.exists(fungen_main):
        print(json.dumps({"error": f"FunGen entrypoint not found: {fungen_main}"}))
        return

    mode = args.get("mode") or ""
    od_mode = args.get("od_mode") or "current"
    overwrite = bool(args.get("overwrite"))
    no_autotune = bool(args.get("no_autotune"))
    no_copy = bool(args.get("no_copy"))
    extra_args = args.get("extra_args") or []
    if isinstance(extra_args, str):
        extra_args = shlex.split(extra_args)
    if not isinstance(extra_args, list):
        extra_args = []

    processed = 0
    skipped = 0
    failed = 0

    try:
        if scope == "install":
            install_dir = args.get("install_dir") or ""
            install_dir = _expand_plugin_dir(install_dir, input_data, args, server_connection)
            repo_url = args.get("fungen_repo") or settings.get("fungen_repo") or ""
            repo_ref = args.get("fungen_ref") or settings.get("fungen_ref") or ""
            if not install_dir:
                raise RuntimeError("Missing install_dir for scope=install")
            ok, msg = _run_install(install_dir, repo_url, repo_ref)
            if ok:
                print(json.dumps({"output": {"installed": True, "message": msg}}))
            else:
                print(json.dumps({"error": msg}))
            return
        scenes: List[Dict[str, Any]] = []
        if scope == "hook":
            hook_context = args.get("hookContext") or {}
            scene_id = hook_context.get("id")
            if scene_id is None:
                raise RuntimeError("Hook context missing scene id")
            _, scenes = _fetch_scenes(url, cookie, [int(scene_id)])
        elif scope == "scene":
            scene_id = args.get("scene_id")
            if scene_id is None:
                raise RuntimeError("Missing scene_id for scope=scene")
            _, scenes = _fetch_scenes(url, cookie, [int(scene_id)])
        else:
            scenes = _iter_all_scenes(url, cookie)

        for scene in scenes:
            ok, msg = _run_fungen_for_scene(
                scene=scene,
                python_path=python_path,
                fungen_main=fungen_main,
                mode=mode,
                od_mode=od_mode,
                overwrite=overwrite,
                no_autotune=no_autotune,
                no_copy=no_copy,
                extra_args=extra_args,
            )
            if ok:
                processed += 1
                _stderr(f"[fungen] {msg}")
            else:
                if "already has funscript" in msg:
                    skipped += 1
                    _stderr(f"[fungen] {msg}")
                else:
                    failed += 1
                    _stderr(f"[fungen] {msg}")

        result = {"processed": processed, "skipped": skipped, "failed": failed}
        print(json.dumps({"output": result}))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))


if __name__ == "__main__":
    main()
