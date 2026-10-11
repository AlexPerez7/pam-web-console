"""
infra_data.py - Telemetry Reporter (ITR)

Lee los archivos HealthCheck_<host>_<timestamp>.json que deja un script
.ps1 corrido en cada servidor on-prem/IaaS del cliente (estado de servicios
de CyberArk, uso de CPU/RAM/disco, certificado RDP) - datos que la API de
Privilege Cloud no expone porque viven en el sistema operativo del
servidor, no en el tenant SaaS. Se completan a mano copiando esos archivos
a una carpeta local (ver clientes/README.md) - este módulo solo parsea JSON
local, no hace ningún request de red.
"""

import glob
import json
import os
from datetime import date, datetime


def _parse_fecha(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return datetime.min


def _cert_status(expires_on_str, report_date):
    """Calcula días restantes y "próximo a vencer" contra report_date (la
    fecha de generación del reporte), no contra la fecha en que corrió el
    script en el servidor - pueden ser días distintos."""
    if not expires_on_str:
        return None
    try:
        expires_on = datetime.strptime(expires_on_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    days_remaining = (expires_on - report_date).days
    return {
        "expires_on_txt": expires_on.strftime("%d-%m-%Y"),
        "days_remaining": days_remaining,
        "expiring_soon": days_remaining < 30,
        "expired": days_remaining < 0,
    }


def load_infra_data(infra_dir, entra_id_cert_expires_on=None, report_date=None):
    """
    infra_dir: carpeta con los HealthCheck_<host>_<timestamp>.json. Si no
    existe o no tiene archivos, devuelve servers=[] (la pestaña
    Infraestructura se muestra igual, con un mensaje de "sin datos").

    Si hay más de un archivo para el mismo host (varias corridas del
    script), se queda con el de "Fecha" más reciente.
    """
    report_date = report_date or date.today()

    # Archivos ignorados (JSON inválido o sin la estructura esperada) - un
    # archivo roto no debe tirar abajo todo el reporte; se informan para
    # mostrarlos como aviso (PAM Web Console, ver app/api/itr.py).
    skipped = []
    latest_by_host = {}
    if infra_dir and os.path.isdir(infra_dir):
        for path in sorted(glob.glob(os.path.join(infra_dir, "HealthCheck_*.json"))):
            name = os.path.basename(path)
            try:
                with open(path, encoding="utf-8-sig") as f:
                    data = json.load(f)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                skipped.append(name)
                continue
            if not isinstance(data, dict) or not data.get("Servidor"):
                skipped.append(name)
                continue
            hostname = data["Servidor"]
            fecha = _parse_fecha(data.get("Fecha"))
            if hostname not in latest_by_host or fecha > latest_by_host[hostname][0]:
                latest_by_host[hostname] = (fecha, data, name)

    servers = []
    for hostname in sorted(latest_by_host):
        _, data, name = latest_by_host[hostname]
        try:
            servers.append(_server_view(hostname, data, report_date))
        except (AttributeError, TypeError):
            skipped.append(name)

    entra_id_certificate = _cert_status(entra_id_cert_expires_on, report_date)

    return {
        "servers": servers,
        "entra_id_certificate": entra_id_certificate,
        "skipped_files": skipped,
    }


def _server_view(hostname, data, report_date):
    cpu = data.get("CPU") or {}
    ram = data.get("RAM") or {}
    disks = [
        {
            "unit": d.get("Unidad"),
            "pct": d.get("Porcentaje"),
            "used_gb": d.get("UsadoGB"),
            "total_gb": d.get("TotalGB"),
        }
        for d in data.get("Discos") or []
    ]
    services = [
        {
            "name": s.get("Nombre"),
            "running": (s.get("Estado") or "").strip().lower() == "running",
            "status_txt": s.get("Estado"),
        }
        for s in data.get("ServiciosCyberArk") or []
    ]
    cert_raw = data.get("CertificadoRDP") or {}
    cert_status = _cert_status(cert_raw.get("FechaVencimiento"), report_date)
    rdp_certificate = {"name": cert_raw.get("Nombre"), **cert_status} if cert_status else None

    return {
        "hostname": hostname,
        "checked_at": data.get("Fecha"),
        "cpu_pct": cpu.get("Porcentaje"),
        "ram_pct": ram.get("Porcentaje"),
        "ram_used_gb": ram.get("UsadoGB"),
        "ram_total_gb": ram.get("TotalGB"),
        "disks": disks,
        "services": services,
        "rdp_certificate": rdp_certificate,
    }
