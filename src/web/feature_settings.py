"""Small authenticated switches for optional MCP tool groups."""

import threading

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from utils import atomic_update_config_yaml

from . import _shared as sh


def register(mcp) -> None:
    commit_lock = threading.Lock()
    breath_commit_lock = threading.Lock()

    def payload() -> dict[str, bool]:
        gate = sh.letter_tool_gate
        return {"enabled": bool(gate and gate.is_visible())}

    @mcp.custom_route("/api/settings/letters", methods=["GET"])
    async def get_letter_setting(request: Request) -> Response:
        error = sh._require_auth(request)
        if error:
            return error
        return JSONResponse(payload(), headers={"Cache-Control": "no-store"})

    def breath_payload() -> dict[str, bool]:
        gate = sh.breath_advanced_tool_gate
        return {"enabled": bool(gate and gate.is_visible())}

    @mcp.custom_route("/api/settings/breath-advanced", methods=["GET"])
    async def get_breath_advanced_setting(request: Request) -> Response:
        error = sh._require_auth(request)
        if error:
            return error
        return JSONResponse(breath_payload(), headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/api/settings/breath-advanced", methods=["POST"])
    async def set_breath_advanced_setting(request: Request) -> Response:
        error = sh._require_auth(request)
        if error:
            return error
        try:
            body = await sh._read_json_object(request)
        except (ValueError, TypeError):
            return JSONResponse({"error": "无效 JSON"}, status_code=400)
        if set(body) != {"enabled"} or not isinstance(body.get("enabled"), bool):
            return JSONResponse({"error": "只接受布尔值 enabled"}, status_code=400)

        gate = sh.breath_advanced_tool_gate
        if gate is None:
            return JSONResponse({"error": "高级 Breath 开关尚未就绪"}, status_code=503)

        enabled = body["enabled"]
        with breath_commit_lock, gate.lock:
            try:
                visible = gate.sync(enabled)
                if visible != enabled:
                    raise RuntimeError("breath_advanced MCP tool state mismatch")

                def mutate(saved: dict) -> None:
                    features = saved.get("features")
                    if not isinstance(features, dict):
                        features = {}
                        saved["features"] = features
                    features["breath_advanced_enabled"] = enabled

                atomic_update_config_yaml(mutate)
                if not isinstance(sh.config.get("features"), dict):
                    sh.config["features"] = {}
                sh.config["features"]["breath_advanced_enabled"] = enabled
            except Exception:
                saved_enabled = bool(
                    (sh.config.get("features") or {}).get(
                        "breath_advanced_enabled", True
                    )
                )
                try:
                    gate.sync(saved_enabled)
                except Exception:
                    gate.sync(False)
                return JSONResponse({"error": "高级 Breath 开关未能保存"}, status_code=503)

        return JSONResponse(breath_payload(), headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/api/settings/letters", methods=["POST"])
    async def set_letter_setting(request: Request) -> Response:
        error = sh._require_auth(request)
        if error:
            return error
        try:
            body = await sh._read_json_object(request)
        except (ValueError, TypeError):
            return JSONResponse({"error": "无效 JSON"}, status_code=400)
        if set(body) != {"enabled"} or not isinstance(body.get("enabled"), bool):
            return JSONResponse({"error": "只接受布尔值 enabled"}, status_code=400)

        gate = sh.letter_tool_gate
        if gate is None:
            return JSONResponse({"error": "Letter 工具开关尚未就绪"}, status_code=503)

        enabled = body["enabled"]
        with commit_lock, gate.lock:
            try:
                visible = gate.sync(enabled)
                if visible != enabled:
                    raise RuntimeError("Letter MCP tool state mismatch")

                def mutate(saved: dict) -> None:
                    features = saved.get("features")
                    if not isinstance(features, dict):
                        features = {}
                        saved["features"] = features
                    features["letters_enabled"] = enabled

                atomic_update_config_yaml(mutate)
                if not isinstance(sh.config.get("features"), dict):
                    sh.config["features"] = {}
                sh.config["features"]["letters_enabled"] = enabled
            except Exception:
                # Persistence failed: return discovery to the last saved state.
                saved_enabled = bool((sh.config.get("features") or {}).get("letters_enabled", False))
                try:
                    gate.sync(saved_enabled)
                except Exception:
                    gate.sync(False)
                return JSONResponse({"error": "Letter 开关未能保存"}, status_code=503)

        return JSONResponse(payload(), headers={"Cache-Control": "no-store"})
