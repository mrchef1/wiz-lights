#!/usr/bin/env python3
"""
WiZ App Simulator using pywizlight (FIXED for v0.6.x)
=====================================================
Fixed: updateState() returns a list in newer versions.
Use bulb.state (PilotParser) to read state after calling updateState().
"""

import asyncio
import sys
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

from pywizlight import wizlight, PilotBuilder, discovery
from pywizlight.bulblibrary import BulbType
from pywizlight.scenes import get_id_from_scene_name


# ═════════════════════════════════════════════════════════════════════════════
# WiZ Scene Definitions
# ═════════════════════════════════════════════════════════════════════════════

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


@dataclass
class BulbInfo:
    ip: str
    mac: str
    name: str = "Unknown"
    bulb_type: Optional[BulbType] = None


# ═════════════════════════════════════════════════════════════════════════════
# WiZ Controller
# ═════════════════════════════════════════════════════════════════════════════

class WiZController:
    def __init__(self):
        self.bulbs: Dict[str, wizlight] = {}
        self.bulb_info: Dict[str, BulbInfo] = {}
        self.discovered: bool = False

    # ── Discovery ────────────────────────────────────────────────────────────

    async def discover(self, broadcast: str = "255.255.255.255") -> List[BulbInfo]:
        print(f"\n🔍 Scanning network ({broadcast}) for WiZ devices...")
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
            print(f"✅ Found {len(self.bulbs)} device(s):")
            for ip, info in self.bulb_info.items():
                caps = []
                if info.bulb_type:
                    f = info.bulb_type.features
                    if f.brightness: caps.append("dim")
                    if f.color: caps.append("RGB")
                    if f.color_tmp: caps.append("CCT")
                    if f.effect: caps.append("scenes")
                cap_str = f" [{', '.join(caps)}]" if caps else ""
                print(f"   • {info.name} @ {ip} (MAC: {info.mac}){cap_str}")
        else:
            print("❌ No WiZ devices found.")
        
        return list(self.bulb_info.values())

    def add_bulb(self, ip: str) -> wizlight:
        if ip not in self.bulbs:
            bulb = wizlight(ip)
            self.bulbs[ip] = bulb
            self.bulb_info[ip] = BulbInfo(ip=ip, mac="Manual")
        return self.bulbs[ip]

    # ── Power Control ────────────────────────────────────────────────────────

    async def turn_on(self, ip: str, pilot: Optional[PilotBuilder] = None) -> bool:
        if ip not in self.bulbs:
            print(f"❌ Bulb {ip} not found.")
            return False
        try:
            await self.bulbs[ip].turn_on(pilot or PilotBuilder())
            print(f"💡 Turned ON {ip}")
            return True
        except Exception as e:
            print(f"❌ Error: {e}")
            return False

    async def turn_off(self, ip: str) -> bool:
        if ip not in self.bulbs:
            print(f"❌ Bulb {ip} not found.")
            return False
        try:
            await self.bulbs[ip].turn_off()
            print(f"🌑 Turned OFF {ip}")
            return True
        except Exception as e:
            print(f"❌ Error: {e}")
            return False

    async def toggle(self, ip: str) -> bool:
        if ip not in self.bulbs:
            print(f"❌ Bulb {ip} not found.")
            return False
        try:
            await self.bulbs[ip].lightSwitch()
            print(f"🔘 Toggled {ip}")
            return True
        except Exception as e:
            print(f"❌ Error: {e}")
            return False

    # ── Color / Brightness ───────────────────────────────────────────────────

    async def set_brightness(self, ip: str, brightness: int) -> bool:
        if not 0 <= brightness <= 255:
            print("❌ Brightness must be 0-255")
            return False
        return await self.turn_on(ip, PilotBuilder(brightness=brightness))

    async def set_color_temp(self, ip: str, kelvin: int) -> bool:
        return await self.turn_on(ip, PilotBuilder(colortemp=kelvin))

    async def set_warm_white(self, ip: str, intensity: int = 255) -> bool:
        return await self.turn_on(ip, PilotBuilder(warm_white=intensity))

    async def set_cold_white(self, ip: str, intensity: int = 255) -> bool:
        return await self.turn_on(ip, PilotBuilder(cold_white=intensity))

    async def set_rgb(self, ip: str, r: int, g: int, b: int) -> bool:
        if not all(0 <= v <= 255 for v in (r, g, b)):
            print("❌ RGB values must be 0-255")
            return False
        return await self.turn_on(ip, PilotBuilder(rgb=(r, g, b)))

    async def set_rgbw(self, ip: str, r: int, g: int, b: int, w: int) -> bool:
        return await self.turn_on(ip, PilotBuilder(rgb=(r, g, b), w=w))

    # ── Scenes ───────────────────────────────────────────────────────────────

    async def set_scene(self, ip: str, scene_name: str) -> bool:
        if scene_name not in WIZ_SCENES:
            print(f"❌ Invalid scene name: {scene_name}.")
            return False
        success = await self.turn_on(ip, PilotBuilder(scene=WIZ_SCENES[scene_name]))
        if success:
            print(f"🎭 Scene: {scene_name}")
        return success

    async def set_scene_by_name(self, ip: str, scene_name: str) -> bool:
        try:
            scene_id = get_id_from_scene_name(scene_name)
            return await self.set_scene(ip, scene_id)
        except ValueError:
            print(f"❌ Unknown scene: '{scene_name}'")
            return False

    async def set_rhythm(self, ip: str) -> bool:
        return await self.set_scene(ip, 33)

    async def get_status(self, ip: str) -> Optional[Dict[str, Any]]:
        """
        Get bulb status. Handles the case where bulb.state is a list
        containing a PilotParser (bug in some pywizlight versions).
        """
        if ip not in self.bulbs:
            print(f"❌ Bulb {ip} not found.")
            return None
        
        bulb = self.bulbs[ip]
        
        try:
            await bulb.updateState()
            raw_state = bulb.state
            
            # FIX: bulb.state might be a list [PilotParser] instead of PilotParser
            if isinstance(raw_state, list) and len(raw_state) > 0:
                state = raw_state[0]
            else:
                state = raw_state
            
            if state is None:
                print(f"⚠️  No state available for {ip}")
                return None
            
            status = {
                "ip": ip,
                "mac": self.bulb_info.get(ip, BulbInfo(ip=ip, mac="?")).mac,
                "power": "ON" if state.get_state() else "OFF",
                "brightness": state.get_brightness(),
                "color_temp": state.get_colortemp(),
                "rgb": state.get_rgb(),
                "warm_white": state.get_warm_white(),
                "cold_white": state.get_cold_white(),
                "scene": state.get_scene(),
                "rssi": getattr(state, 'rssi', None),
            }
            return status
            
        except Exception as e:
            print(f"❌ Error getting status for {ip}: {e}")
            return None
        
    async def print_status(self, ip: str) -> None:
        status = await self.get_status(ip)
        if not status:
            return
        
        print(f"\n📊 Status for {ip}:")
        print(f"   Power:      {status['power']}")
        print(f"   Brightness: {status['brightness']}/255")
        if status['color_temp']:
            print(f"   Color Temp: {status['color_temp']}K")
        if status['rgb']:
            print(f"   RGB:        {status['rgb']}")
        if status['scene']:
            print(f"   Scene:      {status['scene']}")
        if status['rssi']:
            print(f"   WiFi RSSI:  {status['rssi']} dBm")

    async def get_capabilities(self, ip: str) -> None:
        if ip not in self.bulbs:
            print(f"❌ Bulb {ip} not found.")
            return
        try:
            bulb_type = await self.bulbs[ip].get_bulbtype()
            if not bulb_type:
                print("Could not retrieve capabilities.")
                return
            f = bulb_type.features
            print(f"\n🔧 Capabilities for {ip}:")
            print(f"   Model:      {bulb_type.name}")
            print(f"   Brightness: {'✅' if f.brightness else '❌'}")
            print(f"   Color:      {'✅' if f.color else '❌'}")
            print(f"   Color Temp: {'✅' if f.color_tmp else '❌'}")
            print(f"   Effects:    {'✅' if f.effect else '❌'}")
            if f.color_tmp:
                print(f"   Kelvin:     {bulb_type.kelvin_range.min}K - {bulb_type.kelvin_range.max}K")
        except Exception as e:
            print(f"❌ Error: {e}")

    # ── Batch Operations ─────────────────────────────────────────────────────

    async def all_on(self, pilot: Optional[PilotBuilder] = None) -> None:
        if not self.bulbs:
            print("❌ No bulbs discovered.")
            return
        tasks = [bulb.turn_on(pilot or PilotBuilder()) for bulb in self.bulbs.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        print(f"💡 All {len(self.bulbs)} bulb(s) ON")

    async def all_off(self) -> None:
        if not self.bulbs:
            print("❌ No bulbs discovered.")
            return
        tasks = [bulb.turn_off() for bulb in self.bulbs.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        print(f"🌑 All {len(self.bulbs)} bulb(s) OFF")

    async def all_status(self) -> None:
        if not self.bulbs:
            print("❌ No bulbs discovered.")
            return
        for ip in self.bulbs:
            await self.print_status(ip)


# ═════════════════════════════════════════════════════════════════════════════
# Interactive Menu
# ═════════════════════════════════════════════════════════════════════════════

def print_menu() -> None:
    print("\n" + "═" * 50)
    print("         🏠 WiZ APP SIMULATOR")
    print("═" * 50)
    print("  1. 🔍 Discover devices")
    print("  2. 💡 Turn ON bulb")
    print("  3. 🌑 Turn OFF bulb")
    print("  4. 🔘 Toggle bulb")
    print("  5. 🔆 Set brightness")
    print("  6. 🌡️  Set color temperature")
    print("  7. 🎨 Set RGB color")
    print("  8. 🎭 Set scene")
    print("  9. 📊 Get bulb status")
    print(" 10. 🔧 Get bulb capabilities")
    print(" 11. 🌅 Set warm white")
    print(" 12. ❄️  Set cold white")
    print(" 13. 🌊 Set Rhythm mode")
    print(" 14. 💡 Turn ALL on")
    print(" 15. 🌑 Turn ALL off")
    print(" 16. 📊 Status for ALL")
    print("  0. 🚪 Exit")
    print("═" * 50)


def select_bulb(controller: WiZController) -> Optional[str]:
    if not controller.bulbs:
        print("❌ No bulbs discovered. Run option 1 first.")
        return None
    print("\n📱 Discovered devices:")
    ips = list(controller.bulbs.keys())
    for i, ip in enumerate(ips, 1):
        info = controller.bulb_info[ip]
        print(f"   {i}. {info.name} @ {ip}")
    try:
        choice = int(input("\nSelect device number: ")) - 1
        if 0 <= choice < len(ips):
            return ips[choice]
        print("❌ Invalid selection.")
        return None
    except ValueError:
        print("❌ Please enter a number.")
        return None


def print_scenes() -> None:
    print("\n🎭 Available Scenes:")
    for name, sid in WIZ_SCENES.items():
        print(f"   {sid:2d}. {name}")


async def interactive_menu(controller: WiZController) -> None:
    await controller.discover("255.255.255.255")
	
	ips = list(controller.bulbs.keys())
	for ip in ips:
		await controller.print_status(ip)
		await controller.get_capabilities(ip)
		await controller.toggle(ip)

# ═════════════════════════════════════════════════════════════════════════════
# Entry Point
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="WiZ App Simulator")
    parser.add_argument("--demo", action="store_true", help="Run quick demo")
    parser.add_argument("--ip", type=str, help="Control bulb by IP directly")
    parser.add_argument("--on", action="store_true")
    parser.add_argument("--off", action="store_true")
    parser.add_argument("--brightness", type=int)
    parser.add_argument("--kelvin", type=int)
    parser.add_argument("--rgb", type=str)
    parser.add_argument("--scene", type=int)
    parser.add_argument("--discover", action="store_true")
    args = parser.parse_args()
    
    controller = WiZController()
    
    if args.demo:
        # Quick demo
        async def demo():
            bulbs = await controller.discover("10.0.0.255")
            if bulbs:
                await controller.print_status(bulbs[0].ip)
                await controller.set_rgb(bulbs[0].ip, 255, 0, 0)
                await asyncio.sleep(2)
                await controller.turn_off(bulbs[0].ip)
        asyncio.run(demo())
        return
    
    if args.ip:
        controller.add_bulb(args.ip)
        if args.discover:
            asyncio.run(controller.get_capabilities(args.ip))
            return
        pilot = PilotBuilder()
        if args.brightness is not None: pilot = PilotBuilder(brightness=args.brightness)
        if args.kelvin: pilot = PilotBuilder(colortemp=args.kelvin)
        if args.rgb:
            r, g, b = map(int, args.rgb.split(","))
            pilot = PilotBuilder(rgb=(r, g, b))
        if args.scene: pilot = PilotBuilder(scene=args.scene)
        if args.on:
            asyncio.run(controller.turn_on(args.ip, pilot))
        elif args.off:
            asyncio.run(controller.turn_off(args.ip))
        elif any([args.brightness, args.kelvin, args.rgb, args.scene]):
            asyncio.run(controller.turn_on(args.ip, pilot))
        else:
            asyncio.run(controller.print_status(args.ip))
        return
    
    if args.discover:
        asyncio.run(controller.discover())
        return
    
    print("🚀 WiZ App Simulator")
    asyncio.run(interactive_menu(controller))


if __name__ == "__main__":
    main()
