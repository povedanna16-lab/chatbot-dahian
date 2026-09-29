# -*- coding: utf-8 -*-
"""
tienda_db.py
=============
Módulo de base de datos relacional (SQLite) para la tienda del
"Centro de Biotecnología Agropecuaria".

Este módulo crea (si no existe) el archivo tienda.db con una tabla
"productos", la llena con datos de demostración la primera vez que se
ejecuta, y expone funciones de consulta que el chatbot Dahian usa para
responder preguntas reales sobre el catálogo (nombre, categoría,
precio, disponibilidad), en vez de dar respuestas fijas de texto.

Los precios están en pesos colombianos (COP).
"""

import sqlite3
import os

RUTA_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tienda.db")

# ---------------------------------------------------------------------
# Datos de demostración
# ---------------------------------------------------------------------
# (nombre, categoria, precio, stock, descripcion)
PRODUCTOS_DEMO = [
    # Semillas certificadas
    ("Semilla certificada de maíz ICA V-109", "Semillas certificadas", 28500, 120,
     "Bolsa de 1 kg, alto rendimiento, resistente a plagas comunes de la región."),
    ("Semilla certificada de frijol Cargamanto", "Semillas certificadas", 19900, 80,
     "Bolsa de 1 kg, variedad tradicional de alta demanda comercial."),
    ("Semilla certificada de arroz Fedearroz 60", "Semillas certificadas", 15200, 200,
     "Bolsa de 1 kg, ciclo corto, ideal para clima cálido."),
    ("Semilla de pasto Kikuyo", "Semillas certificadas", 22000, 0,
     "Bolsa de 500 g para renovación de praderas de ganado. Agotado temporalmente."),
    ("Semilla de aguacate Hass (patrón)", "Semillas certificadas", 3500, 300,
     "Semilla individual para germinación de patrón de aguacate Hass."),

    # Abonos y fertilizantes
    ("Abono orgánico compostado", "Abonos y fertilizantes", 32000, 60,
     "Bulto de 40 kg, compost 100% orgánico, mejora la estructura del suelo."),
    ("Fertilizante NPK 10-30-10", "Abonos y fertilizantes", 98000, 45,
     "Bulto de 50 kg, fórmula balanceada para etapa de floración."),
    ("Fertilizante foliar líquido", "Abonos y fertilizantes", 24500, 90,
     "Presentación de 1 litro, absorción rápida vía foliar."),
    ("Cal agrícola dolomita", "Abonos y fertilizantes", 18700, 70,
     "Bulto de 25 kg, corrige acidez del suelo y aporta calcio y magnesio."),
    ("Humus de lombriz", "Abonos y fertilizantes", 21500, 0,
     "Bulto de 20 kg, abono orgánico premium. Agotado, próximo lote en 2 semanas."),

    # Plántulas y material vegetal
    ("Plántula de tomate chonto (bandeja x50)", "Plántulas y material vegetal", 45000, 25,
     "Bandeja de 50 plántulas, listas para trasplante, sanidad certificada."),
    ("Plántula de café variedad Castillo", "Plántulas y material vegetal", 1800, 500,
     "Plántula individual en bolsa, resistente a la roya."),
    ("Plántula de cacao clonal", "Plántulas y material vegetal", 4200, 250,
     "Plántula clonal de alto rendimiento, injerto certificado."),
    ("Esqueje de mora de Castilla", "Plántulas y material vegetal", 2500, 180,
     "Esqueje enraizado, listo para siembra directa."),
    ("Plántula de plátano Dominico Hartón", "Plántulas y material vegetal", 3800, 0,
     "Colino certificado libre de Moko y Sigatoka. Agotado hasta nueva cosecha."),

    # Kits y laboratorio
    ("Kit de análisis de suelo (pH, N-P-K)", "Kits y laboratorio", 156000, 15,
     "Kit portátil para medir pH, nitrógeno, fósforo y potasio en campo."),
    ("Kit de propagación in vitro básico", "Kits y laboratorio", 320000, 8,
     "Incluye medios de cultivo, frascos y reactivos básicos para el laboratorio."),
    ("Microscopio de laboratorio 40x-1000x", "Kits y laboratorio", 480000, 5,
     "Microscopio óptico binocular para prácticas de biotecnología."),
    ("Kit de bioseguridad (guantes, tapabocas, bata)", "Kits y laboratorio", 65000, 40,
     "Set completo de protección personal para manejo de muestras."),
    ("Cámara de flujo laminar portátil", "Kits y laboratorio", 1250000, 0,
     "Uso exclusivo del laboratorio del Centro. Disponible solo bajo pedido especial."),

    # Control biológico y biopreparados
    ("Biopreparado a base de Trichoderma", "Control biológico", 27500, 55,
     "Presentación de 1 litro, controlador biológico de hongos del suelo."),
    ("Biopreparado de Beauveria bassiana", "Control biológico", 29900, 38,
     "Presentación de 500 ml, control biológico de insectos plaga."),
    ("Extracto de ajo-ají biocontrolador", "Control biológico", 16800, 70,
     "Presentación de 1 litro, repelente natural de insectos."),
    ("Trampas amarillas para monitoreo (x10)", "Control biológico", 12300, 100,
     "Paquete de 10 trampas adhesivas para monitoreo de plagas voladoras."),
    ("Feromonas para control de plagas", "Control biológico", 38900, 20,
     "Set de trampas con feromonas específicas para lepidópteros."),

    # Herramientas agrícolas
    ("Azadón forjado con cabo", "Herramientas agrícolas", 34500, 65,
     "Herramienta forjada en acero, cabo de madera resistente."),
    ("Machete de acero al carbono", "Herramientas agrícolas", 28900, 90,
     "Hoja de 18 pulgadas, ideal para labores de campo."),
    ("Tijeras de podar profesionales", "Herramientas agrícolas", 42000, 30,
     "Tijeras de podar de precisión, mango ergonómico antideslizante."),
    ("Aspersora manual 16 litros", "Herramientas agrícolas", 89000, 22,
     "Fumigadora de mochila, presión manual, ideal para pequeñas extensiones."),
    ("Guantes de carnaza para labranza", "Herramientas agrícolas", 15900, 0,
     "Talla única, protección reforzada. Agotado, nuevo pedido en camino."),

    # Huevos
    ("Huevo AAA x30 (cubeta)", "Huevos", 19500, 60,
     "Cubeta de 30 unidades, calibre extra grande, producción del Centro."),
    ("Huevo AA x30 (cubeta)", "Huevos", 17200, 90,
     "Cubeta de 30 unidades, calibre grande."),
    ("Huevo A x30 (cubeta)", "Huevos", 15000, 100,
     "Cubeta de 30 unidades, calibre mediano."),
    ("Huevo campesino x12 (bandeja)", "Huevos", 12800, 0,
     "Bandeja de 12 unidades, gallinas de campo libre. Agotado, nueva producción en 3 días."),

    # Flores
    ("Ramo de rosas rojas x12", "Flores", 38000, 25,
     "Docena de rosas rojas frescas, cultivadas en el Centro."),
    ("Ramo de claveles x12", "Flores", 19500, 40,
     "Docena de claveles de colores surtidos."),
    ("Girasol (unidad)", "Flores", 6000, 70,
     "Girasol fresco cortado, tallo largo."),
    ("Orquídea en materera", "Flores", 48000, 0,
     "Orquídea en materera de 15 cm, lista para exhibición. Agotada por alta demanda."),

    # Lácteos
    ("Leche entera x1L", "Lácteos", 3600, 150,
     "Leche entera pasteurizada, producción de la unidad ganadera del Centro."),
    ("Leche deslactosada x1L", "Lácteos", 4300, 80,
     "Leche deslactosada pasteurizada, presentación de 1 litro."),
    ("Yogur natural x1L", "Lácteos", 8700, 50,
     "Yogur natural artesanal, sin azúcar añadida."),
    ("Queso campesino x500g", "Lácteos", 13500, 0,
     "Queso campesino fresco, presentación de 500 g. Agotado, nuevo lote la próxima semana."),
]


def conectar():
    """Devuelve una conexión SQLite con filas accesibles por nombre de columna."""
    conexion = sqlite3.connect(RUTA_DB)
    conexion.row_factory = sqlite3.Row
    return conexion


def inicializar_db():
    """
    Crea la tabla 'productos' si no existe y la llena con los datos de
    demostración solo si está vacía (para no duplicar datos cada vez
    que se reinicia el servidor).
    """
    conexion = conectar()
    cursor = conexion.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            categoria TEXT NOT NULL,
            precio INTEGER NOT NULL,
            stock INTEGER NOT NULL,
            descripcion TEXT
        )
    """)
    cursor.execute("SELECT COUNT(*) FROM productos")
    total = cursor.fetchone()[0]
    if total == 0:
        cursor.executemany(
            "INSERT INTO productos (nombre, categoria, precio, stock, descripcion) VALUES (?, ?, ?, ?, ?)",
            PRODUCTOS_DEMO,
        )
        conexion.commit()
    conexion.close()


def obtener_todos():
    """Devuelve todos los productos como lista de diccionarios."""
    conexion = conectar()
    filas = conexion.execute("SELECT * FROM productos ORDER BY categoria, nombre").fetchall()
    conexion.close()
    return [dict(f) for f in filas]


def obtener_categorias():
    """Devuelve la lista de categorías distintas disponibles en la tienda."""
    conexion = conectar()
    filas = conexion.execute("SELECT DISTINCT categoria FROM productos ORDER BY categoria").fetchall()
    conexion.close()
    return [f["categoria"] for f in filas]


def buscar_por_categoria(categoria):
    """Devuelve los productos que pertenecen a una categoría (coincidencia parcial, sin distinguir mayúsculas)."""
    conexion = conectar()
    filas = conexion.execute(
        "SELECT * FROM productos WHERE LOWER(categoria) LIKE ? ORDER BY nombre",
        (f"%{categoria.lower()}%",),
    ).fetchall()
    conexion.close()
    return [dict(f) for f in filas]


def buscar_por_nombre(texto):
    """Devuelve los productos cuyo nombre o descripción contiene el texto buscado."""
    conexion = conectar()
    patron = f"%{texto.lower()}%"
    filas = conexion.execute(
        "SELECT * FROM productos WHERE LOWER(nombre) LIKE ? OR LOWER(descripcion) LIKE ? ORDER BY nombre",
        (patron, patron),
    ).fetchall()
    conexion.close()
    return [dict(f) for f in filas]


def productos_disponibles(lista_productos):
    """Filtra una lista de productos dejando solo los que tienen stock > 0."""
    return [p for p in lista_productos if p["stock"] > 0]


def producto_mas_barato(lista_productos=None):
    """Devuelve el producto con menor precio (de toda la tienda o de una lista dada)."""
    productos = lista_productos if lista_productos is not None else obtener_todos()
    disponibles = [p for p in productos if p["stock"] > 0]
    origen = disponibles if disponibles else productos
    if not origen:
        return None
    return min(origen, key=lambda p: p["precio"])


def filtrar_por_precio_maximo(precio_max, lista_productos=None):
    """Devuelve los productos con precio menor o igual al indicado."""
    productos = lista_productos if lista_productos is not None else obtener_todos()
    return [p for p in productos if p["precio"] <= precio_max]


def formatear_precio(valor):
    """Formatea un entero como precio en pesos colombianos, ej: 45000 -> '$45.000'."""
    return "$" + f"{valor:,.0f}".replace(",", ".")


if __name__ == "__main__":
    inicializar_db()
    print(f"Base de datos inicializada en: {RUTA_DB}")
    print(f"Total de productos: {len(obtener_todos())}")
    print(f"Categorías: {obtener_categorias()}")
