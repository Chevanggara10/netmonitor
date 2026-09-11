"""
Mengelola koneksi WebSocket aktif agar hasil monitoring bisa disiarkan
secara realtime ke dashboard yang sedang terbuka.

Tiap koneksi disimpan bersama user_id pemiliknya (diverifikasi dari JWT
saat handshake di main.py) -- broadcast hasil check hanya dikirim ke
koneksi milik user yang sama dengan pemilik target tsb, supaya data
monitoring satu akun tidak bocor ke akun lain yang sedang online.
"""
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        # Setiap koneksi dipasangkan dengan user_id pemiliknya.
        self.active_connections: list[tuple[WebSocket, int]] = []

    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        self.active_connections.append((websocket, user_id))

    def disconnect(self, websocket: WebSocket):
        self.active_connections = [
            (ws, uid) for ws, uid in self.active_connections if ws is not websocket
        ]

    async def broadcast(self, message: dict, user_id: int):
        """Kirim message hanya ke koneksi milik user_id ini."""
        dead_connections = []
        for connection, conn_user_id in self.active_connections:
            if conn_user_id != user_id:
                continue
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)
        for dead in dead_connections:
            self.disconnect(dead)


manager = ConnectionManager()
