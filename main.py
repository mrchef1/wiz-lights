#!/usr/bin/env python3
"""
WiZ Light Controller — LLM-ready tool interface via WebSocket
"""

import asyncio
import inspect
import json
import websockets
from typing import Any, Callable, Dict, List, Optional, Set, Union
from dataclasses import dataclass
from pathlib import Path

from pywizlight import wizlight, PilotBuilder, discovery
from pywizlight.bulblibrary import BulbType
from pywizlight.scenes import SCENES, SCENES_BY_CLASS


# ── Config ───────────────────────────────────────────────────────────────────

CONFIG_PATH = Path("/home/iris/hub/config.json")
IRIS_URL = "wss://backend.irisapis.us/api/devices/ws/{user}/{device_id}"

# Seconds to wait between network scans for bulbs. Each scan also listens for
# replies for ~5s, so a full cycle is roughly SCAN_INTERVAL + 5. Sensible range: 5-60.
SCAN_INTERVAL = 15.0

# A bulb missing from this many consecutive scans is treated as offline: its
# backend connection is closed, and it's reconnected automatically as soon as a
# later scan sees it again. Broadcast discovery can occasionally miss a bulb, so
# keep this at 2 or more. Set to 0 to never disconnect a bulb.
OFFLINE_AFTER_MISSES = 3

# Only used if push updates can't be started for a bulb (e.g. UDP port 38900 is
# already taken by another program): poll the bulb at this interval instead.
FALLBACK_POLL_INTERVAL = 2.0


# ── Scene Definitions ────────────────────────────────────────────────────────

# pywizlight's SCENES ({id: name}) is the single source of truth for scene IDs.
# PilotBuilder(scene=...) expects the integer ID, so we never hardcode IDs here
# (e.g. Rhythm is 1000 in pywizlight, and 33 is Diwali).

def _normalize_scene(name: str) -> str:
    """Lowercase and strip punctuation/spaces so 'Wake up', 'wake-up' and
    'WAKE-UP' all match pywizlight's 'Wake-up'."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


_SCENE_LOOKUP: Dict[str, int] = {
    _normalize_scene(name): scene_id for scene_id, name in SCENES.items()
}


def resolve_scene_id(scene: Union[int, str]) -> int:
    """
    Turn a scene name or ID into a valid pywizlight scene ID.

    Accepts an int, a numeric string ("4"), or a scene name (case/punctuation
    insensitive). Raises ValueError if it isn't a scene pywizlight knows about.
    """
    scene_id: Optional[int] = None

    if isinstance(scene, bool):
        pass  # bool is an int subclass; never a valid scene
    elif isinstance(scene, int):
        scene_id = scene
    elif isinstance(scene, str):
        text = scene.strip()
        scene_id = int(text) if text.isdigit() else _SCENE_LOOKUP.get(_normalize_scene(text))

    if scene_id is None or scene_id not in SCENES:
        raise ValueError(f"Unknown scene: {scene!r}")
    return scene_id


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
        """
        Scan the network for bulbs. Safe to call repeatedly: bulbs we already
        track keep their existing wizlight object (and its push subscription),
        and only unknown IPs are added.

        data["seen"] = every IP that answered this scan
        data["new"]  = the subset that wasn't tracked before
        data["bulbs"] = ip/mac/name for everything in "seen"

        success is False only if the scan itself failed; finding nothing is a
        normal result (success=True, empty lists).
        """
        try:
            found = await discovery.discover_lights(broadcast_space=broadcast)
        except Exception as e:
            return Result(success=False, message=f"Discovery failed: {e}")

        seen: List[str] = []
        new: List[str] = []

        for bulb in found:
            if bulb.ip in self.bulbs:
                seen.append(bulb.ip)
                continue

            try:
                info = BulbInfo(ip=bulb.ip, mac=await bulb.getMac() or "Unknown")
                try:
                    info.bulb_type = await bulb.get_bulbtype()
                    info.name = info.bulb_type.name if info.bulb_type else "WiZ Bulb"
                except Exception:
                    info.name = "WiZ Bulb"
            except Exception as e:
                # Answered the broadcast but not a direct request; retried next scan
                print(f"[wiz] {bulb.ip} answered discovery but couldn't be queried: {e}")
                try:
                    await bulb.async_close()
                except Exception:
                    pass
                continue

            self.bulbs[bulb.ip] = bulb
            self.bulb_info[bulb.ip] = info
            seen.append(bulb.ip)
            new.append(bulb.ip)

        self.discovered = len(self.bulbs) > 0

        return Result(
            success=True,
            message=f"Found {len(seen)} device(s), {len(new)} new",
            data={
                "seen": seen,
                "new": new,
                "bulbs": [
                    {"ip": ip, "mac": self.bulb_info[ip].mac, "name": self.bulb_info[ip].name}
                    for ip in seen
                ],
            },
        )

    def add_bulb(self, ip: str) -> Result:
        if ip not in self.bulbs:
            self.bulbs[ip] = wizlight(ip)
            self.bulb_info[ip] = BulbInfo(ip=ip, mac="Manual")
            return Result(success=True, message=f"Added bulb {ip}")
        return Result(success=True, message=f"Bulb {ip} already exists")

    # ── Push Updates ─────────────────────────────────────────────────────────

    async def start_push(self, ip: str, callback: Callable[[Any], None]) -> bool:
        """
        Subscribe to the bulb's own state pushes (syncPilot). The bulb sends one
        whenever its state changes, whatever caused it: the WiZ app, a wall
        switch, a remote, one of our commands, etc.

        The callback is called synchronously from inside pywizlight, so keep it
        tiny. Returns False if push couldn't be started, in which case the
        caller should fall back to polling.
        """
        bulb = self.bulbs.get(ip)
        if bulb is None:
            return False
        try:
            await bulb.getMac()  # push subscriptions are keyed by MAC
            return bool(await bulb.start_push(callback))
        except Exception as e:
            print(f"[wiz] push setup failed for {ip}: {e}")
            return False

    async def stop_bulb(self, ip: str) -> None:
        """Stop push updates, close the bulb's socket, and stop tracking it."""
        bulb = self.bulbs.pop(ip, None)
        self.bulb_info.pop(ip, None)
        if bulb is None:
            return
        try:
            bulb.push_running = False  # ends the keep-alive re-registration chain
            if bulb.push_cancel:
                bulb.push_cancel()
                bulb.push_cancel = None
            await bulb.async_close()
        except Exception as e:
            print(f"[wiz] error closing {ip}: {e}")

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

    async def _supported_scene_names(self, ip: str) -> Optional[Set[str]]:
        """
        Scene names this specific bulb supports, per pywizlight's SCENES_BY_CLASS
        (RGB bulbs get everything; tunable-white and dimmable-white bulbs only
        a subset). Returns None if the bulb class can't be determined, in which
        case we skip the check and let the bulb decide.
        """
        try:
            bulb_type = self.bulb_info[ip].bulb_type or await self.bulbs[ip].get_bulbtype()
            names = SCENES_BY_CLASS.get(getattr(bulb_type, "bulb_type", None))
            return set(names) if names else None
        except Exception:
            return None

    async def set_scene(self, ip: str, scene_name: Union[str, int]) -> Result:
        """Set a scene by name or ID (e.g. "Ocean", "wake up", 1)."""
        if ip not in self.bulbs:
            return Result(success=False, message=f"Bulb {ip} not found")

        try:
            scene_id = resolve_scene_id(scene_name)
        except ValueError:
            return Result(
                success=False,
                message=f"Invalid scene: {scene_name!r}. Available: {', '.join(sorted(SCENES.values()))}",
            )

        label = SCENES[scene_id]

        supported = await self._supported_scene_names(ip)
        if supported is not None and label not in supported:
            return Result(
                success=False,
                message=f"Scene '{label}' isn't supported by this bulb. Supported: {', '.join(sorted(supported))}",
            )

        # PilotBuilder expects the integer scene ID
        result = await self.turn_on(ip, PilotBuilder(scene=scene_id))
        if result.success:
            result.message = f"Scene: {label}"
            result.data = {"scene_id": scene_id, "scene": label}
        return result

    async def set_scene_by_name(self, ip: str, scene_name: str) -> Result:
        # set_scene resolves names itself; kept as an alias for existing callers
        return await self.set_scene(ip, scene_name)

    async def set_rhythm(self, ip: str) -> Result:
        return await self.set_scene(ip, "Rhythm")

    # ── Status & Capabilities ────────────────────────────────────────────────

    async def get_status(self, ip: str) -> Result:
        if ip not in self.bulbs:
            return Result(success=False, message=f"Bulb {ip} not found")
        try:
            bulb = self.bulbs[ip]
            # With push running this returns the pushed state without touching
            # the network; otherwise it queries the bulb.
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


# ── Device Struct ───────────────────────────────────────────────────────────

async def build_device(
    controller: WiZController,
    ip: str,
    features: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Build the device struct the backend expects from the bulb's current state.

    Returns None if the state couldn't be read, so callers never push a
    misleading "off / 0" state to the backend.
    """
    status_result = await controller.get_status(ip)
    if not status_result.success:
        print(f"[hub-ws] status read failed for {ip}: {status_result.message}")
        return None

    status = status_result.data or {}
    info = controller.bulb_info.get(ip)

    return {
        "id": ip,
        "name": info.bulb_type.name if info and info.bulb_type else "WiZ Bulb",
        "type": "leds",
        "status": "on" if status.get("power") == "ON" else "off",
        "value": f"{status.get('brightness') or 0}",
        "metadata": {"type": "WiZ", "color_temp": status.get("color_temp"), "rgb": status.get("rgb"), "scene": status.get("scene"), "rssi": status.get("rssi"), "features": features},
        # "room": "",
    }


# ── WebSocket Loop ──────────────────────────────────────────────────────────

async def ws_loop(controller: WiZController, user: str, ip: str):
    """
    Keep one bulb connected to the backend. Runs until cancelled (which is what
    the scanner does when the bulb goes offline), then releases the bulb.
    """
    # Serialize UDP traffic to the bulb (commands + reads) and writes to the socket
    bulb_lock = asyncio.Lock()
    send_lock = asyncio.Lock()

    # Set by the bulb's push callback whenever its state changes
    state_changed = asyncio.Event()

    def on_push(_state: Any) -> None:
        # Runs synchronously inside pywizlight's UDP handler: just flag it and
        # let the publisher task do the real work.
        state_changed.set()

    # Command dispatch
    functions = {
        "turn_on": controller.turn_on,
        "turn_off": controller.turn_off,
        "toggle": controller.toggle,
        "set_brightness": controller.set_brightness,
        "set_color_temp": controller.set_color_temp,
        "set_rgb": controller.set_rgb,
        "set_scene": controller.set_scene,
        "get_status": controller.get_status,
        "all_on": controller.all_on,
        "all_off": controller.all_off,
    }

    push_active = False

    try:
        while True:
            try:
                # (Re)try push if it isn't running yet; once started it stays on
                # across backend reconnects.
                if not push_active:
                    push_active = await controller.start_push(ip, on_push)
                    if push_active:
                        print(f"[hub-ws] {ip}: push updates enabled")
                    else:
                        print(f"[hub-ws] {ip}: push unavailable, polling every {FALLBACK_POLL_INTERVAL}s instead")

                iris_url = IRIS_URL.format(user=user, device_id=ip)
                print(f"[hub-ws] connecting to {iris_url}")
                
                async with websockets.connect(iris_url) as ws:
                    print("[hub-ws] connected")

                    # Capabilities don't change, so fetch once per connection
                    features = await controller.get_capabilities(ip)

                    last_sent: Optional[Dict[str, Any]] = None

                    async def push_state(force: bool = False):
                        """Read the bulb's state and send the device struct to the
                        backend if it changed (or if forced)."""
                        nonlocal last_sent

                        # Hold the lock through read + send so an older read can
                        # never overwrite a newer one on the backend.
                        async with bulb_lock:
                            device = await build_device(controller, ip, features.data if features.success and features.data else {})
                            if device is None:
                                return

                            if not force and device == last_sent:
                                return

                            async with send_lock:
                                await ws.send(json.dumps(device))
                            last_sent = device

                    async def publisher():
                        """Keep the backend in sync with the bulb. Driven by the
                        bulb's pushes; only polls if push isn't available."""
                        while True:
                            if push_active:
                                await state_changed.wait()
                                state_changed.clear()
                            else:
                                await asyncio.sleep(FALLBACK_POLL_INTERVAL)
                            await push_state()

                    async def receiver():
                        """Handle commands from the backend."""
                        async for msg in ws:
                            try:
                                data: dict = json.loads(msg)
                            except Exception:
                                print(f"[hub-ws] bad message: {msg!r}")
                                continue

                            print(f"[hub-ws] recv: {data}")
                            
                            name = data.get("cmd")
                            args = data.get("params") or {}

                            # Ensure args is dict
                            if not isinstance(args, dict):
                                try:
                                    args = json.loads(args)
                                except Exception:
                                    print(f"[hub-ws] bad arguments for {name}: {args}")
                                    continue

                            if name not in functions:
                                print(f"[hub-ws] unknown function: {name}")
                                continue
                            
                            fn = functions[name]

                            # Per-bulb functions always target this loop's bulb (overriding any
                            # ip the caller sent). Batch functions (all_on/all_off) take no ip.
                            if "ip" in inspect.signature(fn).parameters:
                                args["ip"] = ip
                            else:
                                args.pop("ip", None)

                            print(f"[hub-ws] calling {name}({args})")
                            
                            try:
                                async with bulb_lock:
                                    result: Result = await fn(**args)
                                async with send_lock:
                                    await ws.send(json.dumps({
                                        "req": data.get("req"),
                                        "result": result.to_dict()
                                    }))
                            except websockets.ConnectionClosed:
                                raise
                            except Exception as e:
                                print(f"[hub-ws] error running {name}: {e}")
                                async with send_lock:
                                    await ws.send(json.dumps({
                                        "req": data.get("req"),
                                        "error": str(e)
                                    }))

                            # With push on, the bulb reports the new state itself a moment
                            # after the command lands (and reading now could return the
                            # pre-command state), so the publisher handles it. Without push,
                            # read it back ourselves.
                            if not push_active:
                                await push_state(force=True)

                    # Announce initial state immediately on connect
                    await push_state(force=True)

                    # Run both until one exits; if either dies, tear down and reconnect
                    tasks = [
                        asyncio.create_task(receiver()),
                        asyncio.create_task(publisher()),
                    ]
                    try:
                        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for t in done:
                            t.result()  # re-raise any exception so we hit the reconnect handler
                    finally:
                        # Also runs if this loop is cancelled, so no task is left behind
                        for t in tasks:
                            t.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)

                # Clean close from the server side
                print("[hub-ws] connection closed, reconnecting")
                await asyncio.sleep(5)
                            
            except Exception as e:
                print(f"[hub-ws] error, reconnecting: {e}")
                await asyncio.sleep(5)
    finally:
        # Cancelled (bulb went offline) or shutting down: release the bulb
        await controller.stop_bulb(ip)
        print(f"[hub-ws] {ip}: disconnected")


# ── Scanner ─────────────────────────────────────────────────────────────────

async def scan_loop(controller: WiZController, user: str):
    """
    Scan for bulbs forever. Bulbs that show up get connected to the backend;
    bulbs that stay missing for OFFLINE_AFTER_MISSES scans get disconnected, and
    reconnect on their own when a later scan finds them again.
    """
    loops: Dict[str, asyncio.Task] = {}
    missed: Dict[str, int] = {}
    previous_seen: Optional[Set[str]] = None

    try:
        while True:
            result = await controller.discover()

            if not result.success:
                # The scan itself failed (network down?): say so, but don't count
                # it against any bulb.
                print(f"[wiz] {result.message}")
            else:
                seen = set(result.data["seen"])

                if seen != previous_seen:
                    print(f"[wiz] {result.message}" + ("" if seen else f" (scanning every {SCAN_INTERVAL:g}s)"))
                    previous_seen = seen

                # Connect anything we can see that isn't connected
                for ip in seen:
                    missed[ip] = 0
                    task = loops.get(ip)
                    if task is not None and task.done():
                        reason = "cancelled" if task.cancelled() else task.exception()
                        print(f"[wiz] {ip}: connection task ended ({reason}), restarting")
                    if task is None or task.done():
                        print(f"[wiz] connecting {ip}")
                        loops[ip] = asyncio.create_task(ws_loop(controller, user, ip))

                # Disconnect bulbs that have stayed missing
                for ip in list(loops):
                    if ip in seen:
                        continue
                    missed[ip] = missed.get(ip, 0) + 1
                    if OFFLINE_AFTER_MISSES and missed[ip] >= OFFLINE_AFTER_MISSES:
                        print(f"[wiz] {ip} looks offline (missed {missed[ip]} scans), disconnecting")
                        task = loops.pop(ip)
                        missed.pop(ip, None)
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

            await asyncio.sleep(SCAN_INTERVAL)
    finally:
        for task in loops.values():
            task.cancel()
        await asyncio.gather(*loops.values(), return_exceptions=True)


async def main():
    user, _, _, _ = load_config()
    controller = WiZController()
    await scan_loop(controller, user)

if __name__ == "__main__":
    asyncio.run(main())
