"""
Model database:
- User          : akun pemilik target monitoring (untuk autentikasi & alert)
- MonitorTarget : web/server yang diinput user untuk dipantau
- CheckResult   : hasil setiap pengecekan (time-series) terhadap target
- AlertRule     : aturan kapan notifikasi dikirim untuk sebuah target
"""
from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    targets: Mapped[list["MonitorTarget"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class MonitorTarget(Base):
    __tablename__ = "monitor_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # Nullable sengaja: supaya target yang sudah ada sebelum fitur auth
    # dibuat (Tahap 1) tidak error saat migrasi. Setelah Fase 2 selesai,
    # endpoint pembuatan target baru akan selalu mengisi user_id ini.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(500))
    interval_seconds: Mapped[int] = mapped_column(Integer, default=10)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # --- Multi check-type (Fase 3) ---
    # "http"  : GET request biasa, cek status code (perilaku default/lama)
    # "ping"  : ICMP ping lewat command sistem (tidak perlu root)
    # "tcp"   : coba buka koneksi TCP ke host:tcp_port
    # "content": seperti http, tapi juga cek expected_content ada di body
    check_type: Mapped[str] = mapped_column(String(20), default="http")
    tcp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_content: Mapped[str | None] = mapped_column(String(500), nullable=True)

    owner: Mapped["User | None"] = relationship(back_populates="targets")
    checks: Mapped[list["CheckResult"]] = relationship(
        back_populates="target", cascade="all, delete-orphan"
    )
    alert_rule: Mapped["AlertRule | None"] = relationship(
        back_populates="target", uselist=False, cascade="all, delete-orphan"
    )


class CheckResult(Base):
    __tablename__ = "check_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("monitor_targets.id"))
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_up: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    response_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Anomaly detection (Fase 4) ---
    is_anomaly: Mapped[bool] = mapped_column(Boolean, default=False)
    anomaly_z_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    target: Mapped["MonitorTarget"] = relationship(back_populates="checks")


class AlertRule(Base):
    """
    Aturan notifikasi untuk satu target. Relasi one-to-one dengan
    MonitorTarget -- tiap target punya paling banyak 1 aturan alert.

    consecutive_failures & last_alert_sent_at dipakai scheduler untuk
    menghitung kapan alert harus ditembak, supaya tidak spam email
    setiap kali 1x gagal (lihat catatan "down > 2x berturut" di roadmap).
    """
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("monitor_targets.id"), unique=True)

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Jumlah kegagalan berturut-turut sebelum alert pertama dikirim.
    failure_threshold: Mapped[int] = mapped_column(Integer, default=2)
    # Jeda minimum antar alert untuk target yang sama (supaya tidak spam
    # kalau downnya berkepanjangan), dalam menit.
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=30)
    notify_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # State berjalan, di-update tiap kali scheduler selesai 1x check.
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_alert_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    target: Mapped["MonitorTarget"] = relationship(back_populates="alert_rule")
