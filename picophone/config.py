from __future__ import annotations

import configparser
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

try:
    import tomllib                       # 3.11+
except ImportError:
    import tomli as tomllib              # type: ignore[import-not-found]

log = logging.getLogger(__name__)


@dataclass
class AudioCfg:
    play_device: int | str = "default"
    record_device: int | str = "default"
    sample_rate_hz: int = 48000
    frame_ms: int = 20
    opus_bitrate_bps: int = 24000
    aec: bool = True
    ns: bool = True
    vad: bool = True
    dfn: bool = False                  # DeepFilterNet AI post-processor (heavy)
    input_threshold_db: float = -50.0
    in_gain_db: float = 0.0
    out_gain_db: float = 0.0
    rec_level: int = 1000
    play_volume: int = 1000
    min_delay_ms: int = 60
    max_delay_ms: int = 800


@dataclass
class NetCfg:
    identity: str = "anon"
    port: int = 11676
    bind_v6: bool = True
    autoanswer: bool = False
    password: str = ""
    encrypt: bool = True
    mdns: bool = True
    contacts: list[str] = field(default_factory=list)
    # NAT traversal: when enabled, ask the configured STUN server for our
    # outward-facing IP+port and advertise that to peers (instead of the
    # LAN-only RFC1918 address).  Off by default — only useful when calling
    # over the public internet.
    stun_enabled: bool = False
    stun_server: str = "stun.l.google.com:19302"


@dataclass
class UiCfg:
    light_background: bool = False
    beeper: bool = False
    window_pos: tuple[int, int] = (200, 200)
    generate_log: bool = False
    minimize_to_tray: bool = False
    autostart: bool = False


@dataclass
class Config:
    path: Path
    audio: AudioCfg = field(default_factory=AudioCfg)
    net: NetCfg = field(default_factory=NetCfg)
    ui: UiCfg = field(default_factory=UiCfg)

    @classmethod
    def load(cls, path: Path, legacy_ini: Path | None = None) -> "Config":
        if path.exists():
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            return cls._from_dict(path, data)
        if legacy_ini and legacy_ini.exists():
            log.info("Migrating legacy %s -> %s", legacy_ini, path)
            cfg = cls._from_ini(path, legacy_ini)
            cfg.save()
            return cfg
        cfg = cls(path=path)
        cfg.save()
        return cfg

    @classmethod
    def _from_dict(cls, path: Path, data: dict[str, Any]) -> "Config":
        a = AudioCfg(**(data.get("audio") or {}))
        n_data = data.get("net") or {}
        n_data["contacts"] = list(n_data.get("contacts", []))
        n = NetCfg(**n_data)
        u_data = data.get("ui") or {}
        if "window_pos" in u_data:
            u_data["window_pos"] = tuple(u_data["window_pos"])
        u = UiCfg(**u_data)
        return cls(path=path, audio=a, net=n, ui=u)

    @classmethod
    def _from_ini(cls, path: Path, ini_path: Path) -> "Config":
        ini = configparser.ConfigParser()
        ini.read(ini_path, encoding="utf-8")
        s = ini["Picophone"] if "Picophone" in ini else ini.defaults()
        contacts: list[str] = []
        for k, v in s.items():
            if k.lower().startswith("address") and v:
                contacts.append(v)
        wp = (200, 200)
        if "windowpos" in s:
            try:
                x, y = (int(p) for p in s["windowpos"].split(","))
                wp = (x, y)
            except ValueError:
                pass
        cfg = cls(
            path=path,
            audio=AudioCfg(
                play_device=int(s.get("playdevice", 0)),
                record_device=int(s.get("recorddevice", 0)),
                input_threshold_db=float(s.get("inputthreshold", -50)),
                in_gain_db=float(s.get("ingain", 0)),
                out_gain_db=float(s.get("outgain", 0)),
                rec_level=int(s.get("reclevel", 1000)),
                play_volume=int(s.get("volume", 1000)),
                min_delay_ms=int(s.get("delay", 60)),
                max_delay_ms=int(s.get("maxdelay", 800)),
                sample_rate_hz=48000 if int(s.get("hq", 1)) else 16000,
            ),
            net=NetCfg(
                identity=s.get("identity", "anon"),
                port=int(s.get("port", 11676)),
                autoanswer=bool(int(s.get("autoanswer", 0))),
                password=s.get("password", ""),
                contacts=contacts,
            ),
            ui=UiCfg(
                light_background=bool(int(s.get("lightbackground", 0))),
                beeper=bool(int(s.get("beeper", 0))),
                generate_log=bool(int(s.get("generatelog", 1))),
                window_pos=wp,
            ),
        )
        return cfg

    def save(self) -> None:
        import tomli_w
        d = {
            "audio": asdict(self.audio),
            "net": asdict(self.net),
            "ui": {**asdict(self.ui), "window_pos": list(self.ui.window_pos)},
        }
        self.path.write_text(tomli_w.dumps(d), encoding="utf-8")
