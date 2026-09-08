#!/usr/bin/env python3
"""
WiZ Light Controller — LLM-ready tool interface via WebSocket
"""

import asyncio
import json
import websockets
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from pathlib import Path

from pywizlight import wizlight, PilotBuilder, discovery
from pywizlight.bulblibrary import BulbType
from pywizlight.scenes import get_id_from_scene_name


# ── Config ───────────────────────────────────────────────────────────────────

CONFIG_PATH = Path("D:\\IRIS_Hub\\config.json")
IRIS_URL = "wss://backend.irisapis.us/api/devices/ws/{user}/{device_id}"


# ── Scene Definitions ────────────────────────────────────────────────────────

WIZ_SCENES: Dict[str, int] = {
    "Ocean": 1, "Romance": 2, "Sunset": 3, "Party": 4, "Fireplace": 5,
    "Cozy": 6, "Forest": 7, "Pastel Colors": 8, "Wake up": 9, "Bedtime": 10,
    "Warm White": 11, "Daylight": 12, "Cool white": 13, "Night light": 14,
    "Focus": 15, "Relax": 16, "True colors": 17, "TV time": 18,
    "Plantgrowth": 19, "Spring": 20, "Summer": 21, "Fall": 22,
    "Deepdive": 23, "Jungle": 24, "Mojito": 25, "Club": 26,
    "Christmas": 27, "Halloween": 28, "Candlelight": 29,
    "Golden white": 30, "Pulse": 31, "Steampunk": 32, "Rhythm": 33,
}


# ── Data Models ─────────────────────────────────────────────────────────────

@dataclass
class BulbInfo:
    ip: str
    mac: str
    name: str = "Unknown"
    bulb_type: Optional[BulbType] = None


@dataclass
class Result:
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "data": self.data
        }


# ── WiZ Controller (returns Results, no prints) ─────────────────────────────

class WiZController:
    def __init__(self):
        self.bulbs: Dict[str, wizlight] = {}
        self.bulb_info: Dict[str, BulbInfo] = {}
        self.discovered: bool = False

    # ── Discovery ────────────────────────────────────────────────────────────

    async def discover(self, broadcast: str = "255.255.255.255") -> Result:
        try:
            found = await discovery.discover_lights(broadcast_space=broadcast)

            self.bulbs.clear()
            self.bulb_info.clear()

            for bulb in found:
                info = BulbInfo(ip=bulb.ip, mac=await bulb.getMac() or "Unknown")
                try:
                    info.bulb_type = await bulb.get_bulbtype()
                    info.name = info.bulb_type.name if info.bulb_type else "WiZ Bulb"
                except Exception:
                    info.name = "WiZ Bulb"

                self.bulbs[bulb.ip] = bulb
                self.bulb_info[bulb.ip] = info

            self.discovered = len(self.bulbs) > 0

            if self.discovered:
                return Result(
                    success=True,
                    message=f"Found {len(self.bulbs)} device(s)",
                    data={"bulbs": [
                        {"ip": ip, "mac": info.mac, "name": info.name}
                        for ip, info in self.bulb_info.items()
                    ]}
                )
            else:
                return Result(success=False, message="No WiZ devices found")

        except Exception as e:
            return Result(success=False, message=f"Discovery failed: {e}")

    def add_bulb(self, ip: str) -> Result:
        if ip not in self.bulbs:
            self.bulbs[ip] = wizlight(ip)
            self.bulb_info[ip] = BulbInfo(ip=ip, mac="Manual")
            return Result(success=True, message=f"Added bulb {ip}")
        return Result(success=True, message=f"Bulb {ip} already exists")

    # ── Power Control ────────────────────────────────────────────────────────

    async def turn_on(self, ip: str, pilot: Optional[PilotBuilder] = None) -> Result:
        if ip not in self.bulbs:
            return Result(success=False, message=f"Bulb {ip} not found")
        try:
            await self.bulbs[ip].turn_on(pilot or PilotBuilder())
            return Result(success=True, message=f"Turned ON {ip}")
        except Exception as e:
            return Result(success=False, message=f"Error: {e}")

    async def turn_off(self, ip: str) -> Result:
        if ip not in self.bulbs:
            return Result(success=False, message=f"Bulb {ip} not found")
        try:
            await self.bulbs[ip].turn_off()
            return Result(success=True, message=f"Turned OFF {ip}")
        except Exception as e:
            return Result(success=False, message=f"Error: {e}")

    async def toggle(self, ip: str) -> Result:
        if ip not in self.bulbs:
            return Result(success=False, message=f"Bulb {ip} not found")
        try:
            await self.bulbs[ip].lightSwitch()
            return Result(success=True, message=f"Toggled {ip}")
        except Exception as e:
            return Result(success=False, message=f"Error: {e}")

    # ── Color / Brightness ───────────────────────────────────────────────────

    async def set_brightness(self, ip: str, brightness: int) -> Result:
        if not 0 <= brightness <= 255:
            return Result(success=False, message="Brightness must be 0-255")
        return await self.turn_on(ip, PilotBuilder(brightness=brightness))

    async def set_color_temp(self, ip: str, kelvin: int) -> Result:
        return await self.turn_on(ip, PilotBuilder(colortemp=kelvin))

    async def set_warm_white(self, ip: str, intensity: int = 255) -> Result:
        return await self.turn_on(ip, PilotBuilder(warm_white=intensity))

    async def set_cold_white(self, ip: str, intensity: int = 255) -> Result:
        return await self.turn_on(ip, PilotBuilder(cold_white=intensity))

    async def set_rgb(self, ip: str, r: int, g: int, b: int) -> Result:
        if not all(0 <= v <= 255 for v in (r, g, b)):
            return Result(success=False, message="RGB values must be 0-255")
        return await self.turn_on(ip, PilotBuilder(rgb=(r, g, b)))

    async def set_rgbw(self, ip: str, r: int, g: int, b: int, w: int) -> Result:
        return await self.turn_on(ip, PilotBuilder(rgb=(r, g, b), w=w))

    # ── Scenes ───────────────────────────────────────────────────────────────

    async def set_scene(self, ip: str, scene_name: str) -> Result:
        if scene_name not in WIZ_SCENES:
            return Result(success=False, message=f"Invalid scene: {scene_name}")
        result = await self.turn_on(ip, PilotBuilder(scene=WIZ_SCENES[scene_name]))
        if result.success:
            result.message = f"Scene: {scene_name}"
        return result

    async def set_scene_by_name(self, ip: str, scene_name: str) -> Result:
        try:
            scene_id = get_id_from_scene_name(scene_name)
            result = await self.set_scene(ip, scene_id)
            return result
        except ValueError:
            return Result(success=False, message=f"Unknown scene: '{scene_name}'")

    async def set_rhythm(self, ip: str) -> Result:
        return await self.set_scene(ip, 33)

    # ── Status & Capabilities ────────────────────────────────────────────────

    async def get_status(self, ip: str) -> Result:
        if ip not in self.bulbs:
            return Result(success=False, message=f"Bulb {ip} not found")
        try:
            bulb = self.bulbs[ip]
            await bulb.updateState()
            raw_state = bulb.state

            if isinstance(raw_state, list) and len(raw_state) > 0:
                state = raw_state[0]
            else:
                state = raw_state

            if state is None:
                return Result(success=False, message=f"No state available for {ip}")

            return Result(
                success=True,
                message=f"Status for {ip}",
                data={
                    "ip": ip,
                    "mac": self.bulb_info.get(ip, BulbInfo(ip=ip, mac="?")).mac,
                    "power": "ON" if state.get_state() else "OFF",
                    "brightness": state.get_brightness(),
                    "color_temp": state.get_colortemp(),
                    "rgb": state.get_rgb(),
                    "warm_white": state.get_warm_white(),
                    "cold_white": state.get_cold_white(),
                    "scene": state.get_scene(),
                    "rssi": getattr(state, "rssi", None),
                }
            )
        except Exception as e:
            return Result(success=False, message=f"Error getting status: {e}")

    async def get_capabilities(self, ip: str) -> Result:
        if ip not in self.bulbs:
            return Result(success=False, message=f"Bulb {ip} not found")
        try:
            bulb_type = await self.bulbs[ip].get_bulbtype()
            if not bulb_type:
                return Result(success=False, message="Could not retrieve capabilities")

            f = bulb_type.features
            return Result(
                success=True,
                message=f"Capabilities for {ip}",
                data={
                    "model": bulb_type.name,
                    "brightness": f.brightness,
                    "color": f.color,
                    "color_temp": f.color_tmp,
                    "effects": f.effect,
                    "kelvin_range": {
                        "min": bulb_type.kelvin_range.min,
                        "max": bulb_type.kelvin_range.max,
                    } if f.color_tmp else None,
                }
            )
        except Exception as e:
            return Result(success=False, message=f"Error: {e}")

    # ── Batch Operations ─────────────────────────────────────────────────────

    async def all_on(self, pilot: Optional[PilotBuilder] = None) -> Result:
        if not self.bulbs:
            return Result(success=False, message="No bulbs discovered")
        tasks = [bulb.turn_on(pilot or PilotBuilder()) for bulb in self.bulbs.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        return Result(success=True, message=f"All {len(self.bulbs)} bulb(s) ON")

    async def all_off(self) -> Result:
        if not self.bulbs:
            return Result(success=False, message="No bulbs discovered")
        tasks = [bulb.turn_off() for bulb in self.bulbs.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        return Result(success=True, message=f"All {len(self.bulbs)} bulb(s) OFF")

    async def all_status(self) -> Result:
        if not self.bulbs:
            return Result(success=False, message="No bulbs discovered")
        statuses = []
        errors = []
        for ip in self.bulbs:
            result = await self.get_status(ip)
            if result.success:
                statuses.append(result.data)
            else:
                errors.append({"ip": ip, "error": result.message})
        return Result(
            success=True,
            message=f"Retrieved status for {len(statuses)} bulb(s)",
            data={"statuses": statuses, "errors": errors}
        )

# ── Config Loader ───────────────────────────────────────────────────────────

def load_config():
    data = json.loads(CONFIG_PATH.read_text())
    return data["user"], data["device_id"], data.get("name", ""), data.get("room", "")


# ── WebSocket Loop ──────────────────────────────────────────────────────────

async def ws_loop(controller: WiZController, user: str, ip: str):
    while True:
        try:
            iris_url = IRIS_URL.format(user=user, device_id=ip)
            print(f"[hub-ws] connecting to {iris_url}")
            
            async with websockets.connect(iris_url) as ws:
                print("[hub-ws] connected")

                # Build device struct
                info = controller.bulb_info.get(ip)
                status_result = await controller.get_status(ip)
                status = status_result.data if status_result.success else {}
                features = await controller.get_capabilities(ip)
                
                device = {
                    "id": ip,
                    "name": info.bulb_type.name if info and info.bulb_type else "WiZ Bulb",
                    "type": "leds",
                    "status": "on" if status.get("power") == "ON" else "off",
                    "value": f"{status.get('brightness', 0)}",
                    "metadata": features.to_dict().get("data") if features.to_dict().get("success") else {},
                    # "room": "",
                }
                
                await ws.send(json.dumps(device))
                
                # Command dispatch
                functions = {
                    "turn_on": controller.turn_on,
                    "turn_off": controller.turn_off,
                    "toggle": controller.toggle,
                    "set_brightness": controller.set_brightness,
                    "set_rgb": controller.set_rgb,
                    "set_scene": controller.set_scene,
                    "get_status": controller.get_status,
                    "all_on": controller.all_on,
                    "all_off": controller.all_off,
                }
                
                async for msg in ws:
                    # Build device struct
                    info = controller.bulb_info.get(ip)
                    status_result = await controller.get_status(ip)
                    status = status_result.data if status_result.success else {}
                    features = await controller.get_capabilities(ip)
                    
                    device = {
                        "id": ip,
                        "name": info.bulb_type.name if info and info.bulb_type else "WiZ Bulb",
                        "type": "leds",
                        "status": "on" if status.get("power") == "ON" else "off",
                        "value": f"{status.get('brightness', 0)}",
                        "metadata": features.to_dict().get("data") if features.to_dict().get("success") else {},
                        # "room": "",
                    }
                    
                    await ws.send(json.dumps(device))
                    
                    data: dict = json.loads(msg)
                    print(f"[hub-ws] recv: {data}")
                    
                    name = data.get("cmd")
                    args = data.get("params") or {}

                    args["ip"] = ip  # Ensure IP is always passed to functions
                    
                    if name not in functions:
                        print(f"[hub-ws] unknown function: {name}")
                        continue
                    
                    # Ensure args is dict
                    if not isinstance(args, dict):
                        try:
                            args = json.loads(args)
                        except Exception:
                            print(f"[hub-ws] bad arguments for {name}: {args}")
                            continue
                    
                    fn = functions[name]
                    print(f"[hub-ws] calling {name}({args})")
                    
                    try:
                        result: Result = await fn(**args)
                        await ws.send(json.dumps({
                            "id": data.get("id"),
                            "result": result.to_dict()
                        }))
                    except Exception as e:
                        print(f"[hub-ws] error running {name}: {e}")
                        await ws.send(json.dumps({
                            "id": data.get("id"),
                            "error": str(e)
                        }))
                        
        except Exception as e:
            print(f"[hub-ws] error, reconnecting: {e}")
            await asyncio.sleep(5)


async def main():
    user, _, _, _ = load_config()
    controller = WiZController()
    
    # Initial discovery
    discover_result = await controller.discover()
    if not discover_result.success:
        print(f"[wiz] {discover_result.message}")
        return
    
    print(f"[wiz] {discover_result.message}")

    tasks = [
        asyncio.create_task(ws_loop(controller, user, ip))
        for ip in controller.bulbs.keys()
    ]

    await asyncio.gather(*tasks, return_exceptions=True)

if __name__ == "__main__":
    asyncio.run(main())
