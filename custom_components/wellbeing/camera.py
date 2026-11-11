"""Camera platform for Wellbeing (3D-Vision Measured Floorplan & Interactive Zone Renderer)."""

from __future__ import annotations

import glob
import io
import json
import logging
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from homeassistant.components.camera import Camera
from homeassistant.const import Platform
from homeassistant.util import slugify
from homeassistant.util.percentage import ranged_value_to_percentage

from .api import Model
from .const import DEFAULT_NAME, DOMAIN
from .entity import WellbeingEntity
from .vacuum import DUSTBIN_LABELS_DE, ROBOT_STATUS_LABELS_DE

_LOGGER: logging.Logger = logging.getLogger(__package__)

ZONE_PALETTE = [
    ((56, 189, 248, 70), (56, 189, 248, 235)),    # Sky Cyan
    ((168, 85, 247, 70), (192, 132, 252, 235)),   # Purple
    ((245, 158, 11, 70), (251, 191, 36, 235)),    # Amber
    ((16, 185, 129, 70), (52, 211, 153, 235)),    # Emerald
    ((236, 72, 153, 70), (244, 114, 182, 235)),   # Pink
    ((99, 102, 241, 70), (129, 140, 248, 235)),   # Indigo
]

ACTIVE_ZONE_COLOR = ((34, 197, 94, 130), (74, 222, 128, 255))

POWER_MODE_LABELS = {
    1: "Quiet",
    2: "Smart",
    3: "Power",
}

ROBOT_WIDTH_M = 0.34
MAX_SEGMENT_M = 0.65
RAW_MAPS_PATH = Path("/config/.storage/wellbeing_raw_maps.json")
AEG_CRED_PATH = Path("/config/.storage/wellbeing_aeg_credentials.json")
_RAW_MAPS_CACHE: dict[str, Any] = {}
_FAILED_MAP_IDS: set[str] = set()


def _fetch_missing_raw_map(pnc_id: str, map_id: str) -> dict[str, Any] | None:
    """Automatically fetch a new/updated rawgzip 3D-Vision scan from the internal AEGOneApp API."""
    if map_id in _FAILED_MAP_IDS or not AEG_CRED_PATH.exists():
        return None
    try:
        import base64, gzip, hashlib, hmac, random, time, urllib.parse, requests
        creds = json.loads(AEG_CRED_PATH.read_text())
        email, password = creds["email"], creds["password"]
        country = creds.get("country", "DE")
        cid = creds.get("client_id", "AEGOneApp")
        csec = creds.get("client_secret", "")
        ua = creds.get("user_agent", "AEG/2.26 android/10")
        xkey = creds.get("x_api_key", "")
        if not csec or not xkey:
            _LOGGER.debug("AEG client_secret or x_api_key missing in credentials file")
            return None
        base = "https://api.ocp.electrolux.one"

        cc_tok = requests.post(
            f"{base}/one-account-authorization/api/v1/token",
            json={"clientId": cid, "clientSecret": csec, "grantType": "client_credentials", "scope": ""},
            headers={"x-api-key": xkey, "User-Agent": ua, "Authorization": "Bearer"},
            timeout=10,
        ).json()["accessToken"]

        idp = requests.get(
            f"{base}/one-account-user/api/v1/identity-providers?brand=aeg&countryCode={country}",
            headers={"Authorization": f"Bearer {cc_tok}", "x-api-key": xkey, "User-Agent": ua, "Context-Brand": "aeg"},
            timeout=10,
        ).json()[0]
        reg_base, g_key, g_dom = idp["httpRegionalBaseUrl"], idp["apiKey"], idp["domain"]

        nonce = lambda: f"{round(time.time() * 1000)}_-" + str(random.randint(100000000, 999999999))
        gua = "okhttp/4.10.0"
        r1 = requests.post(
            f"https://socialize.{g_dom}/socialize.getIDs",
            headers={"apikey": g_key, "user-agent": gua, "content-type": "application/x-www-form-urlencoded"},
            data={"apiKey": g_key, "format": "json", "httpStatusCodes": "false", "nonce": nonce(), "sdk": "Android_7.0.11", "targetEnv": "mobile"},
            timeout=10,
        ).json()
        r2 = requests.post(
            f"https://accounts.{g_dom}/accounts.login",
            headers={"apikey": g_key, "user-agent": gua, "content-type": "application/x-www-form-urlencoded"},
            data={"apiKey": g_key, "format": "json", "httpStatusCodes": "false", "nonce": nonce(), "sdk": "Android_7.0.11", "targetEnv": "mobile", "gmid": r1["gmid"], "ucid": r1["ucid"], "loginID": email, "password": password},
            timeout=10,
        ).json()
        params = {
            "apiKey": g_key, "fields": "country", "format": "json", "gmid": r1["gmid"], "httpStatusCodes": "false",
            "nonce": nonce(), "oauth_token": r2["sessionInfo"]["sessionToken"], "sdk": "Android_7.0.11",
            "targetEnv": "mobile", "timestamp": r2["signatureTimestamp"], "ucid": r1["ucid"],
        }
        jurl = f"https://accounts.{g_dom}/accounts.getJWT"
        pq = urllib.parse.quote(urllib.parse.urlencode(params))
        skey = base64.b64decode(r2["sessionInfo"]["sessionSecret"].encode("ascii"))
        base_sig = f"POST&{urllib.parse.quote(jurl, safe='')}&{pq}"
        params["sig"] = base64.urlsafe_b64encode(hmac.new(skey, base_sig.encode("utf-8"), hashlib.sha1).digest()).decode("ascii")
        r3 = requests.post(jurl, headers={"apikey": g_key, "user-agent": gua, "content-type": "application/x-www-form-urlencoded"}, data=params, timeout=10).json()
        r4 = requests.post(
            f"{base}/one-account-authorization/api/v1/token",
            json={"clientId": cid, "idToken": r3["id_token"], "grantType": "urn:ietf:params:oauth:grant-type:token-exchange"},
            headers={"Origin-Country-Code": r2["profile"]["country"], "User-Agent": ua, "x-api-key": xkey},
            timeout=10,
        ).json()
        hdrs = {"Authorization": f"Bearer {r4['accessToken']}", "x-api-key": xkey, "User-Agent": ua, "Context-Brand": "aeg"}
        maps = requests.get(f"{reg_base}/purei/api/v2/appliances/{pnc_id}/interactive-maps", headers=hdrs, timeout=10).json()
        for m in maps:
            mid = m["id"]
            seq = m.get("sequenceNumber", 0)
            rm = requests.get(f"{reg_base}/purei/api/v2/appliances/{pnc_id}/interactive-maps/{mid}/sequences/{seq}/maps", headers=hdrs, params={"mapFormat": "rawgzip"}, timeout=15)
            if rm.status_code == 200:
                _RAW_MAPS_CACHE.setdefault(pnc_id, {}).setdefault("maps", {})[mid] = json.loads(gzip.decompress(rm.content).decode("utf-8"))
        RAW_MAPS_PATH.write_text(json.dumps(_RAW_MAPS_CACHE))
        return _RAW_MAPS_CACHE.get(pnc_id, {}).get("maps", {}).get(map_id)
    except Exception as err:
        _LOGGER.warning("Auto-sync raw map %s failed: %s", map_id, err)
        _FAILED_MAP_IDS.add(map_id)
        return None


def _get_raw_map(pnc_id: str, map_id: str | None) -> dict[str, Any] | None:
    global _RAW_MAPS_CACHE
    if not map_id:
        return None
    if not _RAW_MAPS_CACHE and RAW_MAPS_PATH.exists():
        try:
            _RAW_MAPS_CACHE = json.loads(RAW_MAPS_PATH.read_text())
        except Exception as err:
            _LOGGER.warning("Could not load cached_raw_maps.json: %s", err)
    res = _RAW_MAPS_CACHE.get(pnc_id, {}).get("maps", {}).get(map_id)
    if res is None:
        res = _fetch_missing_raw_map(pnc_id, map_id)
    return res


def _load_font(size: int, bold: bool = False):
    pattern = "DejaVu-Sans-Bold.ttf" if bold else "DejaVu-Sans.ttf"
    matches = glob.glob(f"/usr/local/lib/python*/site-packages/aioslimproto/font/{pattern}")
    if not matches and not bold:
        matches = glob.glob("/usr/local/lib/python*/site-packages/env_canada/DejaVuSans.ttf")
    if matches:
        try:
            return ImageFont.truetype(matches[0], size=size)
        except Exception:
            pass
    return ImageFont.load_default(size=size)


def _rotate_pt(x: float, y: float, rad: float) -> tuple[float, float]:
    c, s = math.cos(rad), math.sin(rad)
    return (x * c - y * s, x * s + y * c)


def _to_global(pt: list[float] | tuple[float, float], transform: list[float]) -> tuple[float, float]:
    tx, ty, a = transform
    return _rotate_pt(pt[0] - tx, pt[1] - ty, -a)


def _split_runs(chunk: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    if not chunk:
        return []
    runs, run = [], [chunk[0]]
    for prev, cur in zip(chunk, chunk[1:]):
        if math.dist(prev, cur) > MAX_SEGMENT_M:
            runs.append(run)
            run = [cur]
        else:
            run.append(cur)
    runs.append(run)
    return runs


def _polygon_area_sqm(vertices: list[dict[str, float]]) -> float:
    """Compute polygon area in m^2 using the Shoelace formula."""
    if len(vertices) < 3:
        return 0.0
    area = 0.0
    n = len(vertices)
    for i in range(n):
        j = (i + 1) % n
        area += float(vertices[i]["x"]) * float(vertices[j]["y"])
        area -= float(vertices[j]["x"]) * float(vertices[i]["y"])
    return abs(area) / 2.0


async def async_setup_entry(hass, entry, async_add_devices):
    """Setup camera platform for Wellbeing PUREi9 vacuums."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    appliances = coordinator.data.get("appliances", None)

    if appliances is not None:
        cameras = [
            WellbeingMapCamera(coordinator, entry, pnc_id)
            for pnc_id, appliance in appliances.appliances.items()
            if appliance.model == Model.PUREi9
        ]
        async_add_devices(cameras)


class WellbeingMapCamera(WellbeingEntity, Camera):
    """Renders the 3D-Vision Measured Map and Interactive Zones for PUREi9 / AEG RX9 vacuums."""

    def __init__(self, coordinator, config_entry, pnc_id):
        WellbeingEntity.__init__(
            self, coordinator, config_entry, pnc_id, Platform.CAMERA, "mapData"
        )
        Camera.__init__(self)
        self.content_type = "image/png"
        self.entity_id = f"camera.{slugify(f'{DEFAULT_NAME}_{self.get_appliance.display_name}_mapData')}"
        self._last_map_id = None
        self._last_rev = None

    def _handle_coordinator_update(self) -> None:
        selected_map = self.api.get_selected_map(self.pnc_id)
        cur_id = selected_map.get("id") if selected_map else None
        cur_rev = self.api.map_revision
        if cur_id != self._last_map_id or cur_rev != self._last_rev:
            self._last_map_id = cur_id
            self._last_rev = cur_rev
            self.async_update_token()
        super()._handle_coordinator_update()

    @property
    def name(self) -> str:
        return "Map"

    @property
    def unique_id(self) -> str:
        return f"{self.config_entry.entry_id}-mapData-{self.pnc_id}"

    @property
    def device_class(self) -> str | None:
        return None

    @property
    def entity_category(self):
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        selected_map = self.api.get_selected_map(self.pnc_id)
        all_maps = self.api.get_interactive_maps(self.pnc_id)
        raw_map = _get_raw_map(self.pnc_id, selected_map.get("id") if selected_map else None)
        zones = []
        if selected_map:
            zones = [
                {
                    "name": z["name"],
                    "id": z["id"],
                    "power_mode": POWER_MODE_LABELS.get(z.get("power_mode"), str(z.get("power_mode"))),
                    "area_sqm": round(_polygon_area_sqm(z.get("vertices") or []), 1),
                }
                for z in selected_map.get("zones", [])
                if z.get("type") == "clean"
            ]
        return {
            "active_map": selected_map.get("name") if selected_map else None,
            "active_map_id": selected_map.get("id") if selected_map else None,
            "available_maps": [m["name"] for m in all_maps],
            "zones": zones,
            "active_cleaning_zones": self.api.get_active_cleaning_zones(self.pnc_id),
            "measured_crumb_points": len(raw_map.get("crumbs", [])) if raw_map else 0,
            "measured_session_id": raw_map.get("sessionId") if raw_map else None,
            "map_revision": self.api.map_revision,
        }

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Render the current map as a PNG image."""
        return await self.hass.async_add_executor_job(
            self._render_map_png, width or 1100, height or 660
        )

    def _render_map_png(self, width: int = 1100, height: int = 660) -> bytes:
        w = max(640, min(width, 1400))
        h = max(480, min(height, 1000))

        img = Image.new("RGBA", (w, h), (13, 19, 33, 255))
        draw = ImageDraw.Draw(img)

        font_title = _load_font(25, bold=True)
        font_sub = _load_font(16, bold=False)
        font_badge = _load_font(16, bold=True)
        font_zone = _load_font(20, bold=True)
        font_zone_sub = _load_font(15, bold=False)
        font_small = _load_font(14, bold=False)

        app = self.get_appliance
        reported = app.reported_state or {}
        raw_status = reported.get("robotStatus", 10)
        status_text = ROBOT_STATUS_LABELS_DE.get(raw_status, f"Status {raw_status}")
        dustbin_raw = str(reported.get("dustbinStatus", "connected"))
        dustbin_ok = dustbin_raw.casefold() in ("connected", "empty", "notconnected")
        dustbin_text = DUSTBIN_LABELS_DE.get(dustbin_raw.casefold(), dustbin_raw)

        bat_raw = reported.get("batteryStatus", 6)
        bat_pct = max(0, min(100, round(ranged_value_to_percentage(app.battery_range, bat_raw))))
        fan_speed = (app.vacuum_fan_speed or "smart").upper()

        selected_map = self.api.get_selected_map(self.pnc_id)
        all_maps = self.api.get_interactive_maps(self.pnc_id)
        raw_map = _get_raw_map(self.pnc_id, selected_map.get("id") if selected_map else None)
        active_cleaning = set(self.api.get_active_cleaning_zones(self.pnc_id))
        is_cleaning = raw_status in (1, 3)

        header_h = 84
        footer_h = 44
        canvas_top = header_h
        canvas_bottom = h - footer_h

        # Subtle background grid
        for gx in range(0, w, 40):
            draw.line([(gx, canvas_top), (gx, canvas_bottom)], fill=(26, 36, 54, 255), width=1)
        for gy in range(canvas_top, canvas_bottom, 40):
            draw.line([(0, gy), (w, gy)], fill=(26, 36, 54, 255), width=1)

        # Header bar
        draw.rectangle([(0, 0), (w, header_h)], fill=(20, 29, 47, 255))
        draw.line([(0, header_h), (w, header_h)], fill=(51, 65, 85, 255), width=2)

        robot_title = f"{app.display_name.strip()}  •  AEG {(app.model_name or 'RX9').upper()}"
        map_name = selected_map["name"] if selected_map else "Keine Karte"
        crumb_cnt = len(raw_map.get("crumbs", [])) if raw_map else 0
        scan_badge = f"3D-Vision Scan ({crumb_cnt} Messpunkte)" if crumb_cnt else f"{len(all_maps)} Karten in Cloud"

        draw.text((22, 15), robot_title, fill=(248, 250, 252, 255), font=font_title)
        draw.text(
            (22, 47),
            f"Aktive Karte: {map_name}   •   {scan_badge}",
            fill=(148, 163, 184, 255),
            font=font_sub,
        )

        # Status & Telemetry Pill
        if is_cleaning:
            st_bg, st_fg = (12, 58, 42, 255), (74, 222, 128, 255)
        elif raw_status in (5, 7, 12):
            st_bg, st_fg = (68, 44, 12, 255), (251, 191, 36, 255)
        elif raw_status == 11 or not dustbin_ok:
            st_bg, st_fg = (69, 20, 26, 255), (248, 113, 113, 255)
        else:
            st_bg, st_fg = (15, 44, 73, 255), (56, 189, 248, 255)

        pill_text = f"{status_text}  |  Akku: {bat_pct}%  |  Modus: {fan_speed}"
        bbox = draw.textbbox((0, 0), pill_text, font=font_badge)
        pw = bbox[2] - bbox[0] + 28
        draw.rounded_rectangle(
            [(w - pw - 20, 14), (w - 20, 44)],
            radius=8,
            fill=st_bg,
            outline=st_fg,
            width=2,
        )
        draw.text((w - pw - 6, 21), pill_text, fill=st_fg, font=font_badge)

        db_color = (74, 222, 128, 255) if dustbin_ok else (248, 113, 113, 255)
        db_label = f"Staubbehälter: {dustbin_text}"
        db_bbox = draw.textbbox((0, 0), db_label, font=font_small)
        draw.text((w - (db_bbox[2] - db_bbox[0]) - 22, 52), db_label, fill=db_color, font=font_small)

        zones = (selected_map or {}).get("zones") or []
        clean_zones = [z for z in zones if z.get("vertices") and len(z["vertices"]) >= 3]
        rot = float((selected_map or {}).get("rotation") or 0.0) - (math.pi / 2.0)

        # Gather all rotated world coordinates from both 3D-Vision scan crumbs & interactive zones
        all_pts: list[tuple[float, float]] = []
        crumb_chunks: list[tuple[int, list[tuple[float, float]]]] = []
        charger_pt: tuple[float, float] | None = None

        if raw_map and raw_map.get("crumbs"):
            transforms = {
                t["t"]: t["xya"]
                for t in raw_map.get("transforms", [])
                if len(t.get("xya", [])) == 3
            }
            identity = [0.0, 0.0, 0.0]
            for c in raw_map["crumbs"]:
                if "xy" not in c:
                    continue
                g_pt = _to_global(c["xy"], transforms.get(c.get("t"), identity))
                r_pt = _rotate_pt(g_pt[0], g_pt[1], rot)
                all_pts.append(r_pt)
                if crumb_chunks and crumb_chunks[-1][0] == c.get("t"):
                    crumb_chunks[-1][1].append(r_pt)
                else:
                    crumb_chunks.append((c.get("t"), [r_pt]))

            if 1000 in transforms:
                cg = _to_global((0.0, 0.0), transforms[1000])
                charger_pt = _rotate_pt(cg[0], cg[1], rot)
                all_pts.append(charger_pt)
            elif crumb_chunks and crumb_chunks[0][1]:
                charger_pt = crumb_chunks[0][1][0]

        for z in clean_zones:
            for v in z["vertices"]:
                all_pts.append(_rotate_pt(float(v["x"]), float(v["y"]), rot))

        if all_pts:
            min_x = min(p[0] for p in all_pts) - 0.22
            max_x = max(p[0] for p in all_pts) + 0.22
            min_y = min(p[1] for p in all_pts) - 0.22
            max_y = max(p[1] for p in all_pts) + 0.22

            span_x = max(max_x - min_x, 1.0)
            span_y = max(max_y - min_y, 1.0)

            pad_x = 32
            pad_y = 24
            avail_w = w - 2 * pad_x
            avail_h = (canvas_bottom - canvas_top) - 2 * pad_y
            scale = min(avail_w / span_x, avail_h / span_y)

            offset_x = pad_x + (avail_w - span_x * scale) / 2.0
            offset_y = canvas_top + pad_y + (avail_h - span_y * scale) / 2.0

            def _rot_to_px(rx: float, ry: float) -> tuple[float, float]:
                px = offset_x + (rx - min_x) * scale
                py = offset_y + (max_y - ry) * scale
                return (px, py)

            def _world_to_px(x: float, y: float) -> tuple[float, float]:
                rx, ry = _rotate_pt(x, y, rot)
                return _rot_to_px(rx, ry)

            # Layer 1: Draw Measured 3D-Vision Scan (Outer Walls + Scanned Floor Swath + Robot Path)
            if crumb_chunks:
                scan_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                sdraw = ImageDraw.Draw(scan_layer)
                swath_px = max(int(ROBOT_WIDTH_M * scale), 10)
                wall_px = swath_px + max(int(0.12 * scale), 6)

                # 1A: Outer Wall / Obstacle Perimeter (light slate wall boundary)
                for _, chunk in crumb_chunks:
                    for run in _split_runs(chunk):
                        pts = [_rot_to_px(p[0], p[1]) for p in run]
                        if len(pts) > 1:
                            sdraw.line(pts, fill=(148, 163, 184, 240), width=wall_px, joint="curve")
                        for p in (pts[0], pts[-1]):
                            r = wall_px / 2.0
                            sdraw.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=(148, 163, 184, 240))

                # 1B: Scanned Room Floor Interior Swath
                for _, chunk in crumb_chunks:
                    for run in _split_runs(chunk):
                        pts = [_rot_to_px(p[0], p[1]) for p in run]
                        if len(pts) > 1:
                            sdraw.line(pts, fill=(43, 76, 126, 255), width=swath_px, joint="curve")
                        for p in (pts[0], pts[-1]):
                            r = swath_px / 2.0
                            sdraw.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=(43, 76, 126, 255))

                # 1C: Driven Cleaning Trajectory Path
                path_w = max(2, int(scale / 26))
                for _, chunk in crumb_chunks:
                    for run in _split_runs(chunk):
                        pts = [_rot_to_px(p[0], p[1]) for p in run]
                        if len(pts) > 1:
                            sdraw.line(pts, fill=(125, 211, 252, 150), width=path_w, joint="curve")

                img = Image.alpha_composite(img, scan_layer)
                draw = ImageDraw.Draw(img)

            # Layer 2: Draw translucent interactive room zones on top of the measured scan
            overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            odraw = ImageDraw.Draw(overlay)
            label_items = []
            total_sqm = 0.0

            for idx, z in enumerate(clean_zones):
                verts = z["vertices"]
                poly_px = [_world_to_px(float(v["x"]), float(v["y"])) for v in verts]
                z_name = z.get("name", f"Zone {idx + 1}")
                is_zone_active = (z_name in active_cleaning) or (is_cleaning and not active_cleaning)

                fill_col, border_col = (
                    ACTIVE_ZONE_COLOR
                    if is_zone_active
                    else ZONE_PALETTE[idx % len(ZONE_PALETTE)]
                )

                odraw.polygon(poly_px, fill=fill_col)
                closed_pts = [*poly_px, poly_px[0]]
                odraw.line(closed_pts, fill=border_col, width=3, joint="curve")

                for px, py in poly_px:
                    odraw.ellipse([(px - 4, py - 4), (px + 4, py + 4)], fill=border_col)

                cx = sum(p[0] for p in poly_px) / len(poly_px)
                cy = sum(p[1] for p in poly_px) / len(poly_px)
                sqm = _polygon_area_sqm(verts)
                total_sqm += sqm
                pm_str = POWER_MODE_LABELS.get(z.get("power_mode"), "Smart")
                sub_str = f"{sqm:.1f} m² • {pm_str}"
                if z_name in active_cleaning:
                    sub_str = f"▶ REINIGT ({sqm:.1f} m²)"

                tb1 = odraw.textbbox((0, 0), z_name, font=font_zone)
                tb2 = odraw.textbbox((0, 0), sub_str, font=font_zone_sub)
                box_w = max(tb1[2] - tb1[0], tb2[2] - tb2[0]) + 28
                box_h = 54

                label_items.append(
                    {
                        "cx": cx,
                        "cy": cy,
                        "w": box_w,
                        "h": box_h,
                        "name": z_name,
                        "sub": sub_str,
                        "border": border_col,
                        "tw1": tb1[2] - tb1[0],
                        "tw2": tb2[2] - tb2[0],
                    }
                )

            img = Image.alpha_composite(img, overlay)
            draw = ImageDraw.Draw(img)

            # Layer 3: Draw Charger Dock & Parked Robot Marker
            if charger_pt is not None:
                csx, csy = _rot_to_px(charger_pt[0], charger_pt[1])
                dock_r = max(11, int(0.18 * scale))
                draw.ellipse(
                    [(csx - dock_r - 4, csy - dock_r - 4), (csx + dock_r + 4, csy + dock_r + 4)],
                    fill=(16, 185, 129, 90),
                    outline=(52, 211, 153, 230),
                    width=2,
                )
                draw.ellipse(
                    [(csx - dock_r, csy - dock_r), (csx + dock_r, csy + dock_r)],
                    fill=(16, 185, 129, 255),
                    outline=(240, 253, 244, 255),
                    width=2,
                )
                draw.line([(csx, csy - dock_r * 0.5), (csx, csy + dock_r * 0.5)], fill=(15, 23, 42, 255), width=3)
                draw.line([(csx - dock_r * 0.45, csy), (csx + dock_r * 0.45, csy)], fill=(15, 23, 42, 255), width=3)

                dl_txt = "Ladestation"
                dl_bb = draw.textbbox((0, 0), dl_txt, font=font_zone_sub)
                dl_w = dl_bb[2] - dl_bb[0] + 12
                dl_h = dl_bb[3] - dl_bb[1] + 6
                draw.rounded_rectangle(
                    [(csx - dl_w / 2, csy + dock_r + 5), (csx + dl_w / 2, csy + dock_r + 5 + dl_h)],
                    radius=5,
                    fill=(6, 78, 59, 235),
                    outline=(52, 211, 153, 210),
                    width=1,
                )
                draw.text((csx - dl_w / 2 + 6, csy + dock_r + 7), dl_txt, fill=(209, 250, 229, 255), font=font_zone_sub)

            # Push room label boxes away from the Charger Dock so both remain 100% readable
            if charger_pt is not None:
                csx, csy = _rot_to_px(charger_pt[0], charger_pt[1])
                for item in label_items:
                    dx = item["cx"] - csx
                    dy = item["cy"] - (csy + 10)
                    min_dx = item["w"] / 2 + 48
                    min_dy = item["h"] / 2 + 30
                    if abs(dx) < min_dx and abs(dy) < min_dy:
                        item["cy"] = (csy - min_dy - 4) if dy < 0 else (csy + min_dy + 14)

            # De-overlap adjacent label boxes (e.g. Badezimmer & Schlafzimmer)
            for i in range(len(label_items)):
                for j in range(i + 1, len(label_items)):
                    a, b = label_items[i], label_items[j]
                    dx = b["cx"] - a["cx"]
                    dy = b["cy"] - a["cy"]
                    min_dx = (a["w"] + b["w"]) / 2 + 8
                    min_dy = (a["h"] + b["h"]) / 2 + 6
                    if abs(dx) < min_dx and abs(dy) < min_dy:
                        shift_y = (min_dy - abs(dy)) / 2 + 2
                        if dy >= 0:
                            a["cy"] -= shift_y
                            b["cy"] += shift_y
                        else:
                            a["cy"] += shift_y
                            b["cy"] -= shift_y

            # Layer 4: Draw crisp room label badges on top
            for item in label_items:
                cx, cy, bw, bh = item["cx"], item["cy"], item["w"], item["h"]
                draw.rounded_rectangle(
                    [(cx - bw / 2, cy - bh / 2), (cx + bw / 2, cy + bh / 2)],
                    radius=8,
                    fill=(15, 23, 42, 235),
                    outline=item["border"],
                    width=2,
                )
                draw.text(
                    (cx - item["tw1"] / 2, cy - 23),
                    item["name"],
                    fill=(248, 250, 252, 255),
                    font=font_zone,
                )
                draw.text(
                    (cx - item["tw2"] / 2, cy + 4),
                    item["sub"],
                    fill=item["border"],
                    font=font_zone_sub,
                )

            # Scale bar & area/scan indicator
            bar_m = 2.0
            bar_px = bar_m * scale
            bx0, by0 = 26, canvas_bottom - 22
            draw.line([(bx0, by0), (bx0 + bar_px, by0)], fill=(148, 163, 184, 255), width=3)
            draw.line([(bx0, by0 - 5), (bx0, by0 + 5)], fill=(148, 163, 184, 255), width=2)
            draw.line([(bx0 + bar_px, by0 - 5), (bx0 + bar_px, by0 + 5)], fill=(148, 163, 184, 255), width=2)
            if total_sqm > 0:
                scale_info = f"2.0 m   |   Zonenfläche: {total_sqm:.1f} m²   |   3D-Vision Scan ({crumb_cnt} Punkte)"
            else:
                ts_raw = ((raw_map or {}).get("timestamp") or {}).get("time", "")[:10]
                scale_info = f"2.0 m   |   Vermessene Etagenkarte ({crumb_cnt} Scan-Punkte, Stand: {ts_raw})"
            draw.text(
                (bx0 + bar_px + 10, by0 - 8),
                scale_info,
                fill=(148, 163, 184, 255),
                font=font_small,
            )

        # Footer Bar
        draw.rectangle([(0, canvas_bottom), (w, h)], fill=(20, 29, 47, 255))
        draw.line([(0, canvas_bottom), (w, canvas_bottom)], fill=(51, 65, 85, 255), width=1)
        all_map_names = "  •  ".join(
            f"[{m['name']}]" if m["name"] == map_name else m["name"] for m in all_maps
        )
        footer_left = f"Verfügbare Karten: {all_map_names or 'Keine'}"
        draw.text((22, canvas_bottom + 14), footer_left, fill=(148, 163, 184, 255), font=font_small)

        zone_names = [z["name"] for z in clean_zones]
        footer_right = (
            f"Räume ({len(zone_names)}): {', '.join(zone_names)}"
            if zone_names
            else "Modus: Komplette vermessene Etage"
        )
        fr_bbox = draw.textbbox((0, 0), footer_right, font=font_small)
        draw.text(
            (w - (fr_bbox[2] - fr_bbox[0]) - 22, canvas_bottom + 14),
            footer_right,
            fill=(56, 189, 248, 255),
            font=font_small,
        )

        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG", optimize=True)
        return buf.getvalue()
