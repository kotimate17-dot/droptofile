from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, send_from_directory, session
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "gif",
    "webp",
    "pdf",
    "txt",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "ppt",
    "pptx",
    "zip",
}

rooms: dict[str, dict[str, Any]] = {}

app = Flask(__name__)
app.config["SECRET_KEY"] = "fleshtogo-dev-secret-key"
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
app.jinja_loader.searchpath.append(str(BASE_DIR / "weboldal" / "templates"))


def ensure_device_id() -> str:
    device_id = session.get("device_id")
    if not device_id:
        device_id = secrets.token_hex(8)
        session["device_id"] = device_id
    return device_id


def make_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))


def allowed_file(filename: str) -> bool:
    if "." not in filename:
        return False
    extension = filename.rsplit(".", 1)[1].lower()
    return extension in ALLOWED_EXTENSIONS


def cleanup_expired_rooms() -> None:
    now = time.time()
    expired_codes = [
        code
        for code, room in rooms.items()
        if now - room["updated_at"] > 60 * 60 * 6
    ]
    for code in expired_codes:
        rooms.pop(code, None)


def find_room_for_device(device_id: str) -> tuple[str | None, dict[str, Any] | None]:
    cleanup_expired_rooms()
    for code, room in rooms.items():
        pending_request = room.get("pending_request") or {}
        if (
            room["host_id"] == device_id
            or room.get("guest_id") == device_id
            or pending_request.get("device_id") == device_id
        ):
            return code, room
    return None, None


def build_room_payload(code: str, room: dict[str, Any], viewer_id: str) -> dict[str, Any]:
    role = "guest"
    if room["host_id"] == viewer_id:
        role = "host"

    pending_request = None
    if room.get("pending_request"):
        pending_request = {
            "device_name": room["pending_request"]["device_name"],
            "requested_at": room["pending_request"]["requested_at"],
        }

    transfers = []
    for transfer in room["transfers"][-12:]:
        transfers.append(
            {
                "id": transfer["id"],
                "kind": transfer["kind"],
                "title": transfer["title"],
                "body": transfer.get("body", ""),
                "file_name": transfer.get("file_name"),
                "download_url": transfer.get("download_url"),
                "created_at": transfer["created_at"],
                "from_role": transfer["from_role"],
            }
        )

    return {
        "code": code,
        "status": room["status"],
        "role": role,
        "host_device_name": room["host_device_name"],
        "guest_device_name": room.get("guest_device_name"),
        "pending_request": pending_request,
        "permissions": room["permissions"],
        "transfers": transfers,
        "join_link": f"/?code={code}",
    }


@app.before_request
def setup_device() -> None:
    ensure_device_id()


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/weboldal")
def weboldal_index():
    return render_template("weboldal/index.html")


@app.get("/weboldal/static/<path:filename>")
def weboldal_static(filename: str):
    return send_from_directory(BASE_DIR / "weboldal" / "static", filename)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/uploads/<path:filename>")
def uploaded_file(filename: str):
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=True)


@app.get("/api/state")
def get_state():
    device_id = ensure_device_id()
    code, room = find_room_for_device(device_id)
    if not room:
        return jsonify({"paired": False, "room": None})
    return jsonify({"paired": room["status"] == "paired", "room": build_room_payload(code, room, device_id)})


@app.post("/api/pairing/generate")
def generate_code():
    device_id = ensure_device_id()
    existing_code, existing_room = find_room_for_device(device_id)
    if existing_room:
        return jsonify(
            {
                "ok": True,
                "room": build_room_payload(existing_code, existing_room, device_id),
                "message": "Mar van aktiv vagy fuggoben levo kapcsolatod.",
            }
        )

    payload = request.get_json(silent=True) or {}
    host_device_name = (payload.get("device_name") or "Ez az eszkoz").strip()[:40]

    code = make_code()
    while code in rooms:
        code = make_code()

    rooms[code] = {
        "host_id": device_id,
        "host_device_name": host_device_name,
        "guest_id": None,
        "guest_device_name": None,
        "status": "waiting",
        "pending_request": None,
        "permissions": {
            "Chat": True,
            "Kuldés": True,
            "Fogadás": True,
            "Jegyzet": True,
        },
        "transfers": [],
        "updated_at": time.time(),
    }

    return jsonify({"ok": True, "room": build_room_payload(code, rooms[code], device_id)})


@app.post("/api/pairing/join")
def join_with_code():
    device_id = ensure_device_id()
    payload = request.get_json(silent=True) or {}
    code = (payload.get("code") or "").strip().upper()
    device_name = (payload.get("device_name") or "Masik eszkoz").strip()[:40]

    if not code or code not in rooms:
        return jsonify({"ok": False, "error": "Ez a kod most nem elerheto."}), 404

    own_code, own_room = find_room_for_device(device_id)
    if own_room and own_code != code:
        return jsonify({"ok": False, "error": "Elobb bontsd a jelenlegi kapcsolatot."}), 409

    room = rooms[code]
    room["updated_at"] = time.time()

    if room["host_id"] == device_id:
        return jsonify({"ok": False, "error": "A sajat kododhoz nem tudsz csatlakozni."}), 400

    if room["status"] == "paired":
        return jsonify({"ok": False, "error": "Ehhez a kodhoz mar kapcsolodott egy masik eszkoz."}), 409

    room["pending_request"] = {
        "device_id": device_id,
        "device_name": device_name,
        "requested_at": int(time.time()),
    }
    room["status"] = "pending_approval"

    return jsonify({"ok": True, "room": build_room_payload(code, room, device_id)})


@app.post("/api/pairing/decision")
def approve_or_reject():
    device_id = ensure_device_id()
    payload = request.get_json(silent=True) or {}
    decision = payload.get("decision")

    code, room = find_room_for_device(device_id)
    if not room:
        return jsonify({"ok": False, "error": "Nincs aktiv kerelmed vagy kapcsolatod."}), 404

    if room["host_id"] != device_id:
        return jsonify({"ok": False, "error": "Csak a kodot generalo eszkoz hozhat dontest."}), 403

    pending_request = room.get("pending_request")
    if not pending_request:
        return jsonify({"ok": False, "error": "Nincs fuggoben levo csatlakozasi keres."}), 400

    if decision == "approve":
        room["guest_id"] = pending_request["device_id"]
        room["guest_device_name"] = pending_request["device_name"]
        room["pending_request"] = None
        room["status"] = "paired"
        room["updated_at"] = time.time()
        return jsonify({"ok": True, "room": build_room_payload(code, room, device_id)})

    room["pending_request"] = None
    room["guest_id"] = None
    room["guest_device_name"] = None
    room["status"] = "waiting"
    room["updated_at"] = time.time()
    return jsonify({"ok": True, "room": build_room_payload(code, room, device_id)})


@app.post("/api/pairing/unpair")
def unpair():
    device_id = ensure_device_id()
    code, room = find_room_for_device(device_id)
    if not room:
        return jsonify({"ok": True})

    rooms.pop(code, None)
    return jsonify({"ok": True})


@app.post("/api/permissions/toggle")
def toggle_permission():
    device_id = ensure_device_id()
    payload = request.get_json(silent=True) or {}
    permission = payload.get("permission")

    code, room = find_room_for_device(device_id)
    if not room:
        return jsonify({"ok": False, "error": "Nincs aktiv kapcsolat."}), 404

    if room["host_id"] != device_id:
        return jsonify({"ok": False, "error": "Az engedelyeket most csak a generalo eszkoz allithatja."}), 403

    if permission not in room["permissions"]:
        return jsonify({"ok": False, "error": "Ismeretlen engedely."}), 400

    room["permissions"][permission] = not room["permissions"][permission]
    room["updated_at"] = time.time()
    return jsonify({"ok": True, "room": build_room_payload(code, room, device_id)})


def append_transfer(room: dict[str, Any], transfer: dict[str, Any]) -> None:
    room["transfers"].append(transfer)
    room["updated_at"] = time.time()


@app.post("/api/send/message")
def send_message():
    device_id = ensure_device_id()
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "Az uzenet nem lehet ures."}), 400

    code, room = find_room_for_device(device_id)
    if not room or room["status"] != "paired":
        return jsonify({"ok": False, "error": "Kuldeshez elobb parosits ket eszkozt."}), 409

    append_transfer(
        room,
        {
            "id": secrets.token_hex(6),
            "kind": "message",
            "title": "Uzenet",
            "body": message[:1000],
            "created_at": int(time.time()),
            "from_role": "host" if room["host_id"] == device_id else "guest",
        },
    )
    return jsonify({"ok": True})


@app.post("/api/send/note")
def send_note():
    device_id = ensure_device_id()
    payload = request.get_json(silent=True) or {}
    note = (payload.get("note") or "").strip()
    if not note:
        return jsonify({"ok": False, "error": "A jegyzet nem lehet ures."}), 400

    code, room = find_room_for_device(device_id)
    if not room or room["status"] != "paired":
        return jsonify({"ok": False, "error": "Kuldeshez elobb parosits ket eszkozt."}), 409

    append_transfer(
        room,
        {
            "id": secrets.token_hex(6),
            "kind": "note",
            "title": "Jegyzet",
            "body": note[:1500],
            "created_at": int(time.time()),
            "from_role": "host" if room["host_id"] == device_id else "guest",
        },
    )
    return jsonify({"ok": True})


@app.post("/api/send/file")
def send_file():
    device_id = ensure_device_id()
    code, room = find_room_for_device(device_id)
    if not room or room["status"] != "paired":
        return jsonify({"ok": False, "error": "Kuldeshez elobb parosits ket eszkozt."}), 409

    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return jsonify({"ok": False, "error": "Valassz egy fajlt."}), 400

    if not allowed_file(uploaded.filename):
        return jsonify({"ok": False, "error": "Ez a fajltipus meg nincs engedelyezve."}), 400

    safe_name = secure_filename(uploaded.filename)
    unique_name = f"{int(time.time())}_{secrets.token_hex(4)}_{safe_name}"
    target = UPLOAD_DIR / unique_name
    uploaded.save(target)

    kind = "image"
    if target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        kind = "file"

    append_transfer(
        room,
        {
            "id": secrets.token_hex(6),
            "kind": kind,
            "title": "Kep kuldes" if kind == "image" else "Fajl kuldes",
            "file_name": uploaded.filename,
            "download_url": f"/uploads/{unique_name}",
            "created_at": int(time.time()),
            "from_role": "host" if room["host_id"] == device_id else "guest",
        },
    )
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)