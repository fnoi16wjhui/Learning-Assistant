"""Interactive Learn double-auth probe.

This script stores temporary cookies locally so the user can provide a mobile
verification code in a second command. It never prints credentials or cookies.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adapters.learn_adapter import LearnAdapter, discover_auth_form_url, select_login_form
from src.env_loader import load_project_env


SESSION_PATH = ROOT / "storage" / "learn_double_auth_session.json"
TRUST_PATH = ROOT / "storage" / "learn_trust_device.json"
DOUBLE_AUTH_URL = "https://id.tsinghua.edu.cn/b/doubleAuth/login"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe Learn double authentication.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Start login and send a verification code.")
    start.add_argument("--type", choices=("mobile", "wechat"), default="mobile")

    verify = subparsers.add_parser("verify", help="Submit verification code and optionally trust device.")
    verify.add_argument("--code", required=True, help="Verification code received by the user.")
    verify.add_argument("--trust-device", action="store_true", help="Ask ID service to trust this device.")
    verify.add_argument("--device-name", default="CourseAgentCollector", help="Device label for trust records.")

    return parser


def save_session(session, *, auth_url: str, auth_html: str) -> None:
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "created_at": datetime.now().isoformat(),
        "auth_url": auth_url,
        "cookies": session.cookies.get_dict(),
        "auth_html": auth_html,
    }
    SESSION_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_session(adapter: LearnAdapter):
    if not SESSION_PATH.exists():
        raise SystemExit("No saved double-auth session. Run start first.")
    data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    session = adapter._build_session()
    session.cookies.update(data["cookies"])
    adapter._session = session
    return session, data


def start_double_auth(kind: str) -> int:
    load_project_env()
    adapter = LearnAdapter()
    session = adapter._build_session()
    adapter._session = session

    root = session.get(adapter.require("base_url"), timeout=20)
    root.raise_for_status()
    auth_url = discover_auth_form_url(root.url, root.text)
    if not auth_url:
        raise SystemExit("Could not discover Tsinghua ID auth form URL.")

    auth = session.get(auth_url, timeout=20)
    auth.raise_for_status()
    form = select_login_form(auth.text)
    payload = dict(form.get("inputs", {}))
    payload["i_user"] = adapter.require("username")
    payload["i_pass"] = adapter._prepare_password(auth.url, auth.text)
    login = session.post(urljoin(auth.url, str(form.get("action") or "")), data=payload, timeout=20)
    login.raise_for_status()

    approaches = session.post(DOUBLE_AUTH_URL, data={"action": "FIND_APPROACHES"}, timeout=20)
    approaches.raise_for_status()
    approach_data = approaches.json()
    if approach_data.get("result") != "success":
        raise SystemExit("Double-auth approach discovery failed.")

    send = session.post(DOUBLE_AUTH_URL, data={"action": "SEND_CODE", "type": kind}, timeout=20)
    send.raise_for_status()
    send_data = send.json()
    save_session(session, auth_url=auth.url, auth_html=auth.text)

    print(f"double_auth_start result={send_data.get('result')} msg_present={bool(send_data.get('msg'))}")
    print(f"session_saved={SESSION_PATH}")
    return 0 if send_data.get("result") == "success" else 2


def verify_double_auth(code: str, *, trust_device: bool, device_name: str) -> int:
    load_project_env()
    adapter = LearnAdapter()
    session, data = load_session(adapter)

    verify = session.post(DOUBLE_AUTH_URL, data={"action": "VERITY_CODE", "vericode": code}, timeout=20)
    verify.raise_for_status()
    verify_data = verify.json()
    print(f"double_auth_verify result={verify_data.get('result')} msg_present={bool(verify_data.get('msg'))}")
    if verify_data.get("result") != "success":
        return 2

    if trust_device:
        trust = session.post(
            "https://id.tsinghua.edu.cn/b/doubleAuth/personal/saveFinger",
            data={
                "fingerprint": "CourseAgentCollector",
                "deviceName": device_name,
                "radioVal": "是",
                "singleLogin": "yes",
            },
            timeout=20,
        )
        trust.raise_for_status()
        trust_data = trust.json()
        TRUST_PATH.parent.mkdir(parents=True, exist_ok=True)
        TRUST_PATH.write_text(json.dumps(trust_data, ensure_ascii=False), encoding="utf-8")
        print(f"trust_device result={trust_data.get('result')} msg_present={bool(trust_data.get('msg'))}")

    final = session.get(adapter.require("base_url"), timeout=20)
    final.raise_for_status()
    print(f"learn_final_url={final.url}")
    print(f"learn_final_bytes={len(final.text.encode('utf-8'))}")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "start":
        return start_double_auth(args.type)
    if args.command == "verify":
        return verify_double_auth(args.code, trust_device=args.trust_device, device_name=args.device_name)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
