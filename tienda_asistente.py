# -*- coding: utf-8 -*-
"""
tienda_asistente.py
====================
Módulo del "asistente de tienda" de Dahian.

Este módulo detecta cuándo el usuario está preguntando por productos
del Centro de Biotecnología Agropecuaria (catálogo, categorías,
precios, disponibilidad) y arma la respuesta consultando DIRECTAMENTE
la base de datos relacional (tienda_db.py / tienda.db), en vez de usar
texto fijo o buscar en internet.

También incluye un pequeño flujo guiado de "intención de compra": si
el usuario dice que quiere comprar algo, Dahian confirma cantidad y
nombre, y entrega un resumen de pedido (una simulación pensada para el
proyecto académico, no una pasarela de pagos real).

Cada función de respuesta devuelve TRES valores:
    (texto_respuesta, nuevo_estado_pedido, opciones)
'opciones' es una lista corta de textos (o None) pensada para mostrarse
como botones de selección rápida en el chat (como un menú de IVR/EPS:
"1. Consultar producto  2. Ver precios  3. Hacer un pedido"), para que
el usuario no tenga que escribir todo a mano y así evitar errores de
tipeo que lo saquen del flujo.
"""

import re
import unicodedata
import difflib

import tienda_db as db


# ---------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------

def _normalizar(texto):
    forma = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in forma if not unicodedata.combining(c))


# Alias público: app.py usa esta función para normalizar el mensaje antes
# de preguntarle a este módulo si se trata de una consulta de tienda.
normalizar = _normalizar


PALABRAS_TIENDA = [
    "producto", "productos", "precio", "precios", "cuesta", "cuestan",
    "vale", "valen", "catalogo", "categoria", "categorias", "tienen",
    "hay disponible", "disponible", "disponibilidad", "stock", "existencia",
    "comprar", "compra", "pedido", "carrito", "tienda", "vender", "venden",
    "mas barato", "mas economico", "mas barata", "oferta",
]

# Palabras clave de cada categoría (para reconocer sinónimos frecuentes)
CATEGORIAS_SINONIMOS = {
    "Semillas certificadas": ["semilla", "semillas"],
    "Abonos y fertilizantes": ["abono", "abonos", "fertilizante", "fertilizantes", "cal agricola", "humus"],
    "Plántulas y material vegetal": ["plantula", "plantulas", "esqueje", "colino", "material vegetal"],
    "Kits y laboratorio": ["kit", "kits", "laboratorio", "microscopio", "bioseguridad"],
    "Control biológico": ["biopreparado", "biopreparados", "control biologico", "trichoderma",
                           "beauveria", "biocontrolador", "trampas", "feromonas", "plaga", "plagas"],
    "Herramientas agrícolas": ["herramienta", "herramientas", "azadon", "machete", "tijeras", "aspersora", "guantes"],
    "Huevos": ["huevo", "huevos", "cubeta"],
    "Flores": ["flor", "flores", "ramo", "rosas", "claveles", "girasol", "orquidea"],
    "Lácteos": ["lacteo", "lacteos", "leche", "yogur", "queso"],
}


# Vocabulario completo de la tienda (para tolerar pequeños errores de tipeo,
# ej. "perdido" en vez de "pedido"), armado una sola vez a partir de las
# listas de arriba.
_VOCABULARIO_TIENDA = set()
for _frase in PALABRAS_TIENDA:
    _VOCABULARIO_TIENDA.update(_frase.split())
for _sinonimos in CATEGORIAS_SINONIMOS.values():
    for _s in _sinonimos:
        _VOCABULARIO_TIENDA.update(_s.split())


def _parece_palabra_de_tienda_con_typo(texto_normalizado, cutoff=0.82):
    """
    Revisa si alguna palabra del mensaje es muy parecida (aunque no igual) a
    una palabra del vocabulario de la tienda, para no perder la intención
    del usuario por un pequeño error de tipeo (ej. escribir "perdido" en vez
    de "pedido"). Solo mira palabras de 5 letras o más, para evitar falsos
    positivos con palabras cortas.
    """
    palabras = re.findall(r"[a-z]+", texto_normalizado)
    for palabra in palabras:
        if len(palabra) < 5:
            continue
        if difflib.get_close_matches(palabra, _VOCABULARIO_TIENDA, n=1, cutoff=cutoff):
            return True
    return False


def detectar_consulta_tienda(texto_normalizado):
    """Devuelve True si el mensaje parece una pregunta sobre la tienda/catálogo."""
    if any(p in texto_normalizado for p in PALABRAS_TIENDA):
        return True
    for sinonimos in CATEGORIAS_SINONIMOS.values():
        if any(s in texto_normalizado for s in sinonimos):
            return True
    # Coincidencia directa con el nombre de algún producto de la base de datos
    for producto in db.obtener_todos():
        if _normalizar(producto["nombre"]) in texto_normalizado:
            return True
    # Último intento: palabras muy parecidas a las de la tienda (typos).
    if _parece_palabra_de_tienda_con_typo(texto_normalizado):
        return True
    return False


def _categoria_mencionada(texto_normalizado):
    for categoria, sinonimos in CATEGORIAS_SINONIMOS.items():
        if _normalizar(categoria) in texto_normalizado:
            return categoria
        if any(s in texto_normalizado for s in sinonimos):
            return categoria
    return None


def _categoria_por_nombre_exacto(texto_normalizado):
    """
    Devuelve la categoría solo si su NOMBRE COMPLETO aparece en el mensaje
    (ej. 'semillas certificadas'), a diferencia de _categoria_mencionada
    que también reconoce sinónimos sueltos. Se usa para dar prioridad a
    una pregunta por categoría completa por encima de una coincidencia
    parcial con el nombre de un solo producto.
    """
    for categoria in CATEGORIAS_SINONIMOS:
        if _normalizar(categoria) in texto_normalizado:
            return categoria
    return None


def _extraer_precio_maximo(texto_normalizado):
    """Busca un número en frases como 'menos de 50000' o 'por debajo de 30 mil'."""
    match = re.search(r"(menos de|maximo|menor a|por debajo de)\s*\$?\s*([\d\.]+)\s*(mil)?", texto_normalizado)
    if not match:
        return None
    numero = match.group(2).replace(".", "")
    valor = int(numero)
    if match.group(3):  # "mil"
        valor *= 1000
    return valor


def _linea_producto(p):
    estado = "Disponible" if p["stock"] > 0 else "Agotado"
    return f"• {p['nombre']} — {db.formatear_precio(p['precio'])} ({estado}, stock: {p['stock']})"


def _listar_productos(lista, encabezado, limite=6):
    if not lista:
        return None, None
    partes = [encabezado]
    for p in lista[:limite]:
        partes.append(_linea_producto(p))
    if len(lista) > limite:
        partes.append(f"...y {len(lista) - limite} producto(s) más. Pregúntame por una categoría específica para ver el resto.")
    partes.append("Elige un producto para ver el detalle, o toca \"Iniciar pedido\".")
    opciones = [p["nombre"] for p in lista[:limite]] + ["Iniciar pedido"]
    return "\n".join(partes), opciones


# Botones de categoría, reutilizados en varias respuestas ("menú" tipo IVR).
def _opciones_categorias():
    return list(db.obtener_categorias())


# ---------------------------------------------------------------------
# Flujo guiado de intención de compra (usa el "session" de Flask)
# ---------------------------------------------------------------------

PALABRAS_COMPRA = [
    "quiero comprar", "comprar", "quiero pedir", "hacer un pedido", "hacer pedido",
    "deseo comprar", "me interesa comprar", "iniciar pedido", "iniciar un pedido",
    "iniciar mi pedido", "nuevo pedido", "empezar pedido", "empezar un pedido",
    "comenzar pedido", "comenzar un pedido", "quiero un pedido", "quiero hacer un pedido",
    "hagamos un pedido", "realizar un pedido", "realizar pedido",
]

# Para reconocer una respuesta corta de sí/no después de que Dahian pregunta
# "¿quieres que iniciemos un pedido de este producto?". Los botones rápidos
# envían exactamente "Sí" / "No", que ya caen dentro de estas listas.
PALABRAS_SI = [
    "si", "sí", "claro", "dale", "de una", "por supuesto", "vale", "ok",
    "bueno", "hazlo", "listo", "obvio", "va",
]
PALABRAS_NO = ["no", "no gracias", "todavia no", "todavía no", "ahora no", "despues", "después"]


def _es_si(texto_normalizado):
    return texto_normalizado in PALABRAS_SI or any(
        texto_normalizado == p or texto_normalizado.startswith(p + " ") for p in PALABRAS_SI
    )


def _es_no(texto_normalizado):
    return texto_normalizado in PALABRAS_NO or any(texto_normalizado.startswith(p) for p in PALABRAS_NO)


# Texto de autorización de tratamiento de datos personales, conforme a la
# Ley 1581 de 2012 (Habeas Data) de Colombia. Se muestra ANTES de pedir el
# nombre y el contacto del cliente, y solo si acepta se continúa guardando
# esa información en la base de datos.
AVISO_TRATAMIENTO_DATOS = (
    "Antes de continuar, necesito tu autorización para el tratamiento de "
    "datos personales (Ley 1581 de 2012 - Habeas Data). Tu nombre y datos "
    "de contacto se usarán únicamente para que un asesor del Centro de "
    "Biotecnología Agropecuaria (SENA) gestione tu pedido, y se guardan de "
    "forma segura en nuestra base de datos. Puedes consultar la política de "
    "tratamiento de datos del SENA en /politica-privacidad.\n"
    "¿Autorizas el uso de tus datos para registrar este pedido?"
)


def _detalle_producto(p):
    """Arma la ficha de un producto y deja pendiente la confirmación de compra."""
    if p["stock"] <= 0:
        # Si está agotado, no tiene sentido preguntar "¿quieres pedirlo?":
        # ni ofrecemos el botón de confirmación ni dejamos pendiente ese
        # estado, para no confundir con una pregunta que no se puede
        # responder que sí.
        respuesta = (
            f"{p['nombre']} ({p['categoria']})\n"
            f"Precio: {db.formatear_precio(p['precio'])}\n"
            "Disponibilidad: Agotado (stock: 0)\n"
            f"{p['descripcion']}\n"
            "Por ahora no hay unidades disponibles. ¿Quieres ver otro producto de la categoría "
            f"'{p['categoria']}' o de otra?"
        )
        opciones = [p["categoria"]] + [c for c in db.obtener_categorias() if c != p["categoria"]]
        # Dejamos un estado activo (aunque no sea un pedido) para que si el
        # usuario responde con un simple "sí"/"no" a esta pregunta, no se
        # pierda el contexto ni caiga en una búsqueda genérica sin relación.
        estado = {"paso": "producto_agotado", "categoria": p["categoria"]}
        return respuesta, estado, opciones

    respuesta = (
        f"{p['nombre']} ({p['categoria']})\n"
        f"Precio: {db.formatear_precio(p['precio'])}\n"
        f"Disponibilidad: Disponible (stock: {p['stock']})\n"
        f"{p['descripcion']}\n"
        "¿Quieres que iniciemos un pedido de este producto?"
    )
    estado = {"paso": "confirmar_compra", "producto_id": p["id"], "producto_nombre": p["nombre"]}
    return respuesta, estado, ["Sí", "No"]


def _tokenizar(texto_normalizado):
    return set(re.findall(r"[a-z0-9]+", texto_normalizado))


def _buscar_producto_mencionado(texto_normalizado, minimo_coincidencias=1):
    """
    Encuentra el producto de la base de datos cuyo nombre coincide con el
    mensaje, comparando PALABRAS COMPLETAS (no subcadenas). Comparar
    subcadenas causaba falsos positivos como que "certificada" (parte del
    nombre de un producto) coincidiera con "certificadas" (plural escrito
    por el usuario), hacía que preguntar por toda la categoría "semillas
    certificadas" devolviera un solo producto al azar en vez de la lista
    completa de la categoría.
    """
    tokens_texto = _tokenizar(texto_normalizado)
    mejor = None
    for p in db.obtener_todos():
        nombre_norm = _normalizar(p["nombre"])
        # Además de palabras de 4+ letras, se aceptan los códigos de calibre
        # de huevo "aa"/"aaa" como palabras válidas para distinguir entre
        # esos productos aunque sean cortas.
        palabras_nombre = [
            w for w in re.findall(r"[a-z0-9]+", nombre_norm)
            if len(w) >= 4 or re.fullmatch(r"a{2,3}", w)
        ]
        palabras_coincidentes = [w for w in palabras_nombre if w in tokens_texto]
        coincidencias = len(palabras_coincidentes)
        # Una sola palabra larga y poco común (7+ letras, ej. "bioseguridad"
        # o "microscopio") ya identifica el producto sin ambigüedad, aunque
        # el resto del nombre (kit, de...) no aparezca en el mensaje.
        tiene_palabra_distintiva = any(len(w) >= 7 for w in palabras_coincidentes)
        if (coincidencias >= minimo_coincidencias or tiene_palabra_distintiva) and (
            mejor is None or coincidencias > mejor[0]
        ):
            mejor = (coincidencias, p)
    return mejor[1] if mejor else None


def _iniciar_flujo_compra(texto_normalizado):
    producto = _buscar_producto_mencionado(texto_normalizado)
    if not producto:
        categorias = ", ".join(db.obtener_categorias())
        return (
            "Con gusto te ayudo a hacer un pedido. ¿Qué producto te interesa? "
            f"Estas son nuestras categorías: {categorias}."
        ), None, _opciones_categorias()

    if producto["stock"] <= 0:
        return (
            f"'{producto['nombre']}' está agotado por ahora. "
            "¿Quieres que te muestre otro producto parecido de la misma categoría?"
        ), None, _opciones_categorias()

    estado = {
        "paso": "cantidad", "producto_id": producto["id"], "producto_nombre": producto["nombre"],
        "items": [],
    }
    return (
        f"Perfecto, '{producto['nombre']}' cuesta {db.formatear_precio(producto['precio'])} "
        f"y tenemos {producto['stock']} unidades disponibles. ¿Cuántas unidades quieres pedir?"
    ), estado, ["1", "2", "3", "5", "10"]


HORARIO_ATENCION = "8:00 a.m. a 6:00 p.m"

# Medios de pago aceptados. El emoji funciona como "logo" del medio de pago
# dentro del chat (no usamos los logotipos oficiales de las marcas, por
# temas de derechos de marca, pero cada opción se distingue visualmente).
MEDIOS_PAGO = [
    ("💳 Tarjeta de crédito", ["tarjeta de credito", "tarjeta credito", "credito"]),
    ("💳 Tarjeta débito", ["tarjeta de debito", "tarjeta debito", "debito"]),
    ("📱 Nequi", ["nequi"]),
    ("📱 Daviplata", ["daviplata", "davi plata"]),
    ("💵 Efectivo contraentrega", ["efectivo", "contraentrega", "contra entrega"]),
]


def _opciones_medios_pago():
    return [etiqueta for etiqueta, _ in MEDIOS_PAGO]


def _detectar_medio_pago(texto_normalizado):
    for etiqueta, claves in MEDIOS_PAGO:
        if any(clave in texto_normalizado for clave in claves):
            return etiqueta
    return None


def _texto_carrito(items):
    if not items:
        return "Tu carrito está vacío."
    partes = ["Tu carrito:"]
    total = 0
    for it in items:
        subtotal = it["cantidad"] * it["precio_unitario"]
        total += subtotal
        partes.append(f"• {it['cantidad']} x {it['producto_nombre']} — {db.formatear_precio(subtotal)}")
    partes.append(f"Total del carrito: {db.formatear_precio(total)}")
    return "\n".join(partes)


def _finalizar_pedido(estado, tipo_entrega, municipio_domicilio, medio_pago):
    """
    Guarda el pedido ya completo (con el carrito, datos de contacto,
    entrega y medio de pago) en la base de datos y arma el resumen final,
    incluyendo el horario de atención y despacho del Centro.
    """
    items = estado["items"]
    total = sum(it["cantidad"] * it["precio_unitario"] for it in items)
    pedido_id = db.crear_pedido(
        items=items,
        cliente_nombre=estado["cliente_nombre"],
        cliente_contacto=estado["cliente_contacto"],
        tipo_entrega=tipo_entrega,
        municipio_domicilio=municipio_domicilio,
        medio_pago=medio_pago,
    )
    linea_entrega = (
        f"• Entrega: Domicilio en {municipio_domicilio} (Cundinamarca)"
        if tipo_entrega == "Domicilio"
        else "• Entrega: Recoge en el Centro de Biotecnología Agropecuaria"
    )
    lineas_items = "\n".join(
        f"  - {it['cantidad']} x {it['producto_nombre']} — "
        f"{db.formatear_precio(it['cantidad'] * it['precio_unitario'])}"
        for it in items
    )
    resumen = (
        f"Pedido #{pedido_id} registrado:\n"
        f"• Cliente: {estado['cliente_nombre']}\n"
        f"• Contacto: {estado['cliente_contacto']}\n"
        f"• Productos:\n{lineas_items}\n"
        f"• Total: {db.formatear_precio(total)}\n"
        f"{linea_entrega}\n"
        f"• Medio de pago: {medio_pago}\n"
        f"Horario de atención y despacho de pedidos: {HORARIO_ATENCION}.\n"
        "Un asesor del Centro de Biotecnología Agropecuaria se comunicará contigo para "
        "confirmar la entrega. ¿Deseas consultar otro producto?"
    )
    nuevo_estado = {"paso": "post_pedido"}
    return resumen, nuevo_estado, ["Sí", "No"]


def _pedir_medio_pago(estado, tipo_entrega, municipio_domicilio):
    """Guarda cómo se hará la entrega y pregunta el medio de pago antes de finalizar."""
    nuevo_estado = dict(estado)
    nuevo_estado["paso"] = "medio_pago"
    nuevo_estado["tipo_entrega"] = tipo_entrega
    nuevo_estado["municipio_domicilio"] = municipio_domicilio
    opciones_pago = _opciones_medios_pago()
    return (
        "¿Cómo prefieres pagar? Opciones: " + ", ".join(opciones_pago)
    ), nuevo_estado, opciones_pago


def _continuar_flujo_compra(texto_original, estado):
    paso = estado.get("paso")

    # Salida de emergencia: en cualquier paso del pedido, el cliente puede
    # cancelar y volver a una conversación normal (por ejemplo, si el
    # carrito quedó vacío tras quitar el único producto, o simplemente
    # cambió de opinión).
    texto_cancelar = _normalizar(texto_original).strip(" .!¡,")
    if texto_cancelar in ("cancelar", "cancelar pedido", "cancelar compra", "salir", "salir del pedido"):
        return (
            "Listo, cancelé el pedido en curso. ¿En qué más te puedo ayudar?"
        ), None, _opciones_categorias()

    if paso == "confirmar_compra":
        texto_normalizado = _normalizar(texto_original).replace(",", " ").strip(" .!¡")
        texto_normalizado = re.sub(r"\s+", " ", texto_normalizado).strip()

        if _es_si(texto_normalizado):
            conexion = db.conectar()
            fila = conexion.execute("SELECT * FROM productos WHERE id = ?", (estado["producto_id"],)).fetchone()
            conexion.close()
            if not fila or fila["stock"] <= 0:
                return "Lo siento, ese producto ya no está disponible. ¿Quieres ver otro?", None, _opciones_categorias()
            producto = dict(fila)
            nuevo_estado = {
                "paso": "cantidad", "producto_id": producto["id"], "producto_nombre": producto["nombre"],
                "items": estado.get("items", []),
            }
            return (
                f"¿Cuántas unidades de '{producto['nombre']}' quieres pedir? "
                f"(tenemos {producto['stock']} disponibles)"
            ), nuevo_estado, ["1", "2", "3", "5", "10"]

        if _es_no(texto_normalizado):
            return "Entendido, no inicio el pedido. ¿En qué más te puedo ayudar?", None, _opciones_categorias()

        # El mensaje no fue un sí/no claro: lo tratamos como una consulta
        # nueva de la tienda (por ejemplo, preguntó por otro producto).
        return responder_tienda(texto_original)

    if paso == "cantidad":
        match = re.search(r"\d+", texto_original)
        if not match:
            return "No entendí la cantidad. ¿Cuántas unidades quieres pedir? (escribe solo el número)", estado, ["1", "2", "3", "5", "10"]

        cantidad = int(match.group(0))
        conexion = db.conectar()
        fila = conexion.execute("SELECT * FROM productos WHERE id = ?", (estado["producto_id"],)).fetchone()
        conexion.close()

        if not fila:
            return "Hubo un problema encontrando ese producto, ¿quieres intentar de nuevo?", None, _opciones_categorias()
        producto = dict(fila)

        if cantidad <= 0:
            return "La cantidad debe ser mayor a cero. ¿Cuántas unidades quieres pedir?", estado, ["1", "2", "3", "5", "10"]
        if cantidad > producto["stock"]:
            return (
                f"Solo tenemos {producto['stock']} unidades disponibles de '{producto['nombre']}'. "
                "¿Qué cantidad (igual o menor) quieres pedir?"
            ), estado, None

        item = {
            "producto_id": producto["id"],
            "producto_nombre": producto["nombre"],
            "cantidad": cantidad,
            "precio_unitario": producto["precio"],
        }
        items = list(estado.get("items", []))
        items.append(item)
        nuevo_estado = {"paso": "carrito", "items": items}
        subtotal = cantidad * producto["precio"]
        return (
            f"Agregado al carrito: {cantidad} unidad(es) de '{producto['nombre']}' — "
            f"{db.formatear_precio(subtotal)}.\n"
            + _texto_carrito(items)
            + "\n¿Quieres agregar otro producto, quitar alguno del carrito, o finalizar el pedido?"
        ), nuevo_estado, ["Agregar otro producto", "Finalizar pedido"]

    if paso == "carrito":
        texto_normalizado = _normalizar(texto_original).replace(",", " ").strip(" .!¡")
        texto_normalizado = re.sub(r"\s+", " ", texto_normalizado).strip()
        items = estado.get("items", [])

        if "agregar" in texto_normalizado:
            nuevo_estado = {"paso": "carrito_agregar", "items": items}
            categorias = ", ".join(db.obtener_categorias())
            return (
                f"¿Qué otro producto quieres agregar al carrito? Categorías: {categorias}."
            ), nuevo_estado, _opciones_categorias()

        if "quitar" in texto_normalizado or "eliminar" in texto_normalizado or "remover" in texto_normalizado:
            # Buscamos la coincidencia SOLO entre los productos que ya están
            # en el carrito (no en todo el catálogo), para no confundir un
            # producto similar que el cliente no ha pedido (ej. "Huevo A" vs
            # "Huevo AAA").
            tokens_texto = _tokenizar(texto_normalizado)
            item_a_quitar = None
            mejor_coincidencias = 0
            for it in items:
                nombre_norm = _normalizar(it["producto_nombre"])
                palabras_nombre = [
                    w for w in re.findall(r"[a-z0-9]+", nombre_norm)
                    if len(w) >= 4 or re.fullmatch(r"a{2,3}", w)
                ]
                coincidencias = len([w for w in palabras_nombre if w in tokens_texto])
                if coincidencias > mejor_coincidencias:
                    mejor_coincidencias = coincidencias
                    item_a_quitar = it

            if item_a_quitar:
                nuevos_items = [it for it in items if it is not item_a_quitar]
                if not nuevos_items:
                    return (
                        f"Listo, quité '{item_a_quitar['producto_nombre']}'. Tu carrito quedó vacío. "
                        "¿Qué producto quieres agregar? (o escribe \"cancelar\" para salir del pedido)"
                    ), {"paso": "carrito_agregar", "items": []}, _opciones_categorias()
                nuevo_estado = {"paso": "carrito", "items": nuevos_items}
                return (
                    f"Listo, quité '{item_a_quitar['producto_nombre']}'.\n" + _texto_carrito(nuevos_items)
                    + "\n¿Algo más?"
                ), nuevo_estado, ["Agregar otro producto", "Finalizar pedido"]
            return (
                "No encontré ese producto en tu carrito.\n" + _texto_carrito(items)
                + "\nEscribe el nombre del producto (tal como aparece arriba) que quieres quitar."
            ), estado, ["Agregar otro producto", "Finalizar pedido"]

        if "finalizar" in texto_normalizado or _es_si(texto_normalizado):
            if not items:
                return (
                    "Tu carrito está vacío. ¿Qué producto quieres agregar?"
                ), {"paso": "carrito_agregar", "items": []}, _opciones_categorias()
            nuevo_estado = {"paso": "consentimiento", "items": items}
            return (
                _texto_carrito(items) + "\n" + AVISO_TRATAMIENTO_DATOS
            ), nuevo_estado, ["Sí, autorizo", "No autorizo"]

        return (
            _texto_carrito(items)
            + "\n¿Quieres agregar otro producto, quitar alguno, o finalizar el pedido?"
        ), estado, ["Agregar otro producto", "Finalizar pedido"]

    if paso == "carrito_agregar":
        texto_normalizado = _normalizar(texto_original)
        items = estado.get("items", [])

        producto = _buscar_producto_mencionado(texto_normalizado)
        if producto:
            if producto["stock"] <= 0:
                return (
                    f"'{producto['nombre']}' está agotado por ahora. ¿Qué otro producto quieres agregar?"
                ), estado, _opciones_categorias()
            nuevo_estado = {
                "paso": "cantidad", "producto_id": producto["id"], "producto_nombre": producto["nombre"],
                "items": items,
            }
            return (
                f"'{producto['nombre']}' cuesta {db.formatear_precio(producto['precio'])} y tenemos "
                f"{producto['stock']} unidades disponibles. ¿Cuántas unidades quieres agregar?"
            ), nuevo_estado, ["1", "2", "3", "5", "10"]

        categoria = _categoria_mencionada(texto_normalizado)
        if categoria:
            productos = db.buscar_por_categoria(categoria)
            respuesta, opciones = _listar_productos(productos, f"Esto tenemos en '{categoria}':")
            if respuesta:
                return respuesta, estado, opciones

        categorias = ", ".join(db.obtener_categorias())
        return (
            f"No encontré ese producto. Estas son nuestras categorías: {categorias}. "
            "¿Cuál te interesa?"
        ), estado, _opciones_categorias()

    if paso == "consentimiento":
        texto_normalizado = _normalizar(texto_original).replace(",", " ").strip(" .!¡")
        texto_normalizado = re.sub(r"\s+", " ", texto_normalizado).strip()

        if _es_si(texto_normalizado):
            nuevo_estado = dict(estado)
            nuevo_estado["paso"] = "nombre"
            return "Gracias por autorizarlo. ¿A nombre de quién registro el pedido?", nuevo_estado, None

        if _es_no(texto_normalizado):
            return (
                "Sin tu autorización no puedo registrar el pedido, ya que necesito guardar tu "
                "nombre y contacto para que un asesor te confirme la entrega. "
                "¿Quieres seguir viendo el catálogo de todas formas?"
            ), None, _opciones_categorias()

        return (
            "Para continuar necesito que confirmes si autorizas o no el tratamiento de tus datos "
            "personales (responde \"Sí, autorizo\" o \"No autorizo\")."
        ), estado, ["Sí, autorizo", "No autorizo"]

    if paso == "nombre":
        nombre_cliente = texto_original.strip()
        if not nombre_cliente:
            return "No entendí el nombre. ¿A nombre de quién registro el pedido?", estado, None
        nuevo_estado = dict(estado)
        nuevo_estado["paso"] = "contacto"
        nuevo_estado["cliente_nombre"] = nombre_cliente
        return (
            "Para que un asesor del Centro pueda confirmarte la entrega, escríbeme un número de "
            "teléfono/WhatsApp o un correo electrónico de contacto."
        ), nuevo_estado, None

    if paso == "contacto":
        contacto = texto_original.strip()
        tiene_digitos = bool(re.search(r"\d{7,}", contacto))
        parece_correo = "@" in contacto and "." in contacto
        if not (tiene_digitos or parece_correo):
            return (
                "Ese dato no parece un teléfono ni un correo válido. Escríbeme un número de "
                "teléfono/WhatsApp (mínimo 7 dígitos) o un correo electrónico."
            ), estado, None

        nuevo_estado = dict(estado)
        nuevo_estado["paso"] = "domicilio"
        nuevo_estado["cliente_contacto"] = contacto
        return (
            "¿Deseas domicilio o prefieres recoger tu pedido en el Centro de Biotecnología "
            "Agropecuaria? (Por ahora el domicilio solo está disponible en Mosquera y Funza, "
            "Cundinamarca)."
        ), nuevo_estado, ["Domicilio", "Recoger en el Centro"]

    if paso == "domicilio":
        texto_normalizado = _normalizar(texto_original).replace(",", " ").strip(" .!¡")
        texto_normalizado = re.sub(r"\s+", " ", texto_normalizado).strip()

        quiere_domicilio = "domicilio" in texto_normalizado or _es_si(texto_normalizado)
        no_quiere = (
            "recoger" in texto_normalizado or "recoge" in texto_normalizado
            or "centro" in texto_normalizado or _es_no(texto_normalizado)
        )

        if quiere_domicilio and not no_quiere:
            nuevo_estado = dict(estado)
            nuevo_estado["paso"] = "municipio"
            return (
                "¿En qué municipio recibirías el pedido? Por ahora solo cubrimos domicilios en "
                "Mosquera o Funza (Cundinamarca)."
            ), nuevo_estado, ["Mosquera", "Funza"]

        if no_quiere:
            return _pedir_medio_pago(estado, "Recoge en el Centro", None)

        return (
            "No entendí tu respuesta. ¿Quieres domicilio (solo Mosquera o Funza) o prefieres "
            "recoger tu pedido en el Centro?"
        ), estado, ["Domicilio", "Recoger en el Centro"]

    if paso == "municipio":
        municipio_normalizado = _normalizar(texto_original).strip(" .!¡,")

        if "mosquera" in municipio_normalizado:
            return _pedir_medio_pago(estado, "Domicilio", "Mosquera")
        if "funza" in municipio_normalizado:
            return _pedir_medio_pago(estado, "Domicilio", "Funza")
        if "recoger" in municipio_normalizado or "recoge" in municipio_normalizado or "centro" in municipio_normalizado:
            return _pedir_medio_pago(estado, "Recoge en el Centro", None)

        return (
            "Por ahora solo hacemos domicilios en Mosquera o Funza (Cundinamarca). "
            "¿Cuál de los dos, o prefieres recoger el pedido en el Centro?"
        ), estado, ["Mosquera", "Funza", "Recoger en el Centro"]

    if paso == "medio_pago":
        texto_normalizado = _normalizar(texto_original).replace(",", " ").strip(" .!¡")
        texto_normalizado = re.sub(r"\s+", " ", texto_normalizado).strip()
        opciones_pago = _opciones_medios_pago()

        medio = _detectar_medio_pago(texto_normalizado)
        if not medio:
            return (
                "No reconocí ese medio de pago. Elige una opción: " + ", ".join(opciones_pago)
            ), estado, opciones_pago

        return _finalizar_pedido(
            estado, estado.get("tipo_entrega"), estado.get("municipio_domicilio"), medio
        )

    if paso == "post_pedido":
        texto_normalizado = _normalizar(texto_original).replace(",", " ").strip(" .!¡")
        texto_normalizado = re.sub(r"\s+", " ", texto_normalizado).strip()

        if _es_si(texto_normalizado):
            return mensaje_bienvenida(), None, _opciones_categorias()

        if _es_no(texto_normalizado):
            return "¡Con gusto! Gracias por tu compra. Aquí estaré si necesitas algo más.", None, None

        # No fue un sí/no claro: en vez de perder el contexto (y caer en una
        # búsqueda genérica sin relación), lo tratamos como una nueva
        # consulta de la tienda, por ejemplo el nombre de otro producto.
        return responder_tienda(texto_original)

    if paso == "producto_agotado":
        texto_normalizado = _normalizar(texto_original).replace(",", " ").strip(" .!¡")
        texto_normalizado = re.sub(r"\s+", " ", texto_normalizado).strip()

        if _es_si(texto_normalizado):
            productos = db.buscar_por_categoria(estado["categoria"])
            respuesta, opciones = _listar_productos(productos, f"Esto tenemos en '{estado['categoria']}':")
            if not respuesta:
                return "No tengo más productos registrados en esa categoría.", None, _opciones_categorias()
            return respuesta, None, opciones

        if _es_no(texto_normalizado):
            return "Entendido, aquí estaré si necesitas algo más.", None, None

        # No fue un sí/no claro (por ejemplo, escribió el nombre de otra
        # categoría o producto): lo tratamos como una nueva consulta.
        return responder_tienda(texto_original)

    return "¿En qué más te puedo ayudar con la tienda?", None, _opciones_categorias()


def mensaje_bienvenida():
    """
    Mensaje inicial de Dahian: en vez de un saludo genérico, muestra de
    una vez el 'menú' con las categorías de la tienda, para que el
    usuario sepa desde el primer momento qué puede consultar.
    """
    categorias = db.obtener_categorias()
    partes = [
        "¡Hola! Soy Dahian, el asistente virtual de la tienda del "
        "Centro de Biotecnología Agropecuaria (SENA).",
        "",
        "Estas son nuestras categorías de productos:",
    ]
    partes.extend(f"• {c}" for c in categorias)
    partes.append("")
    partes.append("Toca una categoría, o escríbeme el producto que buscas.")
    partes.append(
        f"Horario de atención y despacho de pedidos: {HORARIO_ATENCION} "
        "Hacemos domicilios únicamente en Mosquera y Funza (Cundinamarca); en otros "
        "municipios puedes recoger tu pedido en el Centro."
    )
    partes.append(
        "Si registras un pedido, tus datos se tratan conforme a nuestra política de "
        "tratamiento de datos personales: /politica-privacidad"
    )
    return "\n".join(partes)


# ---------------------------------------------------------------------
# Función principal del módulo
# ---------------------------------------------------------------------

def responder_tienda(texto_original, pedido_en_curso=None):
    """
    Punto de entrada del asistente de tienda.

    Devuelve una tupla (respuesta_texto, nuevo_estado_pedido, opciones).
    'nuevo_estado_pedido' debe guardarse en la sesión del usuario
    (o None si no hay un pedido en curso) para poder continuar el
    flujo de compra en el siguiente mensaje. 'opciones' es una lista de
    textos cortos para mostrar como botones de respuesta rápida (o None
    si no aplica ningún botón para esa respuesta).
    """
    texto_normalizado = _normalizar(texto_original)

    # Si ya había un pedido en curso esperando cantidad o nombre, seguimos ahí.
    if pedido_en_curso:
        return _continuar_flujo_compra(texto_original, pedido_en_curso)

    # ¿El usuario quiere iniciar una compra?
    if any(frase in texto_normalizado for frase in PALABRAS_COMPRA):
        return _iniciar_flujo_compra(texto_normalizado)

    # ¿Pregunta por el catálogo completo o las categorías?
    if "catalogo" in texto_normalizado or ("que productos" in texto_normalizado) or ("categorias" in texto_normalizado):
        categorias = db.obtener_categorias()
        partes = ["Estas son las categorías disponibles en la tienda del Centro de Biotecnología Agropecuaria:"]
        partes.extend(f"• {c}" for c in categorias)
        partes.append("Toca una categoría para ver sus productos.")
        return "\n".join(partes), None, _opciones_categorias()

    # ¿Pregunta por el producto más barato / una oferta?
    if "mas barato" in texto_normalizado or "mas economico" in texto_normalizado or "mas barata" in texto_normalizado:
        categoria = _categoria_mencionada(texto_normalizado)
        base = db.buscar_por_categoria(categoria) if categoria else None
        producto = db.producto_mas_barato(base)
        if not producto:
            return "No encontré productos para comparar precios en este momento.", None, _opciones_categorias()
        return (
            f"El producto más económico disponible es '{producto['nombre']}' "
            f"a {db.formatear_precio(producto['precio'])} ({producto['descripcion']})."
        ), None, ["Iniciar pedido"] + _opciones_categorias()

    # ¿Pregunta con un tope de precio ("menos de 50000")?
    precio_maximo = _extraer_precio_maximo(texto_normalizado)
    if precio_maximo is not None:
        categoria = _categoria_mencionada(texto_normalizado)
        base = db.buscar_por_categoria(categoria) if categoria else db.obtener_todos()
        filtrados = db.filtrar_por_precio_maximo(precio_maximo, base)
        respuesta, opciones = _listar_productos(
            filtrados, f"Productos con precio menor o igual a {db.formatear_precio(precio_maximo)}:"
        )
        if not respuesta:
            return f"No encontré productos por {db.formatear_precio(precio_maximo)} o menos.", None, _opciones_categorias()
        return respuesta, None, opciones

    # ¿El mensaje nombra una categoría COMPLETA (ej. "semillas certificadas")?
    # Esto tiene prioridad sobre la coincidencia con un solo producto: si
    # preguntan por toda la categoría, deben ver la lista completa, no un
    # producto elegido al azar dentro de ella.
    categoria_exacta = _categoria_por_nombre_exacto(texto_normalizado)
    if categoria_exacta:
        productos = db.buscar_por_categoria(categoria_exacta)
        if "disponible" in texto_normalizado or "hay" in texto_normalizado.split():
            productos = db.productos_disponibles(productos)
        respuesta, opciones = _listar_productos(productos, f"Esto tenemos en '{categoria_exacta}':")
        if not respuesta:
            return f"No tengo productos registrados en la categoría '{categoria_exacta}' en este momento.", None, _opciones_categorias()
        return respuesta, None, opciones

    # ¿Menciona un producto específico por nombre (con al menos 2 palabras
    # coincidentes, para no confundirlo con una simple mención de categoría)?
    producto_encontrado = _buscar_producto_mencionado(texto_normalizado, minimo_coincidencias=2)
    if producto_encontrado:
        return _detalle_producto(producto_encontrado)

    # ¿Menciona una categoría conocida (por sinónimo)?
    categoria = _categoria_mencionada(texto_normalizado)
    if categoria:
        productos = db.buscar_por_categoria(categoria)
        if "disponible" in texto_normalizado or "hay" in texto_normalizado.split():
            productos = db.productos_disponibles(productos)
        respuesta, opciones = _listar_productos(productos, f"Esto tenemos en '{categoria}':")
        if not respuesta:
            return f"No tengo productos registrados en la categoría '{categoria}' en este momento.", None, _opciones_categorias()
        return respuesta, None, opciones

    # ¿Menciona un producto específico por nombre (coincidencia más suelta,
    # por si el mensaje solo repite una palabra del nombre)?
    producto_encontrado = _buscar_producto_mencionado(texto_normalizado)
    if producto_encontrado:
        return _detalle_producto(producto_encontrado)

    # Búsqueda genérica por texto libre contra nombre/descripción
    resultados = db.buscar_por_nombre(texto_normalizado)
    if resultados:
        respuesta, opciones = _listar_productos(resultados, "Esto encontré relacionado con tu búsqueda:")
        if respuesta:
            return respuesta, None, opciones

    categorias = ", ".join(db.obtener_categorias())
    return (
        "No encontré un producto exacto con esos datos en nuestra base de datos. "
        f"Estas son nuestras categorías disponibles: {categorias}. "
        "¿Sobre cuál te gustaría preguntar?"
    ), None, _opciones_categorias()
