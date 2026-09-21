from __future__ import annotations

"""Interactive terrain/placement surface for the Units on Map workspace.

Unlike the first implementation, this view does not trust ``war3mapMap.blp``
as the map background.  A surprising number of maps replace that file with
custom lobby art / a loading picture.  The placement surface therefore rebuilds
a lightweight minimap from the actual terrain grid in ``war3map.w3e`` and uses
the embedded minimap only as a fallback when the terrain file is unavailable.

The surface also owns the visual placement tools used by Object Studio:
selection, move (drag), rotation (drag), add and remove.
"""

from dataclasses import dataclass
from io import BytesIO
import colorsys
import math
import struct
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from typing import Callable, Iterable

from ..debuglog import configure_logging
from ..storm import StormArchive

LOGGER, _SESSION_LOG = configure_logging()


@dataclass(frozen=True)
class MapWorldBounds:
    min_x: float
    max_x: float
    min_y: float
    max_y: float

    @property
    def width(self) -> float:
        return max(1.0, float(self.max_x) - float(self.min_x))

    @property
    def height(self) -> float:
        return max(1.0, float(self.max_y) - float(self.min_y))


@dataclass(frozen=True)
class W3ETilePoint:
    ground_height: int
    water_raw: int
    flags: int
    ground_texture: int
    detail: int
    cliff_variation: int
    cliff_texture: int
    layer: int

    @property
    def water(self) -> bool:
        # v11 uses 0x40.  v12 shifted the flag nibble left by two bits; the
        # parser normalizes the flag field back to the familiar v11 values.
        return bool(self.flags & 0x40)

    @property
    def blight(self) -> bool:
        return bool(self.flags & 0x20)


@dataclass(frozen=True)
class W3ETerrain:
    version: int
    main_tileset: str
    ground_tiles: tuple[str, ...]
    cliff_tiles: tuple[str, ...]
    mx: int
    my: int
    offset_x: float
    offset_y: float
    points: tuple[W3ETilePoint, ...]

    @property
    def bounds(self) -> MapWorldBounds:
        return MapWorldBounds(
            float(self.offset_x),
            float(self.offset_x) + (self.mx - 1) * 128.0,
            float(self.offset_y),
            float(self.offset_y) + (self.my - 1) * 128.0,
        )

    def point(self, x: int, y: int) -> W3ETilePoint:
        return self.points[y * self.mx + x]


def _read_u32(data: bytes, pos: int) -> tuple[int, int]:
    if pos + 4 > len(data):
        raise ValueError("war3map.w3e truncated")
    return struct.unpack_from("<I", data, pos)[0], pos + 4


def parse_w3e(data: bytes) -> W3ETerrain:
    """Parse enough W3E data to reconstruct a faithful placement minimap.

    Supports classic/Reforged terrain version 11 and Reforged 2.x version 12.
    Version 12 expands the packed ground-texture/flags field from one byte to a
    ushort; the rest of the tilepoint data is kept in the same logical order.
    """
    if not isinstance(data, (bytes, bytearray)) or len(data) < 32:
        raise ValueError("war3map.w3e is too small")
    data = bytes(data)
    if data[:4] != b"W3E!":
        raise ValueError("war3map.w3e header is not W3E!")
    pos = 4
    version, pos = _read_u32(data, pos)
    if version not in (11, 12):
        LOGGER.warning("Unknown W3E version %s; attempting compatible parse", version)
    main_tileset = chr(data[pos])
    pos += 1
    _custom, pos = _read_u32(data, pos)
    ground_count, pos = _read_u32(data, pos)
    if ground_count > 64:
        raise ValueError(f"invalid ground tileset count: {ground_count}")
    ground_tiles = []
    for _ in range(ground_count):
        if pos + 4 > len(data):
            raise ValueError("war3map.w3e truncated in ground tilesets")
        ground_tiles.append(data[pos:pos + 4].decode("latin1"))
        pos += 4
    cliff_count, pos = _read_u32(data, pos)
    if cliff_count > 64:
        raise ValueError(f"invalid cliff tileset count: {cliff_count}")
    cliff_tiles = []
    for _ in range(cliff_count):
        if pos + 4 > len(data):
            raise ValueError("war3map.w3e truncated in cliff tilesets")
        cliff_tiles.append(data[pos:pos + 4].decode("latin1"))
        pos += 4
    mx, pos = _read_u32(data, pos)
    my, pos = _read_u32(data, pos)
    if mx < 2 or my < 2 or mx > 8193 or my > 8193:
        raise ValueError(f"war3map.w3e dimensions look invalid: {mx}x{my}")
    if pos + 8 > len(data):
        raise ValueError("war3map.w3e truncated before terrain offsets")
    offset_x, offset_y = struct.unpack_from("<ff", data, pos)
    pos += 8

    points: list[W3ETilePoint] = []
    point_size = 8 if version >= 12 else 7
    needed = int(mx) * int(my) * point_size
    if pos + needed > len(data):
        raise ValueError(
            f"war3map.w3e terrain data truncated: need {needed} bytes, have {len(data)-pos}"
        )

    for _ in range(int(mx) * int(my)):
        ground_h = struct.unpack_from("<H", data, pos)[0]
        water_raw = struct.unpack_from("<H", data, pos + 2)[0]
        p = pos + 4
        if version >= 12:
            packed = struct.unpack_from("<H", data, p)[0]
            p += 2
            ground_texture = packed & 0x3F
            # v12 moves the familiar 0x10/20/40/80 flags two bits upward.
            flags = (packed & 0x03C0) >> 2
        else:
            packed = data[p]
            p += 1
            ground_texture = packed & 0x0F
            flags = packed & 0xF0
        detail_byte = data[p]
        p += 1
        cliff_byte = data[p]
        p += 1
        points.append(W3ETilePoint(
            ground_height=ground_h,
            water_raw=water_raw,
            flags=flags,
            ground_texture=ground_texture,
            detail=(detail_byte >> 3) & 0x1F,
            cliff_variation=detail_byte & 0x07,
            cliff_texture=(cliff_byte >> 4) & 0x0F,
            layer=cliff_byte & 0x0F,
        ))
        pos += point_size

    return W3ETerrain(
        version=int(version),
        main_tileset=main_tileset,
        ground_tiles=tuple(ground_tiles),
        cliff_tiles=tuple(cliff_tiles),
        mx=int(mx), my=int(my), offset_x=float(offset_x), offset_y=float(offset_y),
        points=tuple(points),
    )


def parse_w3e_bounds(data: bytes) -> MapWorldBounds:
    return parse_w3e(data).bounds


def infer_bounds_from_units(units: Iterable[object]) -> MapWorldBounds:
    points = [(float(getattr(u, "x", 0.0)), float(getattr(u, "y", 0.0))) for u in units]
    if not points:
        return MapWorldBounds(-4096.0, 4096.0, -4096.0, 4096.0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    span_x = max(1024.0, max(xs) - min(xs))
    span_y = max(1024.0, max(ys) - min(ys))
    pad_x = max(768.0, span_x * 0.15)
    pad_y = max(768.0, span_y * 0.15)
    return MapWorldBounds(min(xs) - pad_x, max(xs) + pad_x, min(ys) - pad_y, max(ys) + pad_y)


def _tileset_base_rgb(main: str) -> tuple[int, int, int]:
    # Warcraft terrain palettes.  These are intentionally restrained average
    # colours, not substitutes for the actual tile textures: the minimap needs
    # readable terrain structure more than decorative detail.
    return {
        "L": (92, 112, 61),   # Lordaeron Summer
        "F": (103, 98, 68),  # Lordaeron Fall
        "W": (118, 126, 113),# Lordaeron Winter
        "A": (64, 104, 58),  # Ashenvale
        "B": (118, 79, 47),  # Barrens
        "C": (76, 99, 67),   # Felwood
        "N": (75, 87, 59),   # Northrend
        "Y": (112, 89, 57),  # Cityscape
        "X": (84, 80, 73),   # Dalaran
        "D": (86, 70, 62),   # Dungeon
        "G": (75, 72, 62),   # Underground
        "Q": (93, 74, 57),   # Village Fall
        "V": (85, 111, 64),  # Village
        "I": (161, 152, 121),# Icecrown
        "J": (82, 71, 61),   # Dalaran Ruins
        "O": (99, 74, 47),   # Outland
        "K": (80, 77, 69),   # Black Citadel
        "Z": (83, 76, 67),   # Sunken Ruins
    }.get(main.upper(), (96, 93, 75))


def _tile_rgb(tile_id: str, index: int, main_tileset: str) -> tuple[int, int, int]:
    code = str(tile_id or "").casefold()
    base = _tileset_base_rgb(main_tileset)
    # Common WC3 tile IDs carry human-ish suffixes (drt, grs, rok, snw, ice…).
    if any(x in code for x in ("grs", "grd", "leaf", "vine", "moss")):
        base = (70, 118, 58)
    elif any(x in code for x in ("drt", "dirt", "mud", "rough")):
        base = (108, 88, 58)
    elif any(x in code for x in ("rok", "rock", "stone", "brik", "brick")):
        base = (99, 96, 88)
    elif any(x in code for x in ("snw", "snow", "ice")):
        base = (181, 189, 183)
    elif any(x in code for x in ("sand", "sng", "des", "ash")):
        base = (150, 123, 75)
    elif any(x in code for x in ("lava", "volc")):
        base = (116, 62, 39)

    # Stable small variation so adjacent texture types remain distinguishable.
    r, g, b = [c / 255.0 for c in base]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    h = (h + ((index * 17) % 23 - 11) / 360.0) % 1.0
    v = min(0.88, max(0.20, v * (0.90 + (index % 5) * 0.035)))
    rr, gg, bb = colorsys.hsv_to_rgb(h, s, v)
    return int(rr * 255), int(gg * 255), int(bb * 255)


def _height_display(p: W3ETilePoint) -> float:
    # Terrain spec: layer 2 is layer-zero, each layer step is 0x0200 raw.
    return (float(p.ground_height) - 0x2000 + (int(p.layer) - 2) * 0x0200) / 4.0


def render_w3e_minimap(terrain: W3ETerrain):
    """Rebuild a top-down terrain image directly from W3E.

    This is deliberately a minimap renderer, not a full World Editor renderer:
    it preserves ground-type regions, water, blight and height shading so unit
    placement lines up with the actual terrain even when the map replaced its
    embedded preview/minimap with custom artwork.
    """
    from PIL import Image

    w = max(1, terrain.mx - 1)
    h = max(1, terrain.my - 1)
    image = Image.new("RGBA", (w, h), (53, 53, 47, 255))
    px = image.load()

    # A cheap but useful height range for hill/cliff shading.
    heights = [_height_display(p) for p in terrain.points]
    hmin = min(heights) if heights else 0.0
    hmax = max(heights) if heights else 1.0
    hspan = max(1.0, hmax - hmin)

    tile_colors = [
        _tile_rgb(tile_id, i, terrain.main_tileset)
        for i, tile_id in enumerate(terrain.ground_tiles)
    ] or [_tileset_base_rgb(terrain.main_tileset)]

    for ty in range(h):
        for tx in range(w):
            p0 = terrain.point(tx, ty)
            p1 = terrain.point(tx + 1, ty)
            p2 = terrain.point(tx, ty + 1)
            p3 = terrain.point(tx + 1, ty + 1)
            idx = int(p0.ground_texture)
            base = tile_colors[idx] if 0 <= idx < len(tile_colors) else tile_colors[idx % len(tile_colors)]
            avg_h = (_height_display(p0) + _height_display(p1) + _height_display(p2) + _height_display(p3)) / 4.0
            # Height adds just enough relief to understand ramps/plateaus.
            light = 0.82 + 0.28 * ((avg_h - hmin) / hspan)
            # Estimate local slope; steeper tiles shade darker like minimap cliffs.
            dx = (_height_display(p1) + _height_display(p3)) - (_height_display(p0) + _height_display(p2))
            dy = (_height_display(p2) + _height_display(p3)) - (_height_display(p0) + _height_display(p1))
            slope = min(1.0, math.sqrt(dx * dx + dy * dy) / 700.0)
            light *= (1.0 - 0.20 * slope)
            r = int(max(0, min(255, base[0] * light)))
            g = int(max(0, min(255, base[1] * light)))
            b = int(max(0, min(255, base[2] * light)))

            if p0.blight or p1.blight or p2.blight or p3.blight:
                r = int(r * 0.70 + 62 * 0.30)
                g = int(g * 0.62 + 42 * 0.38)
                b = int(b * 0.72 + 68 * 0.28)

            # Water flag is attached to tilepoints. If any corner says water,
            # tint the cell; this mirrors the broad shapes visible in-game.
            if p0.water or p1.water or p2.water or p3.water:
                r = int(r * 0.42 + 33 * 0.58)
                g = int(g * 0.50 + 89 * 0.50)
                b = int(b * 0.42 + 128 * 0.58)

            # PIL top-left is north; W3E rows start in the south, so flip Y.
            px[tx, h - 1 - ty] = (r, g, b, 255)

    # Scale tiny maps up once so later UI resizing remains crisp enough.
    if max(w, h) < 512:
        scale = max(1, min(4, 512 // max(w, h)))
        if scale > 1:
            image = image.resize((w * scale, h * scale), resample=Image.Resampling.NEAREST)
    return image


def _decode_embedded_map_image(arc: StormArchive):
    """Fallback only: embedded minimap/preview files can be arbitrary artwork."""
    candidates = (
        "war3mapMap.blp",
        "war3mapMap.tga",
        "war3mapPreview.tga",
        "war3mapPreview.blp",
    )
    for name in candidates:
        if not arc.has(name):
            continue
        try:
            raw = arc.read(name)
            from PIL import Image
            if name.casefold().endswith(".blp"):
                import PIL.BlpImagePlugin  # noqa: F401
            with Image.open(BytesIO(raw)) as opened:
                return opened.convert("RGBA"), name
        except Exception:
            LOGGER.warning("Could not decode map preview %s", name, exc_info=True)
    return None, ""


def load_map_placement_art(map_path: str | Path, units: Iterable[object] = ()):
    """Return ``(image, bounds, source)`` for the placement workspace.

    Primary source: reconstructed ``war3map.w3e`` terrain.
    Fallback: embedded minimap/preview image.
    Last resort: coordinate grid.
    """
    image = None
    source = ""
    bounds = None
    map_path = Path(map_path)
    with StormArchive(map_path, read_only=True) as arc:
        if arc.has("war3map.w3e"):
            try:
                terrain = parse_w3e(arc.read("war3map.w3e"))
                bounds = terrain.bounds
                image = render_w3e_minimap(terrain)
                source = "war3map.w3e · terrain reconstructed"
            except Exception:
                LOGGER.warning("Could not reconstruct W3E minimap", exc_info=True)
        if image is None:
            embedded, embedded_source = _decode_embedded_map_image(arc)
            if embedded is not None:
                image = embedded
                source = embedded_source + " · fallback"
        if bounds is None and arc.has("war3map.w3e"):
            try:
                bounds = parse_w3e_bounds(arc.read("war3map.w3e"))
            except Exception:
                pass

    if bounds is None:
        bounds = infer_bounds_from_units(units)
    return image, bounds, source


# Warcraft-like player colors. Neutral slots intentionally use subdued tones.
_PLAYER_COLORS = (
    "#ff3030", "#3077ff", "#28d56b", "#9b54e8", "#ffd430", "#ff9138",
    "#35e7e4", "#ef7fb7", "#a8a8a8", "#80a84b", "#7cb6ff", "#704529",
    "#9b0000", "#0000c3", "#00e7e7", "#550081", "#fffc01", "#fe8a0e",
    "#20c000", "#e55bb0", "#959697", "#7ebff1", "#106246", "#4e2a04",
    "#7a1212", "#aab0b7", "#d0b56c", "#d4d4d4",
)


class MapPlacementCanvas(ttk.Frame):
    """Terrain view + editable Units.doo placement overlay."""

    def __init__(
        self,
        parent,
        *,
        msg: Callable[[str, str], str],
        on_select: Callable[[int], None] | None = None,
        on_move: Callable[[int, float, float], None] | None = None,
        on_rotate: Callable[[int, float], None] | None = None,
        on_add: Callable[[], None] | None = None,
        on_remove: Callable[[int], None] | None = None,
    ):
        super().__init__(parent)
        self._msg = msg
        self._on_select = on_select
        self._on_move = on_move
        self._on_rotate = on_rotate
        self._on_add = on_add
        self._on_remove = on_remove
        self._image = None
        self._photo = None
        self._bounds = MapWorldBounds(-4096, 4096, -4096, 4096)
        self._source = ""
        self._units: list[object] = []
        self._studio_added: set[int] = set()
        self._selected: int | None = None
        self._image_box = (0.0, 0.0, 1.0, 1.0)
        self._placement_callback: Callable[[float, float], None] | None = None
        self._placement_label = ""
        self._resize_after = None
        self._tool = "select"
        self._drag_creation: int | None = None
        self._drag_preview_pos: tuple[float, float] | None = None
        self._drag_preview_angle: float | None = None
        self._drag_started = False

        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill="x")
        top_line = ttk.Frame(bar)
        top_line.pack(fill="x")
        self.title_var = tk.StringVar(value=self._msg("MAPA / POSICIONAMENTO", "MAP / PLACEMENT"))
        ttk.Label(top_line, textvariable=self.title_var, font=("Segoe UI", 11, "bold")).pack(side="left")
        self.hint_var = tk.StringVar(value="")
        ttk.Label(top_line, textvariable=self.hint_var, style="Source.TLabel").pack(side="left", padx=(12, 0), fill="x", expand=True)
        self.cancel_btn = ttk.Button(top_line, text=self._msg("CANCELAR POSIÇÃO", "CANCEL PLACEMENT"), command=self.cancel_placement)
        self.cancel_btn.pack(side="right")
        self.cancel_btn.state(["disabled"])

        # Dedicated second row: keeps the tools readable even when the center
        # pane is narrow because the 3D preview is open on the right.
        tools = ttk.Frame(bar)
        tools.pack(fill="x", pady=(6, 0))
        self.select_btn = ttk.Button(tools, text=self._msg("SELECIONAR", "SELECT"), command=lambda: self.set_tool("select"), width=10)
        self.move_btn = ttk.Button(tools, text=self._msg("MOVER", "MOVE"), command=lambda: self.set_tool("move"), width=8)
        self.rotate_btn = ttk.Button(tools, text=self._msg("ROTACIONAR", "ROTATE"), command=lambda: self.set_tool("rotate"), width=10)
        self.add_btn = ttk.Button(tools, text=self._msg("+ UNIDADE", "+ UNIT"), command=self._invoke_add, width=9)
        self.remove_btn = ttk.Button(tools, text=self._msg("REMOVER", "REMOVE"), command=self._invoke_remove, width=9)
        for widget in (self.select_btn, self.move_btn, self.rotate_btn, self.add_btn, self.remove_btn):
            widget.pack(side="left", padx=(0, 4))

        host = tk.Frame(self, bg="#121418", highlightthickness=1, highlightbackground="#343942")
        host.pack(fill="both", expand=True, padx=8, pady=(0, 5))
        self.canvas = tk.Canvas(host, bg="#15181d", highlightthickness=0, cursor="arrow")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Button-3>", lambda _e: self._cancel_active_action())
        self.canvas.bind("<Delete>", lambda _e: self._invoke_remove())
        self.canvas.bind("<Escape>", lambda _e: self._cancel_active_action())
        self.canvas.configure(takefocus=True)

        self.status_var = tk.StringVar(value=self._msg("Abra um mapa para visualizar as unidades.", "Open a map to view units."))
        ttk.Label(self, textvariable=self.status_var, style="Source.TLabel", anchor="w").pack(fill="x", padx=8, pady=(0, 7))
        self.set_tool("select")

    def _tool_hint(self) -> str:
        if self._tool == "move":
            return self._msg("Arraste uma unidade para mover.", "Drag a unit to move it.")
        if self._tool == "rotate":
            return self._msg("Arraste a partir de uma unidade para apontar a direção.", "Drag from a unit to set its facing.")
        return self._msg("Clique em uma unidade para selecioná-la.", "Click a unit to select it.")

    def apply_language(self) -> None:
        self.title_var.set(self._msg("MAPA / POSICIONAMENTO", "MAP / PLACEMENT"))
        self.select_btn.configure(text=self._msg("SELECIONAR", "SELECT"))
        self.move_btn.configure(text=self._msg("MOVER", "MOVE"))
        self.rotate_btn.configure(text=self._msg("ROTACIONAR", "ROTATE"))
        self.add_btn.configure(text=self._msg("+ UNIDADE", "+ UNIT"))
        self.remove_btn.configure(text=self._msg("REMOVER", "REMOVE"))
        self.cancel_btn.configure(text=self._msg("CANCELAR POSIÇÃO", "CANCEL PLACEMENT"))
        if self._placement_callback is not None:
            self.hint_var.set(self._msg(
                f"POSICIONANDO {self._placement_label}: clique no mapa · botão direito cancela",
                f"PLACING {self._placement_label}: click the map · right-click cancels",
            ))
        else:
            self.hint_var.set(self._tool_hint())
        self.redraw()

    def set_tool(self, tool: str) -> None:
        if tool not in {"select", "move", "rotate"}:
            tool = "select"
        self._tool = tool
        self._drag_creation = None
        self._drag_preview_pos = None
        self._drag_preview_angle = None
        self._drag_started = False
        if self._placement_callback is None:
            self.hint_var.set(self._tool_hint())
        cursor = "fleur" if tool == "move" else ("crosshair" if tool == "rotate" else "arrow")
        self.canvas.configure(cursor=cursor)
        # ttk does not expose a portable toggle visual, so mark the active tool
        # through the pressed state where the theme supports it.
        for name, button in (("select", self.select_btn), ("move", self.move_btn), ("rotate", self.rotate_btn)):
            try:
                button.state(["pressed"] if name == tool else ["!pressed"])
            except Exception:
                pass

    def _invoke_add(self) -> None:
        if self._on_add is not None:
            self._on_add()

    def _invoke_remove(self) -> None:
        if self._selected is not None and self._on_remove is not None:
            self._on_remove(int(self._selected))

    def set_map(self, image, bounds: MapWorldBounds, source: str = "") -> None:
        self._image = image.copy() if image is not None else None
        self._bounds = bounds
        self._source = source or ""
        self.redraw()

    def set_units(self, units: Iterable[object], *, studio_added: Iterable[int] = (), selected: int | None = None) -> None:
        self._units = list(units)
        self._studio_added = {int(x) for x in studio_added}
        # Selection identity is the placement's file-order index, not
        # creation_number. Third-party maps may contain duplicate creation
        # numbers, while an index is always unique inside the loaded DOO.
        self._selected = int(selected) if selected is not None else None
        self.redraw()

    def set_selected(self, placement_index: int | None) -> None:
        self._selected = int(placement_index) if placement_index is not None else None
        self.redraw()

    def begin_placement(self, label: str, callback: Callable[[float, float], None]) -> None:
        self._placement_label = str(label)
        self._placement_callback = callback
        self._drag_creation = None
        self.canvas.configure(cursor="crosshair")
        self.cancel_btn.state(["!disabled"])
        self.hint_var.set(self._msg(
            f"POSICIONANDO {label}: clique no mapa · botão direito cancela",
            f"PLACING {label}: click the map · right-click cancels",
        ))
        self.status_var.set(self._msg(
            "Escolha o ponto no terreno. A unidade só será gravada quando você clicar SALVAR.",
            "Choose the terrain point. The unit is written only when you click SAVE.",
        ))

    def cancel_placement(self) -> None:
        if self._placement_callback is None:
            return
        self._placement_callback = None
        self._placement_label = ""
        self.cancel_btn.state(["disabled"])
        self.hint_var.set(self._tool_hint())
        self.set_tool(self._tool)
        self.status_var.set(self._base_status())

    def _cancel_active_action(self) -> None:
        if self._placement_callback is not None:
            self.cancel_placement()
        else:
            self._drag_creation = None
            self._drag_preview_pos = None
            self._drag_preview_angle = None
            self._drag_started = False
            self.redraw()

    def _base_status(self) -> str:
        source = self._source or self._msg("grade de coordenadas", "coordinate grid")
        return self._msg(
            f"{source} · {len(self._units)} placement(s) · mover/rotacionar altera DOO + runtime no próximo SALVAR",
            f"{source} · {len(self._units)} placement(s) · move/rotate updates DOO + runtime on next SAVE",
        )

    def _on_resize(self, _event=None) -> None:
        if self._resize_after is not None:
            try:
                self.after_cancel(self._resize_after)
            except Exception:
                pass
        self._resize_after = self.after(80, self.redraw)

    def _layout_box(self) -> tuple[float, float, float, float]:
        cw = max(40, self.canvas.winfo_width())
        ch = max(40, self.canvas.winfo_height())
        pad = 16.0
        avail_w = max(1.0, cw - pad * 2)
        avail_h = max(1.0, ch - pad * 2)
        # World extents are authoritative.  Terrain images reconstructed from
        # W3E share the same aspect; a fallback image may not.
        aspect = self._bounds.width / self._bounds.height
        if self._image is not None and "fallback" in self._source and self._image.height:
            aspect = self._image.width / self._image.height
        if avail_w / avail_h > aspect:
            h = avail_h
            w = h * aspect
        else:
            w = avail_w
            h = w / max(1e-9, aspect)
        left = (cw - w) / 2.0
        top = (ch - h) / 2.0
        return left, top, left + w, top + h

    def world_to_canvas(self, x: float, y: float) -> tuple[float, float]:
        l, t, r, b = self._image_box
        nx = (float(x) - self._bounds.min_x) / self._bounds.width
        ny = (float(y) - self._bounds.min_y) / self._bounds.height
        return l + nx * (r - l), b - ny * (b - t)

    def canvas_to_world(self, px: float, py: float, *, clamp: bool = False) -> tuple[float, float] | None:
        l, t, r, b = self._image_box
        if r <= l or b <= t:
            return None
        if not clamp and not (l <= px <= r and t <= py <= b):
            return None
        px = min(r, max(l, px))
        py = min(b, max(t, py))
        nx = (px - l) / (r - l)
        ny = 1.0 - (py - t) / (b - t)
        return self._bounds.min_x + nx * self._bounds.width, self._bounds.min_y + ny * self._bounds.height

    def _unit_display_state(self, unit, placement_index: int) -> tuple[float, float, float]:
        x = float(getattr(unit, "x", 0.0))
        y = float(getattr(unit, "y", 0.0))
        angle = float(getattr(unit, "angle", 0.0))
        if int(placement_index) == self._drag_creation:
            if self._drag_preview_pos is not None:
                x, y = self._drag_preview_pos
            if self._drag_preview_angle is not None:
                angle = self._drag_preview_angle
        return x, y, angle

    def redraw(self) -> None:
        self._resize_after = None
        c = self.canvas
        c.delete("all")
        self._image_box = self._layout_box()
        l, t, r, b = self._image_box

        if self._image is not None:
            try:
                from PIL import Image, ImageTk
                w = max(1, int(round(r - l)))
                h = max(1, int(round(b - t)))
                # Terrain reconstruction looks better with bilinear scaling;
                # nearest-neighbour source pixels are already tile cells.
                resized = self._image.resize((w, h), resample=Image.Resampling.BILINEAR)
                self._photo = ImageTk.PhotoImage(resized)
                c.create_image(l, t, image=self._photo, anchor="nw", tags=("mapbg",))
            except Exception:
                LOGGER.debug("Could not render placement map image", exc_info=True)
                self._photo = None
                c.create_rectangle(l, t, r, b, fill="#2b2924", outline="#59606a")
        else:
            self._photo = None
            c.create_rectangle(l, t, r, b, fill="#292720", outline="#59606a")
            for i in range(1, 8):
                x = l + (r - l) * i / 8.0
                y = t + (b - t) * i / 8.0
                c.create_line(x, t, x, b, fill="#3c3a34")
                c.create_line(l, y, r, y, fill="#3c3a34")
            c.create_text((l+r)/2, (t+b)/2, text=self._msg(
                "TERRENO NÃO DISPONÍVEL\ncoordenadas e placements continuam disponíveis",
                "TERRAIN NOT AVAILABLE\ncoordinates and placements are still available",
            ), fill="#a6abb3", justify="center", font=("Segoe UI", 10, "bold"))

        c.create_rectangle(l, t, r, b, outline="#7c8796", width=1)

        for placement_index, unit in enumerate(self._units):
            try:
                creation = int(getattr(unit, "creation_number"))
                uxw, uyw, angle = self._unit_display_state(unit, placement_index)
                x, y = self.world_to_canvas(uxw, uyw)
                player = int(getattr(unit, "player", 0))
            except Exception:
                continue
            color = _PLAYER_COLORS[player] if 0 <= player < len(_PLAYER_COLORS) else "#e7e7e7"
            size = 10.0 if placement_index == self._selected else 7.5
            ux = math.cos(angle)
            uy = -math.sin(angle)
            pxv = -uy
            pyv = ux
            nose = (x + ux * size * 1.45, y + uy * size * 1.45)
            rear1 = (x - ux * size + pxv * size * .72, y - uy * size + pyv * size * .72)
            rear2 = (x - ux * size - pxv * size * .72, y - uy * size - pyv * size * .72)
            outline = "#ffffff" if placement_index == self._selected else ("#8cf0a9" if creation in self._studio_added else "#17191d")
            width = 3 if placement_index == self._selected else 2
            c.create_polygon(
                nose[0], nose[1], rear1[0], rear1[1], rear2[0], rear2[1],
                fill=color, outline=outline, width=width,
                tags=("placement", f"placement:{placement_index}"),
            )
            if placement_index == self._selected:
                raw = str(getattr(unit, "unit_id", ""))
                c.create_text(x, y-20, text=f"#{creation} [{raw}]", fill="#ffffff", font=("Segoe UI", 9, "bold"), tags=("selection-label",))
                if self._tool == "rotate" or self._drag_preview_angle is not None:
                    c.create_line(x, y, x + ux * 34, y + uy * 34, fill="#ffffff", width=2, arrow="last")

        if self._placement_callback is None and not self._drag_started:
            self.status_var.set(self._base_status())

    def _nearest_marker(self, px: float, py: float, radius: float = 15.0) -> int | None:
        best = None
        best_d2 = radius * radius
        for placement_index, unit in enumerate(self._units):
            try:
                uxw, uyw, _a = self._unit_display_state(unit, placement_index)
                x, y = self.world_to_canvas(uxw, uyw)
                d2 = (x-px)*(x-px) + (y-py)*(y-py)
                if d2 <= best_d2:
                    best_d2 = d2
                    best = placement_index
            except Exception:
                continue
        return best

    def _unit_by_index(self, placement_index: int):
        try:
            idx = int(placement_index)
            return self._units[idx] if 0 <= idx < len(self._units) else None
        except Exception:
            return None

    def _on_press(self, event) -> None:
        self.canvas.focus_set()
        if self._placement_callback is not None:
            world = self.canvas_to_world(event.x, event.y)
            if world is None:
                return
            callback = self._placement_callback
            self.cancel_placement()
            callback(float(world[0]), float(world[1]))
            return

        placement_index = self._nearest_marker(event.x, event.y)
        if placement_index is not None:
            self._selected = placement_index
            if self._on_select is not None:
                self._on_select(placement_index)
            if self._tool in {"move", "rotate"}:
                self._drag_creation = placement_index
                self._drag_started = True
                unit = self._unit_by_index(placement_index)
                if unit is not None:
                    self._drag_preview_pos = (float(getattr(unit, "x", 0.0)), float(getattr(unit, "y", 0.0)))
                    self._drag_preview_angle = float(getattr(unit, "angle", 0.0))
            self.redraw()
        else:
            self._drag_creation = None
            self._drag_started = False

    def _on_drag(self, event) -> None:
        if self._placement_callback is not None or self._drag_creation is None:
            return
        unit = self._unit_by_index(self._drag_creation)
        if unit is None:
            return
        if self._tool == "move":
            world = self.canvas_to_world(event.x, event.y, clamp=True)
            if world is None:
                return
            self._drag_preview_pos = (float(world[0]), float(world[1]))
            creation = int(getattr(unit, "creation_number", -1))
            self.status_var.set(self._msg(
                f"MOVER #{creation} → X {world[0]:.1f} · Y {world[1]:.1f}",
                f"MOVE #{creation} → X {world[0]:.1f} · Y {world[1]:.1f}",
            ))
        elif self._tool == "rotate":
            uxw, uyw, _angle = self._unit_display_state(unit, self._drag_creation)
            cx, cy = self.world_to_canvas(uxw, uyw)
            dx = float(event.x) - cx
            dy_world = cy - float(event.y)
            if abs(dx) + abs(dy_world) < 2.0:
                return
            deg = math.degrees(math.atan2(dy_world, dx)) % 360.0
            self._drag_preview_angle = math.radians(deg)
            creation = int(getattr(unit, "creation_number", -1))
            self.status_var.set(self._msg(
                f"ROTACIONAR #{creation} → {deg:.1f}°",
                f"ROTATE #{creation} → {deg:.1f}°",
            ))
        self.redraw()

    def _on_release(self, _event) -> None:
        if self._drag_creation is None or not self._drag_started:
            return
        placement_index = int(self._drag_creation)
        try:
            if self._tool == "move" and self._drag_preview_pos is not None and self._on_move is not None:
                x, y = self._drag_preview_pos
                self._on_move(placement_index, float(x), float(y))
            elif self._tool == "rotate" and self._drag_preview_angle is not None and self._on_rotate is not None:
                deg = math.degrees(float(self._drag_preview_angle)) % 360.0
                self._on_rotate(placement_index, deg)
        finally:
            self._drag_creation = None
            self._drag_preview_pos = None
            self._drag_preview_angle = None
            self._drag_started = False
            self.redraw()

    def _on_motion(self, event) -> None:
        if self._drag_started:
            return
        world = self.canvas_to_world(event.x, event.y)
        if world is None:
            return
        x, y = world
        if self._placement_callback is not None:
            self.status_var.set(self._msg(
                f"Clique para colocar {self._placement_label} em X {x:.1f} · Y {y:.1f}",
                f"Click to place {self._placement_label} at X {x:.1f} · Y {y:.1f}",
            ))
            return
        placement_index = self._nearest_marker(event.x, event.y)
        if placement_index is not None:
            cursor = "fleur" if self._tool == "move" else ("crosshair" if self._tool == "rotate" else "hand2")
            self.canvas.configure(cursor=cursor)
            unit = self._unit_by_index(placement_index)
            raw = str(getattr(unit, "unit_id", "")) if unit is not None else ""
            creation = int(getattr(unit, "creation_number", -1)) if unit is not None else -1
            self.status_var.set(self._msg(
                f"Instância #{creation} [{raw}] · X {x:.1f} · Y {y:.1f}",
                f"Instance #{creation} [{raw}] · X {x:.1f} · Y {y:.1f}",
            ))
        else:
            cursor = "fleur" if self._tool == "move" else ("crosshair" if self._tool == "rotate" else "arrow")
            self.canvas.configure(cursor=cursor)
            self.status_var.set(f"X {x:.1f} · Y {y:.1f}")
