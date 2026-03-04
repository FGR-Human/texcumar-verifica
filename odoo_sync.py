"""
odoo_sync.py — TEXCUMAR Portal de Verificación
Sincroniza Guías de Remisión de Odoo 17 → Google Sheets
Versión: 1.0 | Ejecución: 3x/día vía GitHub Actions
"""
import os
import json
import logging
import urllib.request
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("texcumar-sync")

# ── Configuración desde GitHub Secrets ───────────────────────────────────────
ODOO_URL      = os.environ["ODOO_URL"].rstrip("/")
ODOO_DB       = os.environ["ODOO_DB"]
ODOO_USER     = os.environ["ODOO_USER"]
ODOO_PASSWORD = os.environ["ODOO_PASSWORD"]
SHEET_ID      = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_CREDS  = os.environ["GOOGLE_CREDS_JSON"]   # JSON completo como string

# Nombre de la hoja dentro del spreadsheet
SHEET_TAB = "Guias"

# Columnas del Google Sheet (26 columnas A-Z, AA)
# Si un campo no existe en Odoo, queda en blanco — NO rompe el script
COLUMNS = [
    "numero",           # A  — "001-001-000058498"
    "autorizacion",     # B  — número autorización SRI (vacío si no aplica)
    "fechaAutorizacion",# C  — fecha autorización
    "base",             # D  — almacén/base de origen
    "codigoSCI",        # E  — código SCI interno
    "globalGAP",        # F  — certificación GlobalGAP
    "destinatario",     # G  — nombre del destinatario
    "rucDestino",       # H  — RUC del destinatario
    "nombreDest",       # I  — nombre lugar destino
    "destino",          # J  — ciudad/dirección destino
    "llegada",          # K  — fecha/hora llegada
    "motivo",           # L  — motivo traslado (Venta, etc.)
    "transportista",    # M  — nombre transportista
    "rucTransp",        # N  — RUC transportista
    "placa",            # O  — placa del vehículo
    "partida",          # P  — punto de partida
    "codProducto",      # Q  — código del producto
    "unidad",           # R  — unidad de medida
    "descripcion",      # S  — descripción producto
    "cantidad",         # T  — cantidad
    "cantBruta",        # U  — cantidad bruta
    "tecnico",          # V  — técnico responsable
    "despacho",         # W  — fecha/hora despacho
    "gavetas",          # X  — número de gavetas
    "plus",             # Y  — porcentaje plus
    "salinidad",        # Z  — salinidad
    "temperatura",      # AA — temperatura
]


# ═════════════════════════════════════════════════════════════════════════════
#  CLASE: OdooConnector
# ═════════════════════════════════════════════════════════════════════════════
class OdooConnector:
    """Maneja la conexión JSON-RPC con Odoo 17."""

    def __init__(self):
        self.uid = self._authenticate()
        log.info(f"Odoo: autenticado como '{ODOO_USER}' (UID={self.uid})")

    def _rpc(self, endpoint, params, retries=3):
        url = f"{ODOO_URL}{endpoint}"
        payload = json.dumps({
            "jsonrpc": "2.0", "method": "call", "id": 1, "params": params
        }).encode()
        for attempt in range(retries):
            try:
                req = urllib.request.Request(
                    url, data=payload,
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=60) as r:
                    resp = json.loads(r.read())
                if "error" in resp:
                    raise RuntimeError(f"Odoo RPC error: {resp['error']}")
                return resp["result"]
            except Exception as e:
                if attempt < retries - 1:
                    log.warning(f"RPC intento {attempt+1} falló: {e}. Reintentando...")
                else:
                    raise

    def _authenticate(self):
        return self._rpc("/web/dataset/call_kw", {
            "model": "res.users", "method": "authenticate",
            "args": [ODOO_DB, ODOO_USER, ODOO_PASSWORD, {}], "kwargs": {}
        })

    def search_read(self, model, domain, fields, limit=0, offset=0):
        return self._rpc("/web/dataset/call_kw", {
            "model": model, "method": "search_read",
            "args": [domain], "kwargs": {
                "fields": fields,
                "limit": limit,
                "offset": offset,
                "order": "id asc",
                "context": {"uid": self.uid, "lang": "es_EC"}
            }
        })

    def get_remission_guides(self):
        """Obtiene todas las guías en estado 'posted' (Publicado)."""
        log.info("Odoo: consultando guías publicadas...")

        # Campos principales del modelo account.remission.guide
        fields = [
            "name",                     # Número guía: "REM 001-001-000058498"
            "state",                    # Estado: posted
            "date_start",               # Fecha inicio traslado
            "date_end",                 # Fecha fin traslado
            "date_real_transfer",       # Fecha real de traslado (si existe)
            "partner_id",               # Destinatario (nombre)
            "l10n_ec_carrier_id",       # Transportista (locación Ecuador)
            "carrier_id",               # Transportista (fallback)
            "vehicle_plate",            # Matrícula/placa
            "l10n_ec_vehicle_plate",    # Placa (localización Ecuador)
            "picking_ids",              # IDs de entregas relacionadas
            "warehouse_id",             # Almacén origen
            "location_id",              # Ubicación origen (fallback)
            "note",                     # Observaciones
            "user_id",                  # Responsable/Técnico
            "l10n_ec_authorization",    # Autorización SRI
            "l10n_ec_authorization_date", # Fecha autorización SRI
        ]

        guides = self.search_read(
            "account.remission.guide",
            [["state", "=", "posted"]],
            fields
        )
        log.info(f"Odoo: encontradas {len(guides)} guías publicadas")
        return guides

    def get_picking_details(self, picking_ids):
        """Obtiene detalles de las entregas (líneas de productos)."""
        if not picking_ids:
            return []
        return self.search_read(
            "stock.picking",
            [["id", "in", picking_ids]],
            ["name", "move_ids", "partner_id", "origin",
             "location_id", "location_dest_id", "note"]
        )

    def get_move_details(self, move_ids):
        """Obtiene líneas de movimiento de productos."""
        if not move_ids:
            return []
        return self.search_read(
            "stock.move",
            [["id", "in", move_ids]],
            ["product_id", "product_uom_qty", "quantity_done",
             "product_uom", "product_tmpl_id"]
        )


# ═════════════════════════════════════════════════════════════════════════════
#  CLASE: DataTransformer
# ═════════════════════════════════════════════════════════════════════════════
class DataTransformer:
    """Transforma datos de Odoo al formato de 27 columnas de Google Sheets."""

    def __init__(self, odoo: OdooConnector):
        self.odoo = odoo

    @staticmethod
    def _clean_number(raw_name: str) -> str:
        """
        Convierte "REM 001-001-000058498" → "001-001-000058498"
        También acepta formatos sin prefijo.
        """
        if not raw_name:
            return ""
        # Eliminar prefijos comunes: REM, GR, GUIDE, etc.
        parts = raw_name.strip().split(" ", 1)
        if len(parts) == 2 and not parts[0].isdigit():
            return parts[1].strip()
        return raw_name.strip()

    @staticmethod
    def _get_related_name(field_val):
        """Extrae nombre de campo many2one: (id, 'Nombre') → 'Nombre'"""
        if isinstance(field_val, (list, tuple)) and len(field_val) >= 2:
            return str(field_val[1])
        if isinstance(field_val, str):
            return field_val
        return ""

    @staticmethod
    def _format_date(date_str):
        """Convierte fecha Odoo '2025-02-01 00:00:00' → '01/02/2025'"""
        if not date_str:
            return ""
        try:
            # Probar formato datetime primero
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(str(date_str)[:19], fmt)
                    return dt.strftime("%d/%m/%Y")
                except ValueError:
                    continue
        except Exception:
            pass
        return str(date_str)

    def transform(self, guide: dict, pickings: list, moves: list) -> list:
        """
        Convierte un registro de Odoo en una fila de 27 valores para Google Sheets.
        El orden corresponde exactamente a las columnas A-AA.
        """
        # Número de guía normalizado (sin prefijo REM)
        numero = self._clean_number(self._get_related_name(guide.get("name", "")))

        # Transportista — probar campos locales Ecuador primero
        transportista_raw = (
            guide.get("l10n_ec_carrier_id") or
            guide.get("carrier_id") or
            False
        )
        transportista = self._get_related_name(transportista_raw)

        # Placa — probar campo local Ecuador primero
        placa = (
            guide.get("l10n_ec_vehicle_plate") or
            guide.get("vehicle_plate") or
            ""
        )
        if isinstance(placa, (list, tuple)):
            placa = placa[1] if len(placa) > 1 else ""

        # Almacén/base
        base = self._get_related_name(guide.get("warehouse_id") or guide.get("location_id"))

        # Destinatario
        partner = guide.get("partner_id", False)
        destinatario = self._get_related_name(partner)

        # RUC destinatario — buscar en partner si disponible
        ruc_destino = ""  # Se obtiene de partner_id.vat si se expande el query

        # Motivo — de las líneas de entrega (picking reason)
        motivo = ""
        destino_ciudad = ""
        partida = self._get_related_name(guide.get("location_id", False))

        if pickings:
            # Tomar datos del primer picking relacionado
            p = pickings[0]
            motivo = p.get("origin", "") or ""
            destino_ciudad = self._get_related_name(p.get("location_dest_id", False))

        # Producto (primera línea de movimiento)
        cod_producto = ""
        unidad = ""
        descripcion = ""
        cantidad = ""
        cant_bruta = ""

        if moves:
            m = moves[0]
            prod = m.get("product_id", False)
            descripcion = self._get_related_name(prod)
            # Código del producto (referencia interna)
            cod_producto = str(prod[0]) if isinstance(prod, (list, tuple)) else ""
            uom = m.get("product_uom", False)
            unidad = self._get_related_name(uom)
            qty_done = m.get("quantity_done", 0) or m.get("product_uom_qty", 0)
            cantidad = str(int(qty_done)) if qty_done else ""
            cant_bruta = cantidad  # Misma cantidad si no hay campo separado

        # Técnico responsable
        tecnico = self._get_related_name(guide.get("user_id", False))

        # Fechas
        fecha_despacho = self._format_date(guide.get("date_start"))
        fecha_llegada  = self._format_date(guide.get("date_end"))
        fecha_autorizacion = self._format_date(guide.get("l10n_ec_authorization_date"))

        # Autorización SRI
        autorizacion = guide.get("l10n_ec_authorization") or ""
        if isinstance(autorizacion, bool):
            autorizacion = ""

        # Construir fila en el orden exacto de COLUMNS
        row = [
            numero,              # A: numero
            str(autorizacion),   # B: autorizacion
            fecha_autorizacion,  # C: fechaAutorizacion
            base,                # D: base
            "",                  # E: codigoSCI (campo personalizado TEXCUMAR)
            "",                  # F: globalGAP (campo personalizado TEXCUMAR)
            destinatario,        # G: destinatario
            ruc_destino,         # H: rucDestino
            destinatario,        # I: nombreDest (mismo que destinatario)
            destino_ciudad,      # J: destino
            fecha_llegada,       # K: llegada
            motivo,              # L: motivo
            transportista,       # M: transportista
            "",                  # N: rucTransp (expandir query si se necesita)
            str(placa),          # O: placa
            partida,             # P: partida
            cod_producto,        # Q: codProducto
            unidad,              # R: unidad
            descripcion,         # S: descripcion
            cantidad,            # T: cantidad
            cant_bruta,          # U: cantBruta
            tecnico,             # V: tecnico
            fecha_despacho,      # W: despacho
            "",                  # X: gavetas (campo personalizado TEXCUMAR)
            "",                  # Y: plus (campo personalizado TEXCUMAR)
            "",                  # Z: salinidad (campo personalizado TEXCUMAR)
            "",                  # AA: temperatura (campo personalizado TEXCUMAR)
        ]
        return row


# ═════════════════════════════════════════════════════════════════════════════
#  CLASE: SheetsWriter
# ═════════════════════════════════════════════════════════════════════════════
class SheetsWriter:
    """Gestiona la escritura en Google Sheets."""

    HEADER = COLUMNS  # Primera fila = nombres de columnas

    def __init__(self):
        creds_dict = json.loads(GOOGLE_CREDS)
        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)

        spreadsheet = client.open_by_key(SHEET_ID)

        # Abrir o crear la pestaña "Guias"
        try:
            self.sheet = spreadsheet.worksheet(SHEET_TAB)
            log.info(f"Sheets: pestaña '{SHEET_TAB}' encontrada")
        except gspread.WorksheetNotFound:
            self.sheet = spreadsheet.add_worksheet(
                title=SHEET_TAB, rows=1000, cols=27
            )
            log.info(f"Sheets: pestaña '{SHEET_TAB}' creada")

    def _ensure_header(self):
        """Asegura que la primera fila tenga los headers correctos."""
        first_row = self.sheet.row_values(1)
        if not first_row or first_row[0] != "numero":
            self.sheet.insert_row(self.HEADER, index=1)
            log.info("Sheets: headers insertados")

    def _get_existing_numbers(self) -> dict:
        """
        Lee la columna A (numero) y retorna {numero: fila_index}.
        Fila 1 = header, datos desde fila 2.
        """
        all_values = self.sheet.get_all_values()
        existing = {}
        for i, row in enumerate(all_values[1:], start=2):  # skip header
            if row and row[0]:
                existing[row[0].strip()] = i
        return existing

    def upsert_guides(self, rows: list[list]) -> dict:
        """
        Inserta filas nuevas y actualiza las existentes.
        Retorna estadísticas del sync.
        """
        self._ensure_header()
        existing = self._get_existing_numbers()

        inserted = 0
        updated  = 0
        skipped  = 0

        for row in rows:
            numero = row[0].strip() if row[0] else ""
            if not numero:
                skipped += 1
                continue

            if numero in existing:
                # Actualizar fila existente
                row_idx = existing[numero]
                col_end = chr(ord('A') + len(row) - 1)
                range_name = f"A{row_idx}:{col_end}{row_idx}"
                self.sheet.update(range_name, [row])
                updated += 1
                log.debug(f"  Actualizado: {numero}")
            else:
                # Insertar nueva fila al final
                self.sheet.append_row(row, value_input_option="USER_ENTERED")
                inserted += 1
                log.info(f"  Insertado: {numero}")

        return {"inserted": inserted, "updated": updated, "skipped": skipped}


# ═════════════════════════════════════════════════════════════════════════════
#  FUNCIÓN PRINCIPAL
# ═════════════════════════════════════════════════════════════════════════════
def main():
    start_time = datetime.now(timezone.utc)
    log.info("=" * 60)
    log.info("TEXCUMAR Sync — Inicio")
    log.info(f"Hora UTC: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    try:
        # 1. Conectar a Odoo
        odoo = OdooConnector()

        # 2. Obtener todas las guías publicadas
        guides = odoo.get_remission_guides()

        if not guides:
            log.info("No hay guías publicadas para sincronizar.")
            return

        # 3. Transformar datos
        transformer = DataTransformer(odoo)
        rows = []

        for guide in guides:
            try:
                # Obtener pickings y moves relacionados
                picking_ids = guide.get("picking_ids", [])
                pickings = odoo.get_picking_details(picking_ids) if picking_ids else []

                move_ids = []
                for p in pickings:
                    move_ids.extend(p.get("move_ids", []))
                moves = odoo.get_move_details(move_ids) if move_ids else []

                row = transformer.transform(guide, pickings, moves)
                rows.append(row)

            except Exception as e:
                guia_name = guide.get("name", "N/A")
                log.warning(f"Error transformando guía '{guia_name}': {e}")
                continue

        log.info(f"Transformadas {len(rows)} guías correctamente")

        # 4. Escribir en Google Sheets
        writer = SheetsWriter()
        stats = writer.upsert_guides(rows)

        # 5. Resumen final
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        log.info("=" * 60)
        log.info(f"✅ Sync completado en {elapsed:.1f}s")
        log.info(f"   Insertadas: {stats['inserted']}")
        log.info(f"   Actualizadas: {stats['updated']}")
        log.info(f"   Omitidas: {stats['skipped']}")
        log.info("=" * 60)

    except Exception as e:
        log.error(f"❌ Error crítico en sync: {e}")
        raise  # Re-lanzar para que GitHub Actions marque el job como fallido


if __name__ == "__main__":
    main()
